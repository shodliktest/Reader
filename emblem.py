"""
V12 PRO — robust emblem/logo detector + replacer for Word images.

Design goals
------------
* A sample may be: transparent PNG, white background, black background, JPG/WEBP,
  emblem only, or emblem + Telegram mark as one block.
* The sample may appear at a different size, position, JPEG quality, contrast,
  brightness, or small rotation.
* Very small logos are handled without shrinking the target too aggressively.
* Detection is a multi-stage ensemble: foreground extraction, edge matching,
  grayscale correlation, color/structure validation, and SIFT/ORB fallback.
* Replacement removes only the detected logo footprint when possible instead of
  blindly erasing a large rectangle.
* DOCX media is processed independently and uses the CRC-tolerant reader from
  watermark.py. One broken image never stops the rest of the document.

No computer-vision system can mathematically guarantee detection for an arbitrary
unknown distortion. V12 therefore keeps the manual crop workflow as the final
fallback while making automatic detection substantially more tolerant.
"""
from __future__ import annotations

import io
import math
import os
import zipfile
from copy import deepcopy
from dataclasses import dataclass, asdict, field
from typing import Iterable

import cv2
import numpy as np
from PIL import Image, ImageOps


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
    details: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def _open_pil(source):
    if isinstance(source, Image.Image):
        im = source.copy()
    elif isinstance(source, (bytes, bytearray, memoryview)):
        im = Image.open(io.BytesIO(bytes(source)))
    elif isinstance(source, (str, os.PathLike)):
        im = Image.open(source)
    else:
        raise TypeError("Unsupported image source")
    # EXIF orientation is common for phone screenshots/photos.
    im = ImageOps.exif_transpose(im)
    im.load()
    return im


def _pil_to_bgr(source):
    rgb = _open_pil(source).convert("RGB")
    return cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)


def _rgb_mask_candidates(im: Image.Image) -> list[np.ndarray]:
    """Return several foreground masks; the best mask is selected later.

    We deliberately do not assume that white is background or that black is
    background. This is important for erased-background images and logos that
    themselves contain white/black symbols.
    """
    rgba = np.asarray(im.convert("RGBA"))
    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]

    masks: list[np.ndarray] = []
    if np.mean(alpha < 245) > 0.005:
        a = (alpha > 12).astype(np.uint8) * 255
        masks.append(a)
        # Transparent image: remove almost invisible RGB noise but preserve
        # white foreground if it is actually opaque.
        structural = (((sat > 18) | (val < 242))).astype(np.uint8) * 255
        masks.append(cv2.bitwise_and(a, structural))

    h, w = rgb.shape[:2]
    border_n = max(1, min(8, int(round(min(h, w) * 0.03))))
    border = np.concatenate([
        rgb[:border_n].reshape(-1, 3), rgb[-border_n:].reshape(-1, 3),
        rgb[:, :border_n].reshape(-1, 3), rgb[:, -border_n:].reshape(-1, 3)
    ], axis=0)
    bg = np.median(border, axis=0).astype(np.float32)
    dist = np.linalg.norm(rgb.astype(np.float32) - bg, axis=2)

    # Adaptive thresholds rather than exact #fff/#000 checks.
    whiteish = (val >= 242) & (sat <= 24)
    blackish = (val <= 24) & (sat <= 45)
    chroma = sat >= 28
    dark = val <= 225
    masks.append((~whiteish & (chroma | dark)).astype(np.uint8) * 255)
    masks.append((~blackish & (chroma | (val >= 35))).astype(np.uint8) * 255)
    masks.append((dist > max(14.0, float(np.percentile(dist, 72)) * 0.55)).astype(np.uint8) * 255)

    # A permissive structural mask catches thin blue/red Telegram outlines.
    masks.append(((sat > 20) | (val < 238)).astype(np.uint8) * 255)
    return masks


def _score_mask(mask: np.ndarray) -> float:
    if mask.size == 0:
        return -1e9
    m = mask > 0
    n = int(m.sum())
    total = mask.size
    if n < 8:
        return -1e9
    ys, xs = np.where(m)
    bw = xs.max() - xs.min() + 1
    bh = ys.max() - ys.min() + 1
    area_ratio = (bw * bh) / float(total)
    fill = n / float(max(1, bw * bh))
    # Prefer compact foreground without rewarding a mask that covers the whole
    # screenshot. Very sparse logos are still valid.
    score = 0.0
    score += 1.5 * min(1.0, n / max(80.0, total * 0.04))
    score += 1.2 * min(1.0, fill / 0.45)
    score += 1.0 * (1.0 - min(1.0, area_ratio / 0.90))
    if area_ratio > 0.90:
        score -= 4.0
    return score


def _choose_mask(im: Image.Image) -> np.ndarray:
    candidates = _rgb_mask_candidates(im)
    best = max(candidates, key=_score_mask)
    m = best.copy()
    # Preserve tiny connected components: only a very small open kernel.
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    return m


def _trim_template(source, pad=3):
    im = _open_pil(source).convert("RGBA")
    mask = _choose_mask(im)
    ys, xs = np.where(mask > 0)
    if len(xs) < 8:
        return im, np.ones((im.height, im.width), np.uint8) * 255
    x1 = max(0, int(xs.min()) - pad)
    y1 = max(0, int(ys.min()) - pad)
    x2 = min(im.width, int(xs.max()) + pad + 1)
    y2 = min(im.height, int(ys.max()) + pad + 1)
    return im.crop((x1, y1, x2, y2)), mask[y1:y2, x1:x2]


def _template_bundle(source, trim=True):
    """Build a matching bundle.

    ``trim=True`` is the normal/background-independent path. ``trim=False``
    keeps the original canvas as a fallback for samples whose matte is part of
    the actual target (for example a black square copied into a 20px Word image).
    """
    original = _open_pil(source).convert("RGBA")
    if trim:
        cropped, fg = _trim_template(original)
    else:
        cropped = original
        fg = _choose_mask(original)
    rgba = np.asarray(cropped.convert("RGBA"))
    rgb = rgba[:, :, :3]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    alpha = rgba[:, :, 3]
    if np.mean(alpha < 245) > 0.005:
        fg = cv2.bitwise_and(fg, (alpha > 10).astype(np.uint8) * 255)
    if fg.shape != bgr.shape[:2]:
        fg = np.ones(bgr.shape[:2], np.uint8) * 255
    fg = cv2.GaussianBlur(fg, (3, 3), 0)
    fg = np.where(fg > 18, 255, 0).astype(np.uint8)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edge = cv2.Canny(gray, 35, 125)
    # Keep edges inside the useful foreground region, but if that removes too
    # much, use the raw edge map.
    masked_edge = cv2.bitwise_and(edge, fg)
    if np.count_nonzero(masked_edge) >= 8:
        edge = masked_edge
    return {
        "bgr": bgr,
        "gray": gray,
        "edge": edge,
        "mask": fg,
        "size": bgr.shape[:2],
    }


def _resize_target(target, max_side=1900):
    h, w = target.shape[:2]
    if max(h, w) <= max_side:
        return target, 1.0
    scale = max_side / float(max(h, w))
    out = cv2.resize(target, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    return out, scale


def _dense_scales(fast=True):
    # The low-scale band is intentionally dense because a 400–500 px source
    # emblem may become only 18–35 px inside a Word screenshot.  Keep the fast
    # path compact enough for multiple images; the deep path fills the gaps.
    if fast:
        vals = [0.025, 0.032, 0.040, 0.050, 0.060, 0.075, 0.090, 0.12,
                0.15, 0.18, 0.22, 0.30, 0.40, 0.50, 0.80, 1.0, 1.5, 2.0, 2.5]
    else:
        vals = list(np.geomspace(0.018, 4.5, 30))
        vals += [0.02, 0.025, 0.028, 0.032, 0.036, 0.040, 0.045, 0.050,
                 0.060, 0.075, 0.09, 0.10, 0.12, 0.15, 0.18, 0.22, 0.30,
                 0.40, 0.50, 0.6667, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 2.0,
                 2.5, 3.0, 4.0]
    return sorted(set(round(float(v), 4) for v in vals))


def _corr(a, b):
    if a.shape != b.shape or a.size < 4:
        return 0.0
    af = a.astype(np.float32).ravel(); bf = b.astype(np.float32).ravel()
    af -= af.mean(); bf -= bf.mean()
    den = float(np.linalg.norm(af) * np.linalg.norm(bf))
    return float(np.dot(af, bf) / den) if den > 1e-7 else 0.0


def _masked_corr(a, b, mask):
    if a.shape != b.shape:
        return 0.0
    m = mask > 15
    if int(m.sum()) < 8:
        return 0.0
    af = a[m].astype(np.float32); bf = b[m].astype(np.float32)
    af -= af.mean(); bf -= bf.mean()
    den = float(np.linalg.norm(af) * np.linalg.norm(bf))
    return float(np.dot(af, bf) / den) if den > 1e-7 else 0.0


def _masked_mae_similarity(a, b, mask):
    m = mask > 15
    if int(m.sum()) < 8:
        return 0.0
    mae = float(np.mean(np.abs(a[m].astype(np.float32) - b[m].astype(np.float32))))
    return max(0.0, min(1.0, 1.0 - mae / 110.0))


def _gradient_similarity(a, b):
    ga = cv2.Laplacian(a, cv2.CV_32F)
    gb = cv2.Laplacian(b, cv2.CV_32F)
    # Normalize absolute gradient energy; useful after JPEG/contrast changes.
    ga = cv2.normalize(np.abs(ga), None, 0, 1, cv2.NORM_MINMAX)
    gb = cv2.normalize(np.abs(gb), None, 0, 1, cv2.NORM_MINMAX)
    return max(0.0, _corr(ga, gb))


def _hsv_similarity(a, b, mask):
    if a.shape != b.shape:
        return 0.0
    ha = cv2.cvtColor(a, cv2.COLOR_BGR2HSV)
    hb = cv2.cvtColor(b, cv2.COLOR_BGR2HSV)
    m = mask > 15
    if int(m.sum()) < 8:
        return 0.0
    # Compare robust mean color and saturation; hue becomes unstable near white.
    va = ha[m].astype(np.float32); vb = hb[m].astype(np.float32)
    mean_a = va.mean(axis=0); mean_b = vb.mean(axis=0)
    d = np.linalg.norm((mean_a - mean_b) / np.array([90.0, 255.0, 255.0], np.float32))
    return max(0.0, min(1.0, 1.0 - d))


def _saturation_similarity(a, b, mask):
    """Compare chroma structure rather than absolute RGB values.

    This is especially useful when the old sample has a black/white matte
    around it but the real target sits on a white page, and when a watermark
    is laid over the target.
    """
    if a.shape != b.shape:
        return 0.0
    sa = cv2.cvtColor(a, cv2.COLOR_BGR2HSV)[:, :, 1].astype(np.float32)
    sb = cv2.cvtColor(b, cv2.COLOR_BGR2HSV)[:, :, 1].astype(np.float32)
    m = mask > 15
    if int(m.sum()) < 8:
        return 0.0
    # Correlation is stable under JPEG/brightness changes.
    return max(0.0, _corr(sa[m], sb[m]))


def _shape_similarity(template_mask, patch_bgr):
    """Approximate foreground silhouette similarity, independent of page color."""
    if template_mask is None or template_mask.size == 0:
        return 0.0
    hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    # Colored/dark pixels are the most reliable foreground on white Word pages.
    pm = (((sat > 32) | (val < 215))).astype(np.uint8) * 255
    pm = cv2.morphologyEx(pm, cv2.MORPH_CLOSE, np.ones((2,2), np.uint8))
    tm = cv2.resize(template_mask, (patch_bgr.shape[1], patch_bgr.shape[0]),
                    interpolation=cv2.INTER_NEAREST) > 35
    pm_bool = pm > 0
    union = int(np.count_nonzero(tm | pm_bool))
    if union < 8:
        return 0.0
    inter = int(np.count_nonzero(tm & pm_bool))
    return float(inter / union)


def _candidate_points(res, top_k=5, min_distance=12):
    """Return several maxima, not just the single global maximum."""
    work = res.copy()
    out = []
    for _ in range(top_k):
        _, mx, _, loc = cv2.minMaxLoc(work)
        if not np.isfinite(mx):
            break
        x, y = loc
        out.append((x, y, float(mx)))
        x1 = max(0, x - min_distance); y1 = max(0, y - min_distance)
        x2 = min(work.shape[1], x + min_distance + 1); y2 = min(work.shape[0], y + min_distance + 1)
        work[y1:y2, x1:x2] = -1.0
    return out


def _evaluate_candidate(tpl, target, x, y, w, h, locator_score, method, target_scale):
    patch = target[y:y+h, x:x+w]
    if patch.shape[:2] != (h, w):
        return None
    rt = tpl["resized"]
    rm = tpl["mask_resized"]
    rg = tpl["gray_resized"]
    re = tpl["edge_resized"]
    pg = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)

    gray_corr = _masked_corr(pg, rg, rm)
    edge_corr = _corr(cv2.Canny(pg, 35, 125), re)
    mae_sim = _masked_mae_similarity(patch, rt, rm)
    grad_sim = _gradient_similarity(pg, rg)
    hsv_sim = _hsv_similarity(patch, rt, rm)
    sat_sim = _saturation_similarity(patch, rt, rm)
    shape_sim = _shape_similarity(rm, patch)

    # Pixel similarity is intentionally not dominant: the sample can have a
    # black/white matte that does not exist in the Word image.  Chroma + shape
    # are strong signals for tiny blue/red emblems on white pages.
    score = (
        0.27 * max(0.0, edge_corr) +
        0.20 * max(0.0, gray_corr) +
        0.10 * mae_sim +
        0.10 * grad_sim +
        0.10 * hsv_sim +
        0.11 * sat_sim +
        0.07 * shape_sim +
        0.05 * max(0.0, min(1.0, locator_score))
    )
    return {
        "score": float(score), "edge": float(edge_corr), "gray": float(gray_corr),
        "mae": float(mae_sim), "gradient": float(grad_sim), "hsv": float(hsv_sim),
        "saturation": float(sat_sim), "shape": float(shape_sim),
        "locator": float(locator_score), "x": int(x), "y": int(y), "w": int(w), "h": int(h),
        "target_scale": float(target_scale), "method": method,
    }


def _multiscale_search(bundle, target, fast=True):
    tg, target_scale = _resize_target(target, 1000 if fast else 1500)
    H, W = tg.shape[:2]
    h0, w0 = bundle["bgr"].shape[:2]
    if min(h0, w0) < 6 or H < 10 or W < 10:
        return None

    gray = cv2.cvtColor(tg, cv2.COLOR_BGR2GRAY)
    edge = cv2.Canny(gray, 35, 125)
    target_sat = cv2.cvtColor(tg, cv2.COLOR_BGR2HSV)[:, :, 1]
    best = None
    second = None

    for scale in _dense_scales(fast):
        w = max(6, int(round(w0 * scale))); h = max(6, int(round(h0 * scale)))
        if w >= W - 1 or h >= H - 1 or w < 7 or h < 7:
            continue
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        rt = cv2.resize(bundle["bgr"], (w, h), interpolation=interp)
        rg = cv2.cvtColor(rt, cv2.COLOR_BGR2GRAY)
        rm = cv2.resize(bundle["mask"], (w, h), interpolation=cv2.INTER_NEAREST)
        re = cv2.Canny(rg, 35, 125)
        if np.count_nonzero(re) < 5:
            continue

        # Edge and grayscale searches are intentionally independent. A white
        # page can defeat color matching while edge matching still succeeds;
        # a blurred/JPEG logo can do the opposite.
        edge_res = cv2.matchTemplate(edge, re, cv2.TM_CCOEFF_NORMED)
        gray_res = cv2.matchTemplate(gray, rg, cv2.TM_CCOEFF_NORMED)
        # Masked grayscale locator plus a saturation locator.  The latter is
        # much less sensitive to black/white sample backgrounds.
        try:
            color_res = cv2.matchTemplate(gray, rg, cv2.TM_CCORR_NORMED, mask=rm)
        except Exception:
            color_res = None
        # Saturation matching is particularly valuable for tiny coloured logos,
        # but is expensive on large templates.  Use it in the tiny/medium band.
        sat_res = None
        if scale <= 0.40:
            template_sat = cv2.cvtColor(rt, cv2.COLOR_BGR2HSV)[:, :, 1]
            try:
                sat_res = cv2.matchTemplate(target_sat, template_sat, cv2.TM_CCOEFF_NORMED, mask=rm)
            except Exception:
                sat_res = cv2.matchTemplate(target_sat, template_sat, cv2.TM_CCOEFF_NORMED)

        candidates = []
        topn = 1 if fast else 2
        for x, y, s in _candidate_points(edge_res, topn, max(5, min(w, h)//3)):
            candidates.append((x, y, s, "edge"))
        for x, y, s in _candidate_points(gray_res, topn, max(5, min(w, h)//3)):
            candidates.append((x, y, s, "gray"))
        if color_res is not None:
            for x, y, s in _candidate_points(color_res, topn, max(5, min(w, h)//3)):
                candidates.append((x, y, s, "masked-gray"))
        if sat_res is not None:
            for x, y, s in _candidate_points(sat_res, topn, max(5, min(w, h)//3)):
                candidates.append((x, y, s, "saturation"))

        seen = set()
        for x, y, loc_score, loc_type in candidates:
            key = (x // max(2, w//5), y // max(2, h//5))
            if key in seen:
                continue
            seen.add(key)
            local = dict(bundle)
            local.update({
                "resized": rt, "gray_resized": rg, "edge_resized": re,
                "mask_resized": rm,
            })
            ev = _evaluate_candidate(local, tg, x, y, w, h, loc_score, f"MultiScale/{loc_type}", target_scale)
            if ev is None:
                continue
            if best is None or ev["score"] > best["score"]:
                second = best; best = ev
            elif second is None or ev["score"] > second["score"]:
                second = ev

        # Once a tiny candidate has strong independent shape + chroma support,
        # continuing through every larger scale only adds latency and increases
        # the chance that unrelated text wins.
        if (fast and best is not None and min(best["w"], best["h"]) <= 40
                and best["score"] >= 0.55
                and best.get("shape", 0.0) >= 0.70
                and best.get("saturation", 0.0) >= 0.65
                and best["gray"] >= 0.35):
            break

    if best is None:
        return None
    # Map target-space coordinates back to original image.
    inv = 1.0 / best["target_scale"]
    x = int(round(best["x"] * inv)); y = int(round(best["y"] * inv))
    w = int(round(best["w"] * inv)); h = int(round(best["h"] * inv))
    W0, H0 = target.shape[1], target.shape[0]
    x = max(0, min(W0 - 1, x)); y = max(0, min(H0 - 1, y))
    w = max(6, min(W0 - x, w)); h = max(6, min(H0 - y, h))
    area_ratio = (w*h) / float(max(1, W0*H0))
    if area_ratio > 0.30:
        return None

    margin = best["score"] - (second["score"] if second else 0.0)
    # Tiny logos get a slightly lower absolute threshold, but must have a strong
    # structural correlation. This is safer than simply lowering min_confidence.
    tiny = min(w, h) <= 36
    # Tiny logos have fewer pixels, so require independent chroma/shape support
    # rather than an unnecessarily high raw correlation.
    absolute = 0.36 if tiny else 0.45
    structural = max(best["edge"], best["gray"], best.get("saturation", 0.0))
    independent = max(best.get("shape", 0.0), best.get("saturation", 0.0), best["edge"])
    if best["score"] < absolute or structural < 0.10 or independent < 0.12:
        return None
    # If confidence is borderline, require a meaningful separation from the
    # second-best candidate so text/boxes do not win accidentally.
    if best["score"] < 0.50 and margin < 0.018 and structural < 0.30:
        return None

    conf = (
        0.36 * best["score"] +
        0.18 * max(0.0, best["edge"]) +
        0.15 * max(0.0, best["gray"]) +
        0.11 * max(0.0, best.get("saturation", 0.0)) +
        0.10 * max(0.0, best.get("shape", 0.0)) +
        0.05 * best["mae"] +
        0.05 * min(1.0, max(0.0, margin) * 8.0)
    )
    conf = float(max(0.0, min(1.0, conf)))
    return Detection(True, x, y, w, h, conf, "MultiScaleEnsemble", 0.0,
                     f"edge={best['edge']:.3f}, gray={best['gray']:.3f}, color={best['hsv']:.3f}, "
                     f"sat={best.get('saturation',0):.3f}, shape={best.get('shape',0):.3f}, "
                     f"grad={best['gradient']:.3f}, scale={best['w']/w0:.3f}x, margin={margin:.3f}",
                     {k: best[k] for k in ("score","edge","gray","mae","gradient","hsv","saturation","shape","locator")})


def _feature_match(bundle, target):
    """SIFT/ORB fallback for mild rotation, perspective, blur and scale changes."""
    if hasattr(cv2, "SIFT_create"):
        detector = cv2.SIFT_create(nfeatures=3000, contrastThreshold=0.008, edgeThreshold=15, sigma=1.2)
        norm, ratio, method = cv2.NORM_L2, 0.80, "SIFT"
    else:
        detector = cv2.ORB_create(nfeatures=3000, fastThreshold=3, edgeThreshold=7)
        norm, ratio, method = cv2.NORM_HAMMING, 0.84, "ORB"

    tg, target_scale = _resize_target(target, 1900)
    tp = bundle["bgr"]
    # Upscale tiny reference so feature detectors have enough pixels.
    min_side = min(tp.shape[:2])
    up = max(1.0, min(6.0, 180.0 / max(8.0, float(min_side))))
    tp = cv2.resize(tp, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC) if up > 1 else tp
    tg2 = cv2.resize(tg, None, fx=1.35, fy=1.35, interpolation=cv2.INTER_CUBIC)

    g1 = cv2.cvtColor(tp, cv2.COLOR_BGR2GRAY); g2 = cv2.cvtColor(tg2, cv2.COLOR_BGR2GRAY)
    kp1, d1 = detector.detectAndCompute(g1, None); kp2, d2 = detector.detectAndCompute(g2, None)
    if d1 is None or d2 is None or len(kp1) < 4 or len(kp2) < 8:
        return None
    pairs = cv2.BFMatcher(norm).knnMatch(d1, d2, k=2)
    good = [m for m, n in pairs if m.distance < ratio*n.distance]
    if len(good) < 5:
        return None
    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1,1,2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1,1,2)
    Hm, inlier_mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.5)
    if Hm is None or inlier_mask is None:
        return None
    inliers = int(inlier_mask.ravel().sum())
    if inliers < 4:
        return None
    hh, ww = tp.shape[:2]
    corners = np.float32([[0,0],[ww,0],[ww,hh],[0,hh]]).reshape(-1,1,2)
    pts = cv2.perspectiveTransform(corners, Hm).reshape(-1,2)
    if not np.isfinite(pts).all():
        return None
    pts /= 1.35
    pts /= target_scale
    x1,y1 = pts.min(axis=0); x2,y2 = pts.max(axis=0)
    W0,H0 = target.shape[1], target.shape[0]
    x1=max(0,float(x1)); y1=max(0,float(y1)); x2=min(W0-1,float(x2)); y2=min(H0-1,float(y2))
    w=x2-x1; h=y2-y1
    if w < 6 or h < 6 or w*h > 0.30*W0*H0:
        return None
    # A valid homography may change scale and mild perspective, but it should
    # not turn a square sample into a 2:1 sliver. This prevents SIFT from
    # matching a fragment of watermark text and then producing a wrong bbox.
    src_ratio = float(tp.shape[1]) / max(1.0, float(tp.shape[0]))
    dst_ratio = float(w) / max(1.0, float(h))
    ratio_factor = max(dst_ratio/src_ratio, src_ratio/dst_ratio)
    if ratio_factor > 1.75:
        return None
    ir = inliers/max(1,len(good))
    conf = min(1.0, 0.50*ir + 0.50*min(1.0,inliers/12.0))
    if conf < 0.34:
        return None
    return Detection(True,int(x1),int(y1),int(w),int(h),float(conf),method,0.0,
                     f"{inliers}/{len(good)} inliers", {"inliers":inliers,"good":len(good)})


def detect_emblem(template_bytes, image_bytes, min_confidence=0.45):
    """Detect an emblem with a background-aware two-pass strategy.

    Pass 1 removes white/black/transparent matte and is therefore ideal when a
    sample was cropped from a white page.  Pass 2 is a raw-canvas fallback and
    is used only when pass 1 is weak; this matters when the matte itself is
    present in the target image (especially tiny black/white 18–30px logos).
    """
    try:
        target = _pil_to_bgr(image_bytes)
        threshold = max(0.30, float(min_confidence))
        candidates = []

        # Normal, matte-independent pass.
        bundle = _template_bundle(template_bytes, trim=True)
        if min(bundle["bgr"].shape[:2]) < 6:
            return Detection(False, reason="Namuna juda kichik.")
        d1 = _multiscale_search(bundle, target, fast=True)
        if d1:
            candidates.append(d1)
        if not d1 or d1.confidence < max(0.50, threshold + 0.03):
            dslow = _multiscale_search(bundle, target, fast=False)
            if dslow:
                candidates.append(dslow)
        if not d1 or d1.confidence < 0.68:
            d2 = _feature_match(bundle, target)
            if d2:
                candidates.append(d2)

        best = max(candidates, key=lambda d: d.confidence) if candidates else None

        # Raw-canvas fallback.  Do not pay this cost when the normal pass is
        # already convincing; this keeps DOCX batches practical on Streamlit.
        if best is None or best.confidence < max(0.55, threshold + 0.08):
            raw = _template_bundle(template_bytes, trim=False)
            rd = _multiscale_search(raw, target, fast=True)
            if rd:
                candidates.append(rd)
            raw_best = max((d for d in candidates if d is not None),
                           key=lambda d: d.confidence, default=None)
            if raw_best and (best is None or raw_best.confidence > best.confidence):
                best = raw_best

        if best is None:
            return Detection(False, reason="Emblema topilmadi. Namuna sifati, o‘lchami yoki target siqilishi juda farq qilishi mumkin.")
        if best.confidence < threshold:
            return Detection(False, confidence=best.confidence, method=best.method,
                             reason=f"Eng yaxshi nomzod {best.confidence:.0%}; minimal {threshold:.0%}.",
                             details=best.details)
        return best
    except Exception as exc:
        return Detection(False, reason=f"Aniqlash xatosi: {exc}")


def _load_rgba(source):
    return _open_pil(source).convert("RGBA")


def _content_crop_rgba(im):
    """Trim transparent/solid matte around a replacement without destroying
    white pixels that are actually part of the logo."""
    rgba = np.asarray(im.convert("RGBA"))
    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3]
    h, w = rgb.shape[:2]
    if np.mean(alpha < 245) > 0.005:
        mask = (alpha > 12).astype(np.uint8) * 255
    else:
        border_n = max(1, min(6, int(round(min(h, w) * 0.03))))
        border = np.concatenate([
            rgb[:border_n].reshape(-1,3), rgb[-border_n:].reshape(-1,3),
            rgb[:, :border_n].reshape(-1,3), rgb[:, -border_n:].reshape(-1,3)
        ], axis=0).astype(np.float32)
        bg = np.median(border, axis=0)
        dist = np.linalg.norm(rgb.astype(np.float32) - bg, axis=2)
        # Prefer a conservative threshold; this catches black/white matte while
        # preserving white lettering inside a colored logo.
        threshold = max(12.0, float(np.percentile(dist, 72)) * 0.45)
        mask = (dist > threshold).astype(np.uint8) * 255
    ys, xs = np.where(mask > 0)
    if len(xs) < 8:
        return im.convert("RGBA")
    pad = max(1, int(round(min(w, h) * 0.01)))
    x1=max(0,int(xs.min())-pad); y1=max(0,int(ys.min())-pad)
    x2=min(w,int(xs.max())+pad+1); y2=min(h,int(ys.max())+pad+1)
    return im.crop((x1,y1,x2,y2)).convert("RGBA")


def _fit_overlay(im, target_w, target_h, scale_percent=0, stretch=False, opacity=100):
    # Always trim the replacement first.  A 1000x1000 PNG containing a 100x100
    # logo must not be positioned using its empty canvas.
    out = _content_crop_rgba(im)
    factor = max(0.20, 1.0 + float(scale_percent)/100.0)
    tw=max(4,int(round(target_w*factor))); th=max(4,int(round(target_h*factor)))
    if stretch:
        out=out.resize((tw,th),Image.Resampling.LANCZOS)
    else:
        out.thumbnail((tw,th),Image.Resampling.LANCZOS)
    if opacity < 100:
        a=out.getchannel("A").point(lambda p:int(p*opacity/100))
        out.putalpha(a)
    return out


def _template_foreground_mask(template_bytes, det):
    """Map the selected sample's foreground mask into the detected bbox."""
    im=_open_pil(template_bytes).convert("RGBA")
    _, mask=_trim_template(im)
    if det.w <= 0 or det.h <= 0:
        return None
    m=cv2.resize(mask,(det.w,det.h),interpolation=cv2.INTER_LINEAR)
    m=np.where(m>35,255,0).astype(np.uint8)
    return m


def _clean_old(src_rgba, template_bytes, det, padding_percent=4):
    """Content-aware cleanup restricted to the logo footprint.

    This avoids the V9 behaviour where a large rectangular inpaint could erase
    nearby question text. A small dilation is added for anti-aliased edges.
    """
    rgb=np.asarray(src_rgba.convert("RGB")).copy()
    mask=_template_foreground_mask(template_bytes,det)
    if mask is None:
        mask=np.ones((det.h,det.w),np.uint8)*255
    pad=max(0,int(round(max(det.w,det.h)*padding_percent/100.0)))
    if pad:
        k=max(3,2*min(8,pad)+1)
        mask=cv2.dilate(mask,np.ones((k,k),np.uint8),iterations=1)
    full=np.zeros(rgb.shape[:2],np.uint8)
    x1=max(0,det.x); y1=max(0,det.y); x2=min(rgb.shape[1],det.x+det.w); y2=min(rgb.shape[0],det.y+det.h)
    crop=mask[:y2-y1,:x2-x1]
    full[y1:y2,x1:x2]=crop
    radius=max(1,min(8,int(round(max(det.w,det.h)*0.045))))
    cleaned=cv2.inpaint(rgb,full,radius,cv2.INPAINT_TELEA)
    return Image.fromarray(cleaned).convert("RGBA")


def _save_like(source_bytes, image):
    original=_open_pil(source_bytes)
    fmt=(original.format or "PNG").upper()
    out=io.BytesIO()
    if fmt in ("JPEG","JPG"):
        image.convert("RGB").save(out,format="JPEG",quality=96,subsampling=0,optimize=True)
    elif fmt=="WEBP":
        image.save(out,format="WEBP",quality=96,method=6)
    elif fmt=="BMP":
        image.convert("RGB").save(out,format="BMP")
    elif fmt in ("TIFF","TIF"):
        image.save(out,format="TIFF")
    else:
        image.save(out,format="PNG",optimize=False)
    return out.getvalue()


def replace_emblem_on_image(image_bytes, template_bytes, replacement_bytes, scale_percent=0,
                            opacity=100, min_confidence=0.45, stretch=False,
                            clean_old=True, cleanup_padding=4):
    src=_open_pil(image_bytes).convert("RGBA")
    det=detect_emblem(template_bytes,image_bytes,min_confidence=min_confidence)
    if not det.found:
        return image_bytes,det
    if clean_old:
        src=_clean_old(src,template_bytes,det,padding_percent=cleanup_padding)
    replacement=_load_rgba(replacement_bytes)
    overlay=_fit_overlay(replacement,det.w,det.h,scale_percent,stretch,opacity)
    cx=det.x+det.w/2.0; cy=det.y+det.h/2.0
    x=int(round(cx-overlay.width/2)); y=int(round(cy-overlay.height/2))
    x=max(0,min(src.width-overlay.width,x)); y=max(0,min(src.height-overlay.height,y))
    src.alpha_composite(overlay,(x,y))
    return _save_like(image_bytes,src),det


def preview_emblem(image_bytes,template_bytes,replacement_bytes,scale_percent=0,opacity=100,
                   min_confidence=0.45,stretch=False,clean_old=True,cleanup_padding=4):
    out,det=replace_emblem_on_image(image_bytes,template_bytes,replacement_bytes,scale_percent,opacity,min_confidence,stretch,clean_old,cleanup_padding)
    if det.found:
        im=_open_pil(out).convert("RGB"); im.thumbnail((1200,900),Image.Resampling.LANCZOS)
        b=io.BytesIO(); im.save(b,format="PNG"); return b.getvalue(),det
    return None,det


def annotate_detection(image_bytes,det,color=(0,180,80),width=4):
    img=_open_pil(image_bytes).convert("RGB")
    cv=cv2.cvtColor(np.asarray(img),cv2.COLOR_RGB2BGR)
    if det.found:
        cv2.rectangle(cv,(det.x,det.y),(det.x+det.w,det.y+det.h),color,width)
        label=f"Emblema {det.confidence:.0%} • {det.method}"
        cv2.putText(cv,label,(det.x,max(20,det.y-8)),cv2.FONT_HERSHEY_SIMPLEX,0.55,color,2,cv2.LINE_AA)
    out=io.BytesIO(); Image.fromarray(cv2.cvtColor(cv,cv2.COLOR_BGR2RGB)).save(out,format="PNG"); return out.getvalue()


def _replace_emblems_in_zip(zin, zout, template_bytes, replacement_bytes,
                            scale_percent=0, opacity=100, min_confidence=0.45,
                            stretch=False, clean_old=True, cleanup_padding=4):
    from watermark import _read_zip_entry_tolerant, SUPPORTED_EXTENSIONS
    report={"total_media":0,"found":0,"not_found":0,"repaired":0,"failed":0,"changed":0,"details":[]}
    for item in zin.infolist():
        lower=item.filename.lower(); ext=os.path.splitext(lower)[1]
        is_media=lower.startswith("word/media/") and ext in SUPPORTED_EXTENSIONS
        try:
            data,tolerant=_read_zip_entry_tolerant(zin,item)
        except Exception as exc:
            if is_media:
                report["failed"]+=1
                report["details"].append({"name":item.filename,"status":"failed","reason":str(exc)})
                # Keep the rest of the DOCX intact. A truly unreadable media
                # entry cannot be reconstructed, but it must not stop others.
                continue
            raise

        if is_media:
            report["total_media"]+=1
            if tolerant:
                report["repaired"]+=1
            try:
                replaced,det=replace_emblem_on_image(
                    data,template_bytes,replacement_bytes,
                    scale_percent,opacity,min_confidence,stretch,
                    clean_old,cleanup_padding
                )
                if det.found:
                    data=replaced
                    report["found"]+=1; report["changed"]+=1
                    report["details"].append({
                        "name":item.filename,"status":"replaced",
                        "confidence":round(det.confidence,4),"method":det.method,
                        "bbox":[det.x,det.y,det.w,det.h],"crc_bad":bool(tolerant),
                        "details":det.details,
                    })
                else:
                    report["not_found"]+=1
                    report["details"].append({
                        "name":item.filename,"status":"not_found",
                        "reason":det.reason,"confidence":round(det.confidence,4),
                        "crc_bad":bool(tolerant),
                    })
            except Exception as exc:
                report["failed"]+=1
                report["details"].append({"name":item.filename,"status":"failed","reason":str(exc)})

        # Always rewrite media entries so stale CRC metadata is repaired.
        info=deepcopy(item)
        if tolerant or is_media:
            info.CRC=None
            info.file_size=len(data)
            info.compress_size=0
        zout.writestr(info,data)
    return report


def replace_emblems_in_docx_bytes(docx_bytes, template_bytes, replacement_bytes,
                                  scale_percent=0, opacity=100, min_confidence=0.45,
                                  stretch=False, clean_old=True, cleanup_padding=4):
    """Replace emblems in a DOCX entirely in memory.

    This is the preferred web-app path: it eliminates /tmp filename races and
    guarantees that a successful processing report has an actual output blob.
    """
    src=io.BytesIO(docx_bytes)
    dst=io.BytesIO()
    with zipfile.ZipFile(src,"r") as zin, zipfile.ZipFile(dst,"w",compression=zipfile.ZIP_DEFLATED) as zout:
        report=_replace_emblems_in_zip(
            zin,zout,template_bytes,replacement_bytes,
            scale_percent,opacity,min_confidence,stretch,clean_old,cleanup_padding
        )
    return dst.getvalue(), report


def replace_emblems_in_docx(input_path,output_path,template_bytes,replacement_bytes,
                            scale_percent=0,opacity=100,min_confidence=0.45,stretch=False,
                            clean_old=True,cleanup_padding=4):
    """File-path compatibility wrapper around the in-memory implementation."""
    with open(input_path,"rb") as f:
        docx_bytes=f.read()
    output_bytes, report=replace_emblems_in_docx_bytes(
        docx_bytes,template_bytes,replacement_bytes,
        scale_percent,opacity,min_confidence,stretch,clean_old,cleanup_padding
    )
    # Atomic replace: never leave a half-written output behind.
    out_dir=os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp_path=output_path+".tmp"
    with open(tmp_path,"wb") as f:
        f.write(output_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path,output_path)
    return report

