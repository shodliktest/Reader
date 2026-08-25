# V6 — Automatic Emblem Replacement for Word Images

## Nima qo'shildi

- DOCX ichidagi barcha `word/media/*` rasmlar avtomatik topiladi.
- Foydalanuvchi **eski emblema namunasini** va **yangi emblemani** yuklaydi.
- Emblema rasmlarning turli joylarida bo'lsa ham topishga urinadi.
- SIFT/ORB feature matching + homography va multi-scale template matching ishlatiladi.
- Topilgan emblema markazi saqlanadi; yangi emblemaga tanlangan `+%` o'lcham beriladi.
- PNG alpha/shaffoflik saqlanadi.
- Har bir rasm alohida ishlanadi: bitta rasm topilmasa qolganlari davom etadi.
- DOCX media CRC noto'g'ri bo'lsa, tolerant ZIP o'qish ishlatiladi.
- Yakuniy DOCX ZIP va `python-docx` bilan tekshiriladi.
- Web App'da bitta rasm preview, barcha rasmlarni scan qilish va yakuniy Word'ga qo'llash mavjud.
- Telegram'da Word fayl ostida bitta `🔍 Tekshirish` tugmasi qoladi; shu tugma mavjud Web App'ni ochadi.

## Ishlatish

1. Telegram botga `.docx` yuboring.
2. Word fayli ostidagi **🔍 Tekshirish** tugmasini bosing.
3. Web App'da:
   - Eski emblema namunasini yuklang. Agar eski watermark Telegram belgisi + dumaloq emblema guruhidan iborat bo'lsa, namuna ikkalasini ham qamrab olsin.
   - Yangi emblemangizni yuklang (PNG tavsiya etiladi).
   - `O'lcham`ni tanlang, masalan `+8%`.
   - `Minimal ishonch`ni odatda `58–70%` oralig'ida qoldiring.
   - Avval **Emblemani topish** yoki **Barcha rasmlarda emblemani tekshirish**ni bosing.
   - Preview'da natijani ko'ring.
   - **🏷️ Emblemani BARCHA Word rasmlariga qo'llash**ni bosing.

## Muhim

Avtomatik detector juda o'xshash logolar, juda kichik/xira tasvirlar yoki emblemaga boshqa obyekt yopishib qolgan holatlarda ishonchni pasaytirishi mumkin. Bunday rasmlar yakuniy hisobotda `topilmadi` sifatida ko'rsatiladi; ular noto'g'ri joyga bo'yab yuborilmasligi uchun konservativ threshold ishlatiladi.

## CRC

`Bad CRC-32` faqat markaziy ZIP katalogidagi checksum noto'g'ri bo'lsa, media bytesni tolerant usulda o'qish orqali chetlab o'tiladi. Haqiqiy media bytesning o'zi buzilgan bo'lsa, uni tiklash kafolatlanmaydi.
