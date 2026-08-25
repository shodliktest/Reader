# WATERMARK / EMBLEM PRO V8

## V8 changes
- Emblem replacement is a separate Web page from Watermark settings.
- Old sample can be uploaded OR selected directly from any readable Word/test image with a crop box.
- The selected sample defines the replacement unit:
  - emblem only -> only emblem is detected/replaced;
  - emblem + Telegram mark together -> the complete block is detected/replaced.
- Transparent PNG and black/white background samples are handled more safely.
- Automatic background trimming for uploaded/cropped samples.
- Masked multi-scale template matching for small logos that move to different positions and sizes.
- SIFT/ORB fallback for harder cases.
- Before placing the new logo, the detected old block is cleaned with inpainting to avoid leaving the old Telegram mark around the new logo.
- New logo can preserve aspect ratio or be stretched to the detected block.
- Controls for size, opacity, minimum confidence and cleanup padding.
- Preview and scan-all report show detected position, size, confidence and method.
- DOCX processing remains CRC-tolerant: one broken media image does not stop the remaining images.
- Rebuilt DOCX ZIP entries have corrected CRC/size metadata.

## Run
```bash
pip install -r requirements.txt
streamlit run web_app.py
```

Set `BOT_TOKEN`, `WEBAPP_BASE_URL` and other existing secrets as before.
