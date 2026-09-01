# Reader — V12 PRO Emblem Engine

This version is based on the working V11 project and fixes the two failures observed in production: tiny black-matte samples and the `/tmp/..._emblem_output.docx` missing-file race.

## V12 fixes

- **Tiny 18–30 px emblems:** a raw-canvas fallback preserves the original black/white matte when that matte is actually present in the target.
- **White-page samples:** the normal trimmed pass removes white/black/transparent matte and matches the emblem footprint rather than the page background.
- **Separate vs combined sample:** emblem-only crops replace only the emblem; emblem + Telegram crops replace the complete selected block.
- **CRC tolerance:** each `word/media/*` entry is processed independently; stale CRC metadata is rewritten.
- **One bad image:** a failed image does not abort the remaining Word images.
- **DOCX output race fixed:** the web finalizer now processes the DOCX in memory and sends the resulting bytes directly to Telegram, so it no longer depends on a shared `/tmp` output filename.
- **Atomic path wrapper:** the legacy file-based function writes through a temporary file and `os.replace()`.
- **Faster scanning:** fewer candidate maxima and optimized saturation preprocessing reduce unnecessary work while retaining the SIFT/ORB fallback.
- **Regression tests:** tiny black-matte detection/replacement and normal emblem tests pass.

## Important limitation

No computer-vision algorithm can guarantee recognition after arbitrary destruction, severe blur, or when the emblem has been completely removed. The manual crop workflow remains available as the deterministic fallback.


## Rasm almashtirish — yangi modul

- Word yuborilgandan keyin `🖼️ Rasm almashtirish` tugmasi chiqadi.
- Word ichidagi rasm tanlanadi va yangi rasm yuklanadi.
- Eski va yangi rasm OLD/NEW ko‘rinishida yonma-yon preview qilinadi.
- Noto‘g‘ri bo‘lsa Word ichidagi vizual jihatdan o‘xshash boshqa rasmlar reytingi ko‘rsatiladi.
- Tasdiqlangandan keyin faqat tanlangan `word/media/*` rasmi almashtiriladi.
- Yangilangan DOCX Telegramga qaytariladi.
