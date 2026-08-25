"""
Pro emblem/logo detection and replacement for images embedded in DOCX.

V8 goals:
- The user may provide ONLY the emblem, OR the emblem + Telegram mark as one crop.
  Whatever is selected is what the detector replaces.
- Transparent PNGs are supported without converting transparent pixels into black.
- Crops made with a black/white background are automatically trimmed to the useful content.
- Detection uses masked multi-scale template matching first (best for tiny logos),
  then SIFT/ORB as a secondary method.
- Replacement first removes the detected old block with inpainting, then places the
  new emblem over exactly that block. This prevents the old Telegram mark from being
  left behind when the selected template contains both elements.
- A single bad/CRC-broken DOCX media entry never stops the other images.
"""
import io
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


def _open_pil(data_or_image):
    if isinstance(data_or_image, Image.Image):
        return data_or_image.copy()
    if isinstance(data_or_image, str):
        return Image.open(data_or_image)
    if isinstance(data_or_image, (bytes, bytearray)):
        return Image.open(io.BytesIO(data_or_image))
    raise TypeError("Unsupported image source")


def _pil_to_bgr(data_or_image):
    im = _open_pil(data_or_image).convert("RGB")
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


def _trim_template_pil(data_or_image, pad=3):
    """Trim transparent/black/white margins from a manually selected sample.

    This is intentionally conservative: it removes large uniform margins but
    keeps the actual white portions of a logo when the border is non-white.
    """
    im = _open_pil(data_or_image).convert("RGBA")
    arr = np.asarray(im)
    rgb = arr[:, :, :3].astype(np.int16)
    alpha = arr[:, :, 3]

    # Transparent pixels are never part of the sample.
    if np.any(alpha < 250):
        mask = alpha > 20
    else:
        border = np.concatenate([
            rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :]
        ], axis=0)
        bg = np.median(border, axis=0)
        dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
        # Also preserve saturated/bright content when the border is black or white.
        hsv = cv2.cvtColor(arr[:, :, :3].astype(np.uint8), cv2.COLOR_RGB2HSV)
        sat = hsv[:, :, 1]
        val = hsv[:, :, 2]
        mask = (dist > 18) | (sat > 35)
        # Black-background eraser exports: any non-black pixel is foreground.
        if np.mean(border) < 35:
            mask = val > 18
        # White-background screenshots: keep dark or saturated logo pixels.
        elif np.mean(border) > 220:
            mask = (val < 235) | (sat > 30)

    # Morphological cleanup so tiny antialiasing islands don't expand the crop.
    m = (mask.astype(np.uint8) * 255)
    kernel = np.ones((3, 3), np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel)
    ys, xs = np.where(m > 0)
    if len(xs) < 20 or len(ys) < 20:
        return im, np.ones((im.height, im.width), np.uint8) * 255

    x1, x2 = max(0, int(xs.min()) - pad), min(im.width, int(xs.max()) + pad + 1)
    y1, y2 = max(0, int(ys.min()) - pad), min(im.height, int(ys.max()) + pad + 1)
    if x2 - x1 < 12 or y2 - y1 < 12:
        return im, m
    return im.crop((x1, y1, x2, y2)), m[y1:y2, x1:x2]


def _template_and_mask(data_or_image):
    cropped, mask = _trim_template_pil(data_or_image)
    bgr = cv2.cvtColor(np.asarray(cropped.convert("RGB")), cv2.COLOR_RGB2BGR)
    # Recompute mask for the cropped image if the original had transparency.
    arr = np.asarray(cropped)
    if arr.shape[2] == 4 and np.any(arr[:, :, 3] < 250):
        mask = (arr[:, :, 3] > 20).astype(np.uint8) * 255
    else:
        # Use the saved crop mask when available; if it became all-white, infer
        # foreground from its border/background again.
        if mask.shape[:2] != bgr.shape[:2]:
            mask = np.ones(bgr.shape[:2], np.uint8) * 255
    mask = cv2.GaussianBlur(mask, (3, 3), 0)
    mask = np.where(mask > 32, 255, 0).astype(np.uint8)
    return bgr, mask


def _resize_max(img, max_side=1100):
    h, w = img.shape[:2]
    if max(h, w) <= max_side:
        return img, 1.0
    scale = max_side / float(max(h, w))
    out = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    return out, scale


def _masked_template_match(template, mask, target):
    """Fast, robust multi-scale match for tiny logos.

    The mask is critical: black/white margins from an uploaded sample do not
    dominate the score. Search is performed on a downscaled target, then the
    bounding box is mapped back to original pixels.
    """
    tg, target_scale = _resize_max(target, 800)
    th, tw = tg.shape[:2]
    h0, w0 = template.shape[:2]
    if h0 < 10 or w0 < 10:
        return None

    # A watermark is normally 4%..45% of the image width. Include larger sizes
    # too for full-block selections, while preventing page-sized false matches.
    scales = np.linspace(0.065, 0.60, 22)
    best = None
    gray_target = cv2.cvtColor(tg, cv2.COLOR_BGR2GRAY)
    gray_tpl = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

    for scale in scales:
        w = int(w0 * scale)
        h = int(h0 * scale)
        if w < 10 or h < 10 or w >= tw or h >= th:
            continue
        resized_tpl = cv2.resize(template, (w, h), interpolation=cv2.INTER_AREA)
        m = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
        if int((m > 32).sum()) < 35:
            continue
        r = cv2.matchTemplate(tg, resized_tpl, cv2.TM_CCORR_NORMED, mask=m)
        _, score, _, loc = cv2.minMaxLoc(r)
        if best is None or score > best[0]:
            best = (float(score), loc[0], loc[1], w, h, target_scale)

    if not best:
        return None
    s1, x, y, w, h, target_scale = best
    # Reject weak matches. The masked score remains high even with JPEG/Telegram
    # compression because the background margins are excluded.
    if s1 < 0.70:
        return None

    # Secondary local grayscale check at the selected location. This is cheap
    # and prevents a saturated blue rectangle from winning on color alone.
    resized_gray = cv2.resize(gray_tpl, (w, h), interpolation=cv2.INTER_AREA)
    patch = gray_target[y:y+h, x:x+w]
    rg = float(cv2.matchTemplate(patch, resized_gray, cv2.TM_CCOEFF_NORMED)[0,0]) if patch.shape == resized_gray.shape else 0.0
    rs = 0.0
    combined = 0.78 * float(s1) + 0.22 * max(0.0, rg)
    if combined < 0.62:
        return None

    inv = 1.0 / target_scale
    x0 = int(round(x * inv)); y0 = int(round(y * inv))
    bw = int(round(w * inv)); bh = int(round(h * inv))
    tw0, th0 = target.shape[1], target.shape[0]
    x0 = max(0, min(tw0 - 1, x0)); y0 = max(0, min(th0 - 1, y0))
    bw = max(8, min(tw0 - x0, bw)); bh = max(8, min(th0 - y0, bh))

    area_ratio = (bw * bh) / float(max(1, tw0 * th0))
    if area_ratio > 0.15:
        return None

    # Confidence is based mostly on the masked match, with JPEG tolerance.
    confidence = max(0.0, min(1.0, 0.70 * s1 + 0.20 * max(0.0, rg) + 0.10 * max(0.0, rs)))
    return Detection(True, x0, y0, bw, bh, confidence, "MaskedTemplate", 0.0,
                     f"masked={s1:.3f}, gray={rg:.3f}, sat={rs:.3f}")


def _feature_match(template, target):
    """Secondary SIFT/ORB detector for cases where template matching struggles."""
    detector = cv2.SIFT_create(nfeatures=900) if hasattr(cv2, "SIFT_create") else cv2.ORB_create(nfeatures=1200, fastThreshold=8)
    name = "SIFT" if hasattr(cv2, "SIFT_create") else "ORB"
    tg, target_scale = _resize_max(target, 1400)
    tp, _ = _resize_max(template, 500)
    if max(tp.shape[:2]) < 160:
        s = 160 / max(tp.shape[:2]); tp = cv2.resize(tp, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    g1 = cv2.cvtColor(tp, cv2.COLOR_BGR2GRAY); g2 = cv2.cvtColor(tg, cv2.COLOR_BGR2GRAY)
    kp1, d1 = detector.detectAndCompute(g1, None); kp2, d2 = detector.detectAndCompute(g2, None)
    if d1 is None or d2 is None or len(kp1) < 5 or len(kp2) < 5:
        return None
    norm = cv2.NORM_L2 if name == "SIFT" else cv2.NORM_HAMMING
    ratio = 0.76 if name == "SIFT" else 0.80
    pairs = cv2.BFMatcher(norm).knnMatch(d1, d2, k=2)
    good = [m for m, n in pairs if m.distance < ratio * n.distance]
    if len(good) < 7:
        return None
    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.5)
    if H is None or mask is None:
        return None
    inliers = int(mask.ravel().sum())
    if inliers < 5:
        return None
    h, w = tp.shape[:2]
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    p = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
    if not np.isfinite(p).all():
        return None
    x1, y1 = p.min(axis=0); x2, y2 = p.max(axis=0)
    tw, th = target.shape[1], target.shape[0]
    x1=max(0,x1); y1=max(0,y1); x2=min(tw-1,x2); y2=min(th-1,y2)
    bw, bh = x2-x1, y2-y1
    if bw < 8 or bh < 8:
        return None
    # Reject collapsed/absurd quadrilaterals that often come from repeated text.
    quad_area = abs(cv2.contourArea(p.astype(np.float32)))
    if quad_area < 0.20 * bw * bh or quad_area > 3.0 * bw * bh:
        return None
    area_ratio = (bw*bh)/float(max(1,tw*th))
    if area_ratio > 0.15:
        return None
    inlier_ratio = inliers / max(1, len(good))
    confidence = min(1.0, 0.55*inlier_ratio + 0.45*min(1.0, inliers/16.0))
    if confidence < 0.42:
        return None
    return Detection(True, int(x1), int(y1), int(bw), int(bh), float(confidence), name, 0.0, f"{inliers}/{len(good)} inliers")


def detect_emblem(template_bytes, image_bytes, min_confidence=0.55):
    try:
        template, mask = _template_and_mask(template_bytes)
        img = _pil_to_bgr(image_bytes)
        candidates = []
        d = _masked_template_match(template, mask, img)
        if d:
            candidates.append(d)
        # SIFT is useful when the image is rotated/warped. Skip it when the
        # masked template match is already strong; this keeps bulk scans fast.
        if not d or d.confidence < 0.74:
            d2 = _feature_match(template, img)
            if d2:
                candidates.append(d2)
        if not candidates:
            return Detection(False, reason="Emblema topilmadi. Namuna o'lchami/aniqligi yoki tanlangan qismni tekshiring.")
        best = max(candidates, key=lambda d: d.confidence)
        if best.confidence < min_confidence:
            return Detection(False, reason=f"Ishonch past: {best.confidence:.0%} (minimal {min_confidence:.0%})")
        return best
    except Exception as exc:
        return Detection(False, reason=str(exc))


def _load_rgba(data):
    return _open_pil(data).convert("RGBA")


def _fit_overlay(im, target_w, target_h, scale_percent=8, stretch=False, opacity=100):
    factor = max(0.50, 1.0 + float(scale_percent) / 100.0)
    tw = max(4, int(target_w * factor)); th = max(4, int(target_h * factor))
    if stretch:
        im = im.resize((tw, th), Image.Resampling.LANCZOS)
    else:
        # Preserve aspect ratio but never exceed the requested box.
        im.thumbnail((tw, th), Image.Resampling.LANCZOS)
    if opacity < 100:
        alpha = im.getchannel("A").point(lambda a: int(a * opacity / 100))
        im.putalpha(alpha)
    return im


def _inpaint_old_block(src_rgba, det, strength=3, padding_percent=6):
    """Remove the old watermark block before placing the new logo."""
    rgb = np.asarray(src_rgba.convert("RGB")).copy()
    mask = np.zeros(rgb.shape[:2], np.uint8)
    pad = max(1, int(round(max(det.w, det.h) * float(padding_percent) / 100.0)), int(strength))
    x1=max(0,det.x-pad); y1=max(0,det.y-pad); x2=min(rgb.shape[1],det.x+det.w+pad); y2=min(rgb.shape[0],det.y+det.h+pad)
    mask[y1:y2, x1:x2] = 255
    # Small watermark blocks benefit from Telea; larger blocks use a little more radius.
    radius = max(2, min(7, int(round(max(det.w, det.h) * 0.06))))
    clean = cv2.inpaint(rgb, mask, radius, cv2.INPAINT_TELEA)
    return Image.fromarray(clean).convert("RGBA")


def replace_emblem_on_image(image_bytes, template_bytes, replacement_bytes, scale_percent=8,
                            opacity=100, min_confidence=0.55, stretch=False, clean_old=True, cleanup_padding=6):
    src = _open_pil(image_bytes).convert("RGBA")
    det = detect_emblem(template_bytes, image_bytes, min_confidence=min_confidence)
    if not det.found:
        return image_bytes, det

    if clean_old:
        src = _inpaint_old_block(src, det, strength=2, padding_percent=cleanup_padding)

    replacement = _load_rgba(replacement_bytes)
    overlay = _fit_overlay(replacement, det.w, det.h, scale_percent=scale_percent, stretch=stretch, opacity=opacity)
    # Center on the exact detected block.
    cx = det.x + det.w / 2.0; cy = det.y + det.h / 2.0
    x = int(round(cx - overlay.width/2)); y = int(round(cy - overlay.height/2))
    x=max(0,min(src.width-overlay.width,x)); y=max(0,min(src.height-overlay.height,y))
    src.alpha_composite(overlay,(x,y))

    out=io.BytesIO()
    fmt = (_open_pil(image_bytes).format or "PNG").upper()
    if fmt in ("JPEG","JPG"):
        src.convert("RGB").save(out,format="JPEG",quality=95,subsampling=0)
    elif fmt == "WEBP":
        src.save(out,format="WEBP",quality=95,method=4)
    else:
        src.save(out,format="PNG")
    return out.getvalue(), det


def preview_emblem(image_bytes, template_bytes, replacement_bytes, scale_percent=8, opacity=100,
                   min_confidence=0.55, stretch=False, clean_old=True, cleanup_padding=6):
    out, det = replace_emblem_on_image(image_bytes,template_bytes,replacement_bytes,scale_percent,opacity,min_confidence,stretch,clean_old,cleanup_padding)
    if det.found:
        im=_open_pil(out).convert("RGB"); im.thumbnail((1100,800),Image.Resampling.LANCZOS)
        buf=io.BytesIO(); im.save(buf,format="PNG"); return buf.getvalue(),det
    return None,det


def annotate_detection(image_bytes, det, color=(0,180,80), width=4):
    img=_open_pil(image_bytes).convert("RGB")
    if det.found:
        cv=cv2.cvtColor(np.asarray(img),cv2.COLOR_RGB2BGR)
        cv2.rectangle(cv,(det.x,det.y),(det.x+det.w,det.y+det.h),color,width)
        label=f"Emblema {det.confidence:.0%}"
        cv2.putText(cv,label,(det.x,max(20,det.y-8)),cv2.FONT_HERSHEY_SIMPLEX,0.65,color,2,cv2.LINE_AA)
        img=Image.fromarray(cv2.cvtColor(cv,cv2.COLOR_BGR2RGB))
    out=io.BytesIO(); img.save(out,format="PNG"); return out.getvalue()


def replace_emblems_in_docx(input_path, output_path, template_bytes, replacement_bytes,
                            scale_percent=8, opacity=100, min_confidence=0.55,
                            stretch=False, clean_old=True, cleanup_padding=6):
    from watermark import _read_zip_entry_tolerant, SUPPORTED_EXTENSIONS
    report={"total_media":0,"found":0,"not_found":0,"repaired":0,"failed":0,"changed":0,"details":[]}
    with zipfile.ZipFile(input_path,"r") as zin, zipfile.ZipFile(output_path,"w",compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            lower=item.filename.lower(); ext=lower.rsplit(".",1)[-1] if "." in lower else ""
            is_media=lower.startswith("word/media/") and ("."+ext) in SUPPORTED_EXTENSIONS
            try:
                data,tolerant=_read_zip_entry_tolerant(zin,item)
            except Exception as exc:
                if is_media:
                    report["failed"]+=1; report["details"].append({"name":item.filename,"status":"failed","reason":str(exc)}); continue
                raise
            if is_media:
                report["total_media"]+=1
                if tolerant: report["repaired"]+=1
                try:
                    replaced,det=replace_emblem_on_image(data,template_bytes,replacement_bytes,scale_percent,opacity,min_confidence,stretch,clean_old,cleanup_padding)
                    if det.found:
                        data=replaced; report["found"]+=1; report["changed"]+=1
                        report["details"].append({"name":item.filename,"status":"replaced","confidence":round(det.confidence,4),"method":det.method,"bbox":[det.x,det.y,det.w,det.h],"crc_bad":bool(tolerant)})
                    else:
                        report["not_found"]+=1; report["details"].append({"name":item.filename,"status":"not_found","reason":det.reason,"crc_bad":bool(tolerant)})
                except Exception as exc:
                    report["failed"]+=1; report["details"].append({"name":item.filename,"status":"failed","reason":str(exc)})
            write_info=deepcopy(item)
            if tolerant or is_media:
                write_info.CRC=None; write_info.file_size=len(data); write_info.compress_size=0
            zout.writestr(write_info,data)
    return report
