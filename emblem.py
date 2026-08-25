"""
emblem.py
--------
Automatic emblem/logo detection and replacement for images embedded in DOCX.

The detector is intentionally multi-stage:
1. SIFT/ORB local-feature matching + homography for logos that move, scale or
   appear on different backgrounds.
2. Multi-scale template matching as a fallback for small/simple logos.
3. Conservative confidence checks to avoid painting over unrelated content.

The replacement is an overlay: the detected emblem region is covered by the
new emblem, scaled relative to the detected region.  The original image is
never modified in-place.
"""
import io
import math
import zipfile
from copy import deepcopy
from dataclasses import dataclass, asdict

import cv2
import numpy as np
from PIL import Image


@dataclass
class Detection:
    found: bool
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    confidence: float = 0.0
    method: str = ""
    angle: float = 0.0
    reason: str = ""

    def to_dict(self):
        return asdict(self)


def _pil_to_bgr(data_or_image):
    if isinstance(data_or_image, Image.Image):
        im = data_or_image.convert("RGB")
    elif isinstance(data_or_image, (str, bytes, bytearray)):
        if isinstance(data_or_image, str):
            im = Image.open(data_or_image).convert("RGB")
        else:
            im = Image.open(io.BytesIO(data_or_image)).convert("RGB")
    else:
        raise TypeError("Unsupported image source")
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


def _trim_template(tpl, pad=2):
    """Trim near-white margins around a logo screenshot/template."""
    gray = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
    # Keep colored/dark pixels.  A slightly relaxed threshold handles white
    # screenshots and light anti-aliased edges.
    mask = gray < 246
    ys, xs = np.where(mask)
    if len(xs) < 20 or len(ys) < 20:
        return tpl
    x1, x2 = max(0, xs.min() - pad), min(tpl.shape[1], xs.max() + pad + 1)
    y1, y2 = max(0, ys.min() - pad), min(tpl.shape[0], ys.max() + pad + 1)
    cropped = tpl[y1:y2, x1:x2]
    # Do not over-trim if the result is implausibly tiny.
    if cropped.shape[0] < 12 or cropped.shape[1] < 12:
        return tpl
    return cropped


def _resize_max(img, max_side=1400):
    h, w = img.shape[:2]
    if max(h, w) <= max_side:
        return img, 1.0
    scale = max_side / float(max(h, w))
    out = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    return out, scale


def _feature_detector():
    # SIFT is available in current OpenCV builds and is much more robust than
    # plain template matching when the logo moves to another location.
    if hasattr(cv2, "SIFT_create"):
        return cv2.SIFT_create(nfeatures=700), "SIFT"
    return cv2.ORB_create(nfeatures=1000, fastThreshold=8), "ORB"


def _feature_match(template, target):
    detector, name = _feature_detector()
    tg, _ = _resize_max(target, 1600)
    tp, _ = _resize_max(template, 500)
    # Upscale tiny templates so feature extraction has enough pixels.
    if max(tp.shape[:2]) < 180:
        s = 180 / float(max(tp.shape[:2]))
        tp = cv2.resize(tp, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)

    g1 = cv2.cvtColor(tp, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(tg, cv2.COLOR_BGR2GRAY)
    kp1, des1 = detector.detectAndCompute(g1, None)
    kp2, des2 = detector.detectAndCompute(g2, None)
    if des1 is None or des2 is None or len(kp1) < 5 or len(kp2) < 5:
        return None

    if name == "SIFT":
        matcher = cv2.BFMatcher(cv2.NORM_L2)
        ratio = 0.74
    else:
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        ratio = 0.78
    pairs = matcher.knnMatch(des1, des2, k=2)
    good = [m for m, n in pairs if m.distance < ratio * n.distance]
    if len(good) < 6:
        return None

    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if H is None or mask is None:
        return None
    inliers = int(mask.ravel().sum())
    if inliers < 5:
        return None

    h, w = tp.shape[:2]
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    projected = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
    if not np.isfinite(projected).all():
        return None
    x1, y1 = projected.min(axis=0)
    x2, y2 = projected.max(axis=0)
    tw, th = target.shape[1], target.shape[0]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(tw - 1, x2), min(th - 1, y2)
    bw, bh = x2 - x1, y2 - y1
    if bw < 8 or bh < 8:
        return None
    area_ratio = (bw * bh) / float(tw * th)
    if area_ratio > 0.20:  # a logo should not cover a fifth of a page screenshot
        return None
    # Inlier ratio plus absolute inlier count; both are required.
    inlier_ratio = inliers / max(1, len(good))
    confidence = min(1.0, 0.55 * inlier_ratio + 0.45 * min(1.0, inliers / 18.0))
    if confidence < 0.38:
        return None
    return Detection(True, int(x1), int(y1), int(bw), int(bh), float(confidence), name, 0.0, f"{inliers}/{len(good)} inliers")


def _template_match(template, target):
    """Multi-scale normalized template matching fallback."""
    tpl = template
    gray_t = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY)
    gray_tpl = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
    th0, tw0 = gray_tpl.shape[:2]
    if th0 < 12 or tw0 < 12:
        return None

    best = None
    # Broad scale range supports screenshots where the emblem is much larger
    # or smaller than the supplied sample.
    for scale in np.linspace(0.35, 2.4, 26):
        w = int(tw0 * scale)
        h = int(th0 * scale)
        if w < 12 or h < 12 or w >= gray_t.shape[1] or h >= gray_t.shape[0]:
            continue
        t = cv2.resize(gray_tpl, (w, h), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        res = cv2.matchTemplate(gray_t, t, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if best is None or score > best[0]:
            best = (float(score), loc[0], loc[1], w, h)
    if not best or best[0] < 0.68:
        return None
    score, x, y, w, h = best
    return Detection(True, int(x), int(y), int(w), int(h), float(max(0, min(1, score))), "Template", 0.0, f"score={score:.3f}")


def detect_emblem(template_bytes, image_bytes, min_confidence=0.55):
    try:
        tpl = _trim_template(_pil_to_bgr(template_bytes))
        img = _pil_to_bgr(image_bytes)
        candidates = []
        d = _feature_match(tpl, img)
        if d:
            candidates.append(d)
        d2 = _template_match(tpl, img)
        if d2:
            candidates.append(d2)
        if not candidates:
            return Detection(False, reason="Emblema topilmadi yoki namuna juda kichik/xira.")
        # Prefer feature detection when it is strong; otherwise use the best score.
        candidates.sort(key=lambda x: (x.method == "SIFT", x.confidence), reverse=True)
        best = candidates[0]
        if best.confidence < min_confidence:
            return Detection(False, reason=f"Ishonch past: {best.confidence:.0%}")
        return best
    except Exception as exc:
        return Detection(False, reason=str(exc))


def _prepare_overlay(replacement_bytes, target_w, target_h, opacity=100):
    im = Image.open(replacement_bytes if isinstance(replacement_bytes, str) else io.BytesIO(replacement_bytes)).convert("RGBA")
    # Fit inside the requested box while preserving aspect ratio.
    im.thumbnail((max(1, target_w), max(1, target_h)), Image.Resampling.LANCZOS)
    if opacity < 100:
        alpha = im.getchannel("A").point(lambda a: int(a * opacity / 100))
        im.putalpha(alpha)
    return im


def replace_emblem_on_image(image_bytes, template_bytes, replacement_bytes, scale_percent=8, opacity=100, min_confidence=0.55):
    src = Image.open(image_bytes if isinstance(image_bytes, str) else io.BytesIO(image_bytes)).convert("RGBA")
    det = detect_emblem(template_bytes, image_bytes, min_confidence=min_confidence)
    if not det.found:
        return image_bytes, det

    # Make the replacement a little larger than the detected old emblem.
    factor = max(1.0, 1.0 + float(scale_percent) / 100.0)
    box_w = max(4, int(det.w * factor))
    box_h = max(4, int(det.h * factor))
    overlay = _prepare_overlay(replacement_bytes, box_w, box_h, opacity)

    # Center on the detected emblem.  This remains stable when emblems move
    # around the image.
    cx = det.x + det.w / 2.0
    cy = det.y + det.h / 2.0
    x = int(round(cx - overlay.width / 2))
    y = int(round(cy - overlay.height / 2))
    # Keep the new emblem inside the image.
    x = max(0, min(src.width - overlay.width, x))
    y = max(0, min(src.height - overlay.height, y))
    src.alpha_composite(overlay, (x, y))

    out = io.BytesIO()
    fmt = Image.open(image_bytes if isinstance(image_bytes, str) else io.BytesIO(image_bytes)).format or "PNG"
    if fmt.upper() in ("JPEG", "JPG"):
        src.convert("RGB").save(out, format="JPEG", quality=95, subsampling=0)
    elif fmt.upper() == "WEBP":
        src.save(out, format="WEBP", quality=95, method=4)
    else:
        src.save(out, format="PNG")
    return out.getvalue(), det


def preview_emblem(image_bytes, template_bytes, replacement_bytes, scale_percent=8, opacity=100, min_confidence=0.55):
    """Return preview PNG and detection metadata."""
    out, det = replace_emblem_on_image(image_bytes, template_bytes, replacement_bytes, scale_percent, opacity, min_confidence)
    if det.found:
        im = Image.open(io.BytesIO(out)).convert("RGB")
        im.thumbnail((1100, 800), Image.Resampling.LANCZOS)
        buf = io.BytesIO(); im.save(buf, format="PNG")
        return buf.getvalue(), det
    return None, det


def annotate_detection(image_bytes, det, color=(0, 180, 80), width=4):
    """Draw a detection rectangle and confidence label for UI preview."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if det.found:
        cv = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        cv2.rectangle(cv, (det.x, det.y), (det.x + det.w, det.y + det.h), color, width)
        label = f"Emblema {det.confidence:.0%}"
        cv2.putText(cv, label, (det.x, max(20, det.y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
        img = Image.fromarray(cv2.cvtColor(cv, cv2.COLOR_BGR2RGB))
    out = io.BytesIO(); img.save(out, format="PNG")
    return out.getvalue()


def replace_emblems_in_docx(input_path, output_path, template_bytes, replacement_bytes,
                            scale_percent=8, opacity=100, min_confidence=0.55):
    """Replace every detected emblem in every readable DOCX media image.

    A failed/low-confidence detection only affects that one image.  CRC-bad
    media is read with the tolerant ZIP reader used by the watermark engine.
    """
    from watermark import _read_zip_entry_tolerant, SUPPORTED_EXTENSIONS

    report = {
        "total_media": 0, "found": 0, "not_found": 0, "repaired": 0,
        "failed": 0, "changed": 0, "details": []
    }
    with zipfile.ZipFile(input_path, "r") as zin, zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            lower = item.filename.lower()
            ext = lower.rsplit(".", 1)[-1] if "." in lower else ""
            is_media = lower.startswith("word/media/") and ("." + ext) in SUPPORTED_EXTENSIONS
            try:
                data, tolerant = _read_zip_entry_tolerant(zin, item)
            except Exception as exc:
                if is_media:
                    report["failed"] += 1
                    report["details"].append({"name": item.filename, "status": "failed", "reason": str(exc)})
                    continue
                raise

            if is_media:
                report["total_media"] += 1
                if tolerant:
                    report["repaired"] += 1
                try:
                    replaced, det = replace_emblem_on_image(
                        data, template_bytes, replacement_bytes,
                        scale_percent=scale_percent, opacity=opacity,
                        min_confidence=min_confidence,
                    )
                    if det.found:
                        data = replaced
                        report["found"] += 1
                        report["changed"] += 1
                        report["details"].append({
                            "name": item.filename, "status": "replaced",
                            "confidence": round(det.confidence, 4),
                            "method": det.method,
                            "bbox": [det.x, det.y, det.w, det.h],
                            "crc_bad": bool(tolerant),
                        })
                    else:
                        report["not_found"] += 1
                        report["details"].append({
                            "name": item.filename, "status": "not_found",
                            "reason": det.reason, "crc_bad": bool(tolerant),
                        })
                except Exception as exc:
                    report["failed"] += 1
                    report["details"].append({"name": item.filename, "status": "failed", "reason": str(exc)})

            write_info = deepcopy(item)
            if tolerant or is_media:
                write_info.CRC = None
                write_info.file_size = len(data)
                write_info.compress_size = 0
            zout.writestr(write_info, data)
    return report
