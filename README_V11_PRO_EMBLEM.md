# Reader — V11 PRO Emblem Engine

V11 is the updated emblem replacement engine based on the supplied working V10 project.

## Fixed in V11

- **Very small emblems:** search now goes below the old 0.06 scale floor, so a 400–500 px sample can be found at roughly 18–30 px in a Word image.
- **White-page targets:** black/white matte in the uploaded sample is not treated as the emblem itself.
- **Black-background samples:** foreground extraction trims the matte before matching.
- **Watermark interference:** matching combines grayscale, edge, HSV/chroma and foreground-shape evidence instead of relying on one template score.
- **SIFT false positives:** geometrically implausible feature matches are rejected so a watermark-text fragment cannot become a huge/wrong replacement box.
- **Correct replacement size:** replacement images are content-trimmed first; empty canvas around the new PNG no longer changes its placement/size.
- **Default size correction:** replacement scale defaults to **0%** instead of +8%, so the new emblem starts at the detected old footprint.
- **Independent images:** every `word/media/*` image is still processed separately; one bad/CRC-broken image does not stop the rest.
- **Combined or separate sample:** crop only the emblem for emblem-only replacement; crop emblem + Telegram mark together for block replacement.
- **Manual fallback:** the existing crop-from-Word/test-image workflow remains available when automatic detection is not confident enough.

## Detection flow

1. EXIF-safe image decoding
2. Adaptive foreground extraction for transparent/white/black/JPEG/WEBP samples
3. Content trimming
4. Dense low-scale multi-resolution search
5. Grayscale + edge + masked correlation + saturation matching
6. Foreground silhouette validation
7. SIFT/ORB fallback for rotation/perspective
8. Geometry validation
9. Footprint-aware inpainting
10. Replacement content trimming + exact footprint placement

## Important limitation

No vision algorithm can guarantee arbitrary recognition after severe cropping, heavy blur, or complete visual destruction. V11 therefore keeps the manual crop workflow as the deterministic fallback.
