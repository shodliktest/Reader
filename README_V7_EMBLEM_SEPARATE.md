# V7 — Separate Emblem Web Page

## What changed

When a Word file is uploaded **or a Word file is generated from test images**, the Telegram document now has two independent buttons:

- `🔍 Tekshirish` → `?page=watermark`
- `🏷️ Emblema almashtirish` → `?page=emblem`

The two tools are intentionally separated.

## Emblem source modes

The Emblem page supports two ways to define the old emblem:

1. **Upload an old-emblem image**.
2. **Select/crop the old emblem directly from one of the Word/test images**, using `streamlit-cropper` like a normal image crop tool.

If the Telegram icon and round emblem must be replaced together, crop both as one template.

## Detection

The selected template is searched independently in every readable Word image using feature matching (SIFT/ORB where available) plus multi-scale template matching. The emblem can be in different positions and at different scales.

One failed detection does not stop the rest of the document.

## CRC tolerance

DOCX media is read with the tolerant ZIP reader from the V5/V6 pipeline. A CRC-bad image is repaired when possible; unreadable images are skipped while other images continue.

## Deployment

Keep these dependencies in `requirements.txt`:

- `streamlit-cropper`
- `opencv-python-headless`
- `Pillow`
- `numpy`
- `python-docx`
- `aiogram`
- `streamlit`
- `requests`

Set `BOT_TOKEN` and `WEBAPP_BASE_URL` in Streamlit Secrets.
