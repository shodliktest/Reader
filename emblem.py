"""Professional emblem replacement engine.

Features:
- SIFT/ORB + homography detection.
- Grayscale and edge based multi-scale template fallback (works better on white backgrounds).
- Manual normalized placement for images where the old emblem is absent.
- Per-image automatic placement OR one reference placement applied to every image.
- Independent size/opacity controls.
- DOCX media processing tolerant of CRC errors.
"""
import io
import os
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
    elif isinstance(data_or_image, str):
        im = Image.open(data_or_image).convert("RGB")
    elif isinstance(data_or_image, (bytes, bytearray)):
        im = Image.open(io.BytesIO(data_or_image)).convert("RGB")
    else:
        raise TypeError("Unsupported image source")
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


def _trim_template(tpl, pad=2):
    """Remove only obvious white screenshot margins, never all-white logos."""
    if tpl.size == 0:
        return tpl
    gray = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
    # Use distance from white plus edges. This is intentionally conservative.
    mask = gray < 250
    edge = cv2.Canny(gray, 30, 100)
    mask = cv2.bitwise_or(mask.astype(np.uint8), (edge > 0).astype(np.uint8))
    ys, xs = np.where(mask > 0)
    if len(xs) < 20:
        return tpl
    x1, x2 = max(0, int(xs.min()) - pad), min(tpl.shape[1], int(xs.max()) + pad + 1)
    y1, y2 = max(0, int(ys.min()) - pad), min(tpl.shape[0], int(ys.max()) + pad + 1)
    cropped = tpl[y1:y2, x1:x2]
    if cropped.shape[0] < 12 or cropped.shape[1] < 12:
        return tpl
    return cropped


def _resize_max(img, max_side=1600):
    h, w = img.shape[:2]
    if max(h, w) <= max_side:
        return img, 1.0
    scale = max_side / float(max(h, w))
    out = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    return out, scale


def _feature_detector():
    if hasattr(cv2, "SIFT_create"):
        return cv2.SIFT_create(nfeatures=1200, contrastThreshold=0.025), "SIFT"
    return cv2.ORB_create(nfeatures=1600, fastThreshold=6), "ORB"


def _feature_match(template, target):
    detector, name = _feature_detector()
    tg, tg_scale = _resize_max(target, 1800)
    tp, tp_scale = _resize_max(template, 700)
    if max(tp.shape[:2]) < 220:
        s = 220.0 / max(tp.shape[:2])
        tp = cv2.resize(tp, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
        tp_scale /= s

    g1 = cv2.cvtColor(tp, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(tg, cv2.COLOR_BGR2GRAY)
    kp1, des1 = detector.detectAndCompute(g1, None)
    kp2, des2 = detector.detectAndCompute(g2, None)
    if des1 is None or des2 is None or len(kp1) < 6 or len(kp2) < 8:
        return None

    norm = cv2.NORM_L2 if name == "SIFT" else cv2.NORM_HAMMING
    matcher = cv2.BFMatcher(norm)
    ratio = 0.76 if name == "SIFT" else 0.80
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

    # Convert from resized target coordinates back to original image coordinates.
    projected /= tg_scale
    x1, y1 = projected.min(axis=0)
    x2, y2 = projected.max(axis=0)
    tw, th = target.shape[1], target.shape[0]
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(tw - 1.0, x2), min(th - 1.0, y2)
    bw, bh = x2 - x1, y2 - y1
    if bw < 8 or bh < 8:
        return None
    area_ratio = (bw * bh) / float(max(1, tw * th))
    if area_ratio > 0.30:
        return None

    inlier_ratio = inliers / max(1, len(good))
    confidence = min(1.0, 0.55 * inlier_ratio + 0.45 * min(1.0, inliers / 20.0))
    if confidence < 0.34:
        return None
    return Detection(True, round(x1), round(y1), round(bw), round(bh), float(confidence), name, 0.0,
                     f"{inliers}/{len(good)} inliers")


def _template_match_gray(template, target):
    gray_t = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY)
    gray_tpl = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    th0, tw0 = gray_tpl.shape[:2]
    if min(th0, tw0) < 10:
        return None
    best = None
    # Wider scale range because screenshots and Word exports can differ a lot.
    for scale in np.linspace(0.20, 3.20, 37):
        w, h = round(tw0 * scale), round(th0 * scale)
        if w < 10 or h < 10 or w >= gray_t.shape[1] or h >= gray_t.shape[0]:
            continue
        t = cv2.resize(gray_tpl, (w, h), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        res = cv2.matchTemplate(gray_t, t, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if best is None or score > best[0]:
            best = (float(score), loc[0], loc[1], w, h)
    if not best or best[0] < 0.64:
        return None
    score, x, y, w, h = best
    return Detection(True, x, y, w, h, max(0.0, min(1.0, score)), "Template-gray", 0.0, f"score={score:.3f}")


def _template_match_edges(template, target):
    """Edge matching is particularly useful when the sample has a white background."""
    tg = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY)
    tp = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    tg = cv2.GaussianBlur(tg, (3, 3), 0)
    tp = cv2.GaussianBlur(tp, (3, 3), 0)
    et = cv2.Canny(tg, 40, 120)
    ep0 = cv2.Canny(tp, 40, 120)
    th0, tw0 = ep0.shape[:2]
    if np.count_nonzero(ep0) < 8:
        return None
    best = None
    for scale in np.linspace(0.25, 3.0, 34):
        w, h = round(tw0 * scale), round(th0 * scale)
        if w < 10 or h < 10 or w >= et.shape[1] or h >= et.shape[0]:
            continue
        ep = cv2.resize(ep0, (w, h), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(et, ep, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if best is None or score > best[0]:
            best = (float(score), loc[0], loc[1], w, h)
    if not best or best[0] < 0.58:
        return None
    score, x, y, w, h = best
    return Detection(True, x, y, w, h, max(0.0, min(1.0, score * 0.95)), "Template-edge", 0.0, f"edge={score:.3f}")


def detect_emblem(template_bytes, image_bytes, min_confidence=0.55):
    try:
        tpl = _trim_template(_pil_to_bgr(template_bytes))
        img = _pil_to_bgr(image_bytes)
        candidates = []
        for fn in (_feature_match, _template_match_gray, _template_match_edges):
            try:
                d = fn(tpl, img)
                if d:
                    candidates.append(d)
            except Exception:
                continue
        if not candidates:
            return Detection(False, reason="Emblema topilmadi. Oqart fonli/rasm sifati past bo'lsa qo'lda joylashtiring.")

        # Feature match wins when it is genuinely strong; otherwise the highest confidence wins.
        candidates.sort(key=lambda d: (d.method in ("SIFT", "ORB") and d.confidence >= 0.52, d.confidence), reverse=True)
        best = candidates[0]
        if best.confidence < min_confidence:
            return Detection(False, reason=f"Ishonch past: {best.confidence:.0%} ({best.method})")
        return best
    except Exception as exc:
        return Detection(False, reason=f"Aniqlash xatosi: {exc}")


def _load_rgba(data):
    if isinstance(data, str):
        return Image.open(data).convert("RGBA")
    return Image.open(io.BytesIO(data)).convert("RGBA")


def _apply_opacity(im, opacity):
    opacity = max(0, min(100, float(opacity)))
    if opacity >= 100:
        return im
    alpha = im.getchannel("A").point(lambda a: round(a * opacity / 100.0))
    out = im.copy()
    out.putalpha(alpha)
    return out


def _fit_overlay(replacement_bytes, box_w, box_h, opacity=100):
    im = _load_rgba(replacement_bytes)
    im.thumbnail((max(1, box_w), max(1, box_h)), Image.Resampling.LANCZOS)
    return _apply_opacity(im, opacity)


def _save_like_source(src, original_bytes):
    out = io.BytesIO()
    fmt = (getattr(src, "format", None) or Image.open(io.BytesIO(original_bytes)).format or "PNG").upper()
    if fmt in ("JPEG", "JPG"):
        src.convert("RGB").save(out, format="JPEG", quality=95, optimize=False, subsampling=0)
    elif fmt == "WEBP":
        src.save(out, format="WEBP", quality=95, method=4)
    else:
        src.save(out, format="PNG", optimize=False)
    return out.getvalue()


def replace_emblem_at_box(image_bytes, replacement_bytes, x, y, w, h, scale_percent=8, opacity=100):
    """Place the new emblem into an arbitrary box. Coordinates are original pixels."""
    src = _load_rgba(image_bytes)
    base_w, base_h = src.size
    w = max(2, int(round(w)))
    h = max(2, int(round(h)))
    factor = max(0.05, 1.0 + float(scale_percent) / 100.0)
    ow, oh = max(2, round(w * factor)), max(2, round(h * factor))
    overlay = _fit_overlay(replacement_bytes, ow, oh, opacity)
    cx, cy = float(x) + w / 2.0, float(y) + h / 2.0
    px = round(cx - overlay.width / 2.0)
    py = round(cy - overlay.height / 2.0)
    # Crop overlay rather than moving it when it touches an image edge.
    ox0, oy0 = max(0, -px), max(0, -py)
    ox1, oy1 = min(overlay.width, base_w - px), min(overlay.height, base_h - py)
    if ox1 <= ox0 or oy1 <= oy0:
        return image_bytes
    clipped = overlay.crop((ox0, oy0, ox1, oy1))
    src.alpha_composite(clipped, (max(0, px), max(0, py)))
    return _save_like_source(src, image_bytes)


def normalized_placement_from_box(box, image_size):
    """Convert a pixel box into portable 0..1 placement values."""
    x, y, w, h = [float(box[k]) for k in ("left", "top", "width", "height")]
    iw, ih = image_size
    return {
        "cx": max(0.0, min(1.0, (x + w / 2) / max(1, iw))),
        "cy": max(0.0, min(1.0, (y + h / 2) / max(1, ih))),
        "w": max(0.002, min(1.0, w / max(1, iw))),
        "h": max(0.002, min(1.0, h / max(1, ih))),
    }


def box_from_normalized(placement, image_size, scale_percent=0):
    iw, ih = image_size
    p = placement
    w = max(2, round(iw * float(p.get("w", 0.1))))
    h = max(2, round(ih * float(p.get("h", 0.1))))
    factor = max(0.05, 1.0 + float(scale_percent) / 100.0)
    w = max(2, round(w * factor))
    h = max(2, round(h * factor))
    cx = float(p.get("cx", 0.9)) * iw
    cy = float(p.get("cy", 0.9)) * ih
    return {
        "x": round(cx - w / 2), "y": round(cy - h / 2), "w": w, "h": h
    }


def replace_emblem_on_image(image_bytes, template_bytes, replacement_bytes, scale_percent=8, opacity=100,
                            min_confidence=0.55, placement=None):
    """Replace automatically, or use an explicit normalized placement if supplied."""
    if placement:
        src = _load_rgba(image_bytes)
        box = box_from_normalized(placement, src.size, scale_percent=scale_percent)
        out = replace_emblem_at_box(image_bytes, replacement_bytes, **box, scale_percent=0, opacity=opacity)
        det = Detection(True, box["x"], box["y"], box["w"], box["h"], 1.0, "Manual", 0.0,
                        "Qo'lda saqlangan joylashuv")
        return out, det

    det = detect_emblem(template_bytes, image_bytes, min_confidence=min_confidence)
    if not det.found:
        return image_bytes, det
    out = replace_emblem_at_box(image_bytes, replacement_bytes, det.x, det.y, det.w, det.h,
                                scale_percent=scale_percent, opacity=opacity)
    return out, det


def preview_emblem(image_bytes, template_bytes, replacement_bytes, scale_percent=8, opacity=100,
                   min_confidence=0.55, placement=None):
    out, det = replace_emblem_on_image(image_bytes, template_bytes, replacement_bytes,
                                       scale_percent, opacity, min_confidence, placement)
    im = Image.open(io.BytesIO(out)).convert("RGB")
    im.thumbnail((1100, 850), Image.Resampling.LANCZOS)
    buf = io.BytesIO(); im.save(buf, format="PNG")
    return buf.getvalue(), det


def annotate_detection(image_bytes, det, color=(0, 180, 80), width=4):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if det.found:
        cv = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        cv2.rectangle(cv, (det.x, det.y), (det.x + det.w, det.y + det.h), color, width)
        label = f"{det.method} {det.confidence:.0%}"
        cv2.putText(cv, label, (det.x, max(22, det.y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
        img = Image.fromarray(cv2.cvtColor(cv, cv2.COLOR_BGR2RGB))
    out = io.BytesIO(); img.save(out, format="PNG")
    return out.getvalue()


def replace_emblems_in_docx(input_path, output_path, template_bytes=None, replacement_bytes=None,
                            scale_percent=8, opacity=100, min_confidence=0.55,
                            placement=None, placement_mode="auto_then_manual"):
    """Replace emblems in every DOCX media image.

    placement_mode:
      auto_then_manual: detect per image; if detection fails use normalized placement.
      manual_all: use normalized placement for every image.
      auto_only: never use fallback placement.
    """
    from watermark import _read_zip_entry_tolerant, SUPPORTED_EXTENSIONS

    report = {"total_media": 0, "found": 0, "manual": 0, "not_found": 0,
              "repaired": 0, "failed": 0, "changed": 0, "details": []}

    with zipfile.ZipFile(input_path, "r") as zin, zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            lower = item.filename.lower()
            ext = os.path.splitext(lower)[1]
            is_media = lower.startswith("word/media/") and ext in SUPPORTED_EXTENSIONS
            try:
                data, tolerant = _read_zip_entry_tolerant(zin, item)
            except Exception as exc:
                if is_media:
                    report["failed"] += 1
                    report["details"].append({"name": item.filename, "status": "failed", "reason": str(exc)})
                    # Do not abort other images.
                    continue
                raise

            if is_media:
                report["total_media"] += 1
                if tolerant:
                    report["repaired"] += 1
                try:
                    det = None
                    if placement_mode != "manual_all" and template_bytes:
                        det = detect_emblem(template_bytes, data, min_confidence=min_confidence)
                    if det and det.found:
                        data = replace_emblem_at_box(data, replacement_bytes, det.x, det.y, det.w, det.h,
                                                     scale_percent=scale_percent, opacity=opacity)
                        report["found"] += 1
                        report["changed"] += 1
                        report["details"].append({"name": item.filename, "status": "replaced", "method": det.method,
                                                   "confidence": round(det.confidence, 4), "bbox": [det.x, det.y, det.w, det.h],
                                                   "crc_bad": bool(tolerant)})
                    elif placement and placement_mode in ("auto_then_manual", "manual_all"):
                        box = box_from_normalized(placement, Image.open(io.BytesIO(data)).size, scale_percent=scale_percent)
                        data = replace_emblem_at_box(data, replacement_bytes, **box, scale_percent=0, opacity=opacity)
                        report["manual"] += 1
                        report["changed"] += 1
                        report["details"].append({"name": item.filename, "status": "manual", "bbox": [box["x"], box["y"], box["w"], box["h"]],
                                                   "crc_bad": bool(tolerant)})
                    else:
                        report["not_found"] += 1
                        report["details"].append({"name": item.filename, "status": "not_found",
                                                   "reason": getattr(det, "reason", "Emblema topilmadi"),
                                                   "crc_bad": bool(tolerant)})
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
