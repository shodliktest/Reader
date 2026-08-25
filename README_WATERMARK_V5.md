# Watermark PRO V5 — CRC-safe Word Preview

## Muhim tuzatish
Word ichidagi `word/media/image14.jpeg` kabi CRC-32 noto'g'ri bo'lgan media fayli Preview'ni butunlay to'xtatmaydi.

### Nima o'zgardi
- ZIP local-header orqali tolerant media o'qish qo'shildi.
- CRC noto'g'ri bo'lsa ham, siqilgan ma'lumot buzilmagan bo'lsa rasm o'qiladi.
- DOCX Preview barcha rasmlarni ko'rib chiqadi va birinchi o'qiladigan rasmni tanlaydi.
- Birinchi rasm o'qilmasa, keyingi rasmga avtomatik o'tadi.
- Web Preview ham bitta buzilgan rasm sabab butunlay to'xtamaydi.
- `watermark_docx.last_report` endi `repaired`, `failed_media`, `changed` hisobotini to'g'ri beradi.
- Yakuniy DOCX `zipfile.testzip()` bilan tekshirilganda CRC xatosiz qayta yig'iladi.

## Ishlatish
Eski loyihadagi fayllarni shu ZIP ichidagi fayllar bilan almashtiring va Streamlit/hostingni restart qiling.
