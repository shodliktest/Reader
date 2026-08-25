# V9 PRO Emblem Engine

V9 is the robust emblem replacement engine for Word images.

### Main improvements over V8
- Multi-scale detection from ~0.10x to 3.20x of the uploaded sample.
- Does not aggressively downscale small Word images; tiny emblems are retained.
- Tolerates white, near-white, black and transparent sample backgrounds.
- Combines masked color matching, grayscale correlation, edge correlation and contrast checks.
- SIFT/ORB fallback is automatically used for rotated/warped/compressed copies.
- A sample can be **emblem only** or **emblem + Telegram mark**. The selected block is what gets replaced.
- Old block is inpainted before the new image is placed.
- CRC-tolerant DOCX media reading remains per-image, so one damaged media item does not stop the batch.

### Recommended settings
- Minimal confidence: 50–60% for tiny logos; raise it if false positives appear.
- Old cleanup padding: 6–10% for emblem-only samples, 8–14% for emblem+Telegram samples.
- Keep the replacement PNG transparent when possible.
