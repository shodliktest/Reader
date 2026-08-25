# Reader — V10 PRO Emblem Engine

V10 is a production-oriented upgrade of the V9 emblem replacement workflow.

## What it handles
- emblem-only sample
- emblem + Telegram mark as one combined sample
- transparent PNG
- white/near-white background
- black background
- JPG/JPEG/WEBP
- different logo scale (small -> large)
- JPEG compression / blur / brightness changes
- mild rotation and perspective through SIFT/ORB fallback
- tiny logos
- different position in every Word image
- CRC-broken DOCX media entries through the existing tolerant ZIP reader
- independent per-image processing: one failed image does not stop the rest

## Detection pipeline
1. EXIF-safe image decoding
2. adaptive foreground/background masks
3. template trimming
4. fast multi-scale edge + grayscale ensemble
5. deeper multi-scale search only when confidence is weak
6. SIFT/ORB fallback for geometric changes
7. confidence + candidate-margin validation
8. footprint-aware inpainting
9. replacement with original image format preservation

## Important behavior
The selected sample defines the replacement unit.

- Crop only the round emblem -> only that emblem is searched/replaced.
- Crop emblem + Telegram icon together -> the whole combined block is searched/replaced.

There is no mathematically universal detector for every arbitrary distortion. If a source is radically different (heavy crop, severe perspective, extremely low resolution), the UI's manual crop workflow remains the final fallback.

## Runtime
No extra ML model is required. V10 uses OpenCV + Pillow already present in the project.
