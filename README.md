# Reader Watermark V5 — CRC-safe Word Preview

## Asosiy tuzatishlar
- DOCX yuborilganda bot ostida rasm oqimidagidek `🔍 Tekshirish` Web tugmasi chiqadi.
- Word fayli Streamlit Web App'ga session orqali ochiladi.
- DOCX ichidagi `word/media/*` rasmlari CRC-32 xatosi bo'lsa ham raw ZIP local-header orqali o'qishga uriniladi.
- `image14.jpeg` kabi bitta CRC xatosi boshqa sog'lom rasmlarning Preview'ini to'xtatmaydi.
- Preview faqat haqiqatan o'qiladigan rasmdan yaratiladi va CRC muammosi alohida ogohlantiriladi.
- Watermark Word ichidagi rasmlarning o'ziga qo'llanadi.
- Yakuniy DOCX qayta tekshiriladi va CRC-tiklangan rasmlar hisobot qilinadi.

## Muhim environment variables
- `BOT_TOKEN`
- `WEBAPP_BASE_URL`
- ixtiyoriy: `SUPABASE_URL`, `SUPABASE_KEY`, `SESSION_STORE_DIR`, `GROQ_API_KEY`

## Test
`test_bad.docx` ichidagi `word/media/image14.jpeg` stale CRC bilan sinovdan o'tkazildi: tolerant extractor rasmni o'qidi, Preview uchun ishlatishi mumkin va watermark engine `repaired=1` qaytardi.


## 🖼️ PRO Rasm almashtirish — Telegram ZIP oqimi

- Word fayl ostidagi **🖼️ Rasm almashtirish** tugmasi endi callback orqali ishlaydi.
- ZIP **saytga yuklanmaydi**: foydalanuvchi uni bevosita Telegram chatga yuboradi.
- Bot ZIPning o'zini DBga saqlamaydi; Telegram `file_id`, nomi va vaqtini saqlaydi.
- Keyingi Word faylda avvalgi ZIP bo'lsa **📦 Avval yuklangan ZIP'dan foydalanish** tugmasi chiqadi.
- Web sahifa ZIPni Telegramdan olib, ichidagi PNG/JPG/JPEG/WEBP/BMP/TIFF rasmlarni ochadi va `🔍 Tekshirish` orqali barcha rasmlarni o'xshashlik bo'yicha saralaydi.
- `VALID_IMAGE_EXT` web_app.py ichida aniq e'lon qilingan; shu sababli oldingi `VALID_IMAGE_EXT is not defined` xatosi yo'q.
- **WebP** rasm sifatida qo'llab-quvvatlanadi. **WebM** esa video konteyner bo'lgani uchun rasm ro'yxatiga kiritilmaydi.
- Supabase ishlatilsa, bir marta quyidagi migrationni bajaring: `supabase_migration_image_zip.sql`.

> Eslatma: Telegram Cloud Bot API orqali faylni bot serveri qayta yuklab olishi uchun amaldagi download limiti hisobga olinadi. Juda katta ZIPlar (masalan 66.8 MB) uchun local Bot API server yoki kichikroq ZIP kerak bo'lishi mumkin.


### Rasm ZIP cache (PRO)
Rasm almashtirish uchun ZIP Telegram chat orqali yuboriladi. Bot ZIPni qabul qilishi bilan shu Python process RAMida saqlaydi va ichidagi barcha haqiqiy rasmlarni ochib tayyorlaydi. Streamlit ZIPni browser orqali qabul qilmaydi va Telegramdan qayta yuklamaydi. Yangi ZIP yuborilganda eski RAM cache ustiga yoziladi; server/process restart bo'lsa cache tozalanadi.
