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
