"""
bot.py
------
YHQ test-rasm to'plovchi Telegram bot (aiogram 3.x).

Ishlash tartibi:
1. Foydalanuvchi botga rasmlarni birma-bir (yoki bir nechtasini bir vaqtda,
   Telegram "albom" sifatida) yuboradi, YOKI bitta .zip fayl yuboradi (ichida
   rasmlar bo'lgan holda).
2. Har bir rasm qabul qilinganda bot "📷 N ta rasm qabul qilindi" deb javob
   beradi, ostida doim ikkita tugma turadi: "✅ Tugatish (N)" va "❌ Bekor qilish".
3. "✅ Tugatish" bosilganda:
   - Bot barcha rasmlarni OCR+Groq orqali qayta ishlaydi (processor.py)
   - Natijalarni session_store'ga (RAM) yozadi
   - Foydalanuvchiga "🔍 Tekshirish" nomli WebApp tugmasini yuboradi -
     bosilganda Streamlit Mini App (web_app.py) o'sha sessiya bilan ochiladi
4. Streamlit'da foydalanuvchi natijalarni tahrirlaydi va "Word yaratish"
   tugmasini bosadi - bu ALOHIDA (bot API orqali to'g'ridan-to'g'ri) tayyor
   Word faylni o'sha Telegram chatga yuboradi (qarang: web_app.py'dagi
   send_docx_to_telegram() funksiyasi).
5. "❌ Bekor qilish" bosilganda - sessiya butunlay o'chiriladi, hech narsa
   yaratilmaydi.

MUHIM SOZLASH:
- BOT_TOKEN muhit o'zgaruvchisini albatta o'rnating.
- WEBAPP_BASE_URL muhit o'zgaruvchisini Streamlit ilovangiz manziliga
  o'rnating (masalan: https://sizning-ilova.streamlit.app). Bu HTTPS
  bo'lishi SHART - Telegram Mini App faqat https bilan ishlaydi.
"""

import os
import sys
import io
import asyncio
import logging
import zipfile
from datetime import datetime

# MUHIM: bot.py Streamlit'ning fon-thread'i ichida ishga tushirilganda
# (run_in_background() orqali), ba'zi hosting muhitlarida (masalan Streamlit
# Cloud'ning uv-asosli ishga tushirish jarayoni) shu faylning o'zi joylashgan
# papka sys.path'da bo'lmasligi mumkin - natijada "processor" va boshqa
# lokal modullarni import qilishda ModuleNotFoundError chiqadi. Buni
# oldini olish uchun o'z papkamizni qo'lda, aniq ravishda sys.path'ga
# qo'shib qo'yamiz (agar allaqachon bo'lmasa).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, FSInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import session_store
from processor import process_single_image
from docx_builder import build_docx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
WEBAPP_BASE_URL = os.environ.get('WEBAPP_BASE_URL', '').rstrip('/')

if not WEBAPP_BASE_URL:
    logger.warning(
        "OGOHLANTIRISH: WEBAPP_BASE_URL o'rnatilmagan. "
        "'Tekshirish' tugmasi ishlamaydi, to'g'ridan-to'g'ri Word yaratiladi."
    )

# MUHIM: bu faylni web_app.py ham import qiladi (fon-thread sifatida botni
# ishga tushirish uchun) - shuning uchun BOT_TOKEN yo'q bo'lsa ham import
# paytida darhol xato bermaymiz (aks holda butun Streamlit sahifasi
# ochilmay qoladi). Xato faqat botni HAQIQATDA ishga tushirishga (main())
# urinilganda ko'tariladi.
bot = None
dp = Dispatcher()

if BOT_TOKEN:
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

VALID_IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp')

# Foydalanuvchining hozirgi FAOL sessiya ID'sini eslab turish uchun (chat_id -> session_id).
# Bu RAM'da, chunki har bir foydalanuvchi bir vaqtning o'zida faqat bitta faol
# to'plash jarayoniga ega bo'lishi kerak (ikkinchisini boshlasa, avvalgisi almashtiriladi).
_active_sessions = {}


def collecting_keyboard(count):
    """Rasm to'plash jarayonida ko'rsatiladigan inline tugmalar."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"✅ Tugatish ({count})", callback_data="finish"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel"),
        ]
    ])


def review_keyboard(session_id):
    """Qayta ishlangandan keyin - Streamlit Mini App'ga o'tish tugmasi."""
    if not WEBAPP_BASE_URL:
        return None
    url = f"{WEBAPP_BASE_URL}/?session_id={session_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Tekshirish", web_app=WebAppInfo(url=url))]
    ])


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Salom! 👋\n\n"
        "Menga YHQ test skrinshotlarini (rasm sifatida, bir nechtasini birma-bir) "
        "yoki ichida rasmlar bo'lgan <b>.zip</b> faylni yuboring.\n\n"
        "Barcha rasmlarni yuborib bo'lgach, <b>✅ Tugatish</b> tugmasini bosing - "
        "men ularni qayta ishlab, tekshirish uchun sizga havola yuboraman."
    )


def _get_or_create_session(chat_id, user_id):
    session_id = _active_sessions.get(chat_id)
    if session_id:
        data = session_store.get_session(session_id)
        if data and data.get("status") == "collecting":
            return session_id, data
    # Yangi sessiya
    session_id = session_store.new_session_id()
    data = session_store.create_session(session_id, user_id, chat_id)
    _active_sessions[chat_id] = session_id
    return session_id, data


async def _add_image_bytes_to_session(session_id, image_bytes, filename_hint=""):
    data = session_store.get_session(session_id)
    if data is None:
        return None
    images = data.get("images", [])
    import base64
    images.append({
        "name": filename_hint or f"image_{len(images) + 1}.jpg",
        "b64": base64.b64encode(image_bytes).decode('ascii'),
    })
    session_store.update_session(session_id, images=images)
    return len(images)


@dp.message(F.photo)
async def handle_photo(message: Message):
    """Foydalanuvchi rasmni to'g'ridan-to'g'ri (siqilgan) rasm sifatida yuborsa."""
    chat_id = message.chat.id
    session_id, data = _get_or_create_session(chat_id, message.from_user.id)

    photo = message.photo[-1]  # eng katta o'lchamdagi versiya
    file = await bot.get_file(photo.file_id)
    buf = io.BytesIO()
    await bot.download_file(file.file_path, destination=buf)

    count = await _add_image_bytes_to_session(session_id, buf.getvalue(), f"{photo.file_id}.jpg")
    await message.answer(
        f"📷 {count} ta rasm qabul qilindi.",
        reply_markup=collecting_keyboard(count),
    )


@dp.message(F.document)
async def handle_document(message: Message):
    """Foydalanuvchi rasmni FAYL sifatida (siqilmagan) yoki .zip fayl yuborsa."""
    chat_id = message.chat.id
    session_id, data = _get_or_create_session(chat_id, message.from_user.id)

    doc = message.document
    fname = (doc.file_name or "").lower()

    file = await bot.get_file(doc.file_id)
    buf = io.BytesIO()
    await bot.download_file(file.file_path, destination=buf)
    file_bytes = buf.getvalue()

    if fname.endswith('.zip'):
        await message.answer("📦 Zip fayl qabul qilindi, ichidagi rasmlar ajratilmoqda...")
        added = 0
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
                names = sorted(zf.namelist())
                for name in names:
                    if name.lower().endswith(VALID_IMAGE_EXT) and not name.startswith('__MACOSX'):
                        img_bytes = zf.read(name)
                        await _add_image_bytes_to_session(session_id, img_bytes, os.path.basename(name))
                        added += 1
        except zipfile.BadZipFile:
            await message.answer("❌ Bu fayl to'g'ri zip fayl emas, qaytadan urinib ko'ring.")
            return

        # Zip fayl nomini standart Word-fayl-nomi sifatida saqlaymiz
        zip_basename = os.path.splitext(os.path.basename(doc.file_name))[0]
        session_store.update_session(session_id, default_filename=zip_basename)

        data = session_store.get_session(session_id)
        total = len(data.get("images", []))
        await message.answer(
            f"✅ Zip fayldan {added} ta rasm qo'shildi. Jami: {total} ta rasm.",
            reply_markup=collecting_keyboard(total),
        )

    elif fname.endswith(VALID_IMAGE_EXT):
        count = await _add_image_bytes_to_session(session_id, file_bytes, doc.file_name)
        await message.answer(
            f"📷 {count} ta rasm qabul qilindi.",
            reply_markup=collecting_keyboard(count),
        )
    else:
        await message.answer("⚠️ Faqat rasm fayllari yoki .zip qabul qilinadi.")


@dp.callback_query(F.data == "cancel")
async def handle_cancel(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    session_id = _active_sessions.pop(chat_id, None)
    if session_id:
        session_store.clear_session(session_id)
    await callback.message.edit_text("❌ Bekor qilindi. To'plangan rasmlar o'chirildi.")
    await callback.answer()


@dp.callback_query(F.data == "finish")
async def handle_finish(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    session_id = _active_sessions.get(chat_id)
    data = session_store.get_session(session_id) if session_id else None

    if not data or not data.get("images"):
        await callback.answer("Hali birorta ham rasm yuborilmagan.", show_alert=True)
        return

    await callback.message.edit_text(
        f"⏳ {len(data['images'])} ta rasm qayta ishlanmoqda... Bu bir necha daqiqa vaqt olishi mumkin."
    )
    await callback.answer()

    session_store.update_session(session_id, status="processing")

    # Qayta ishlashni fon vazifasi sifatida ishga tushiramiz - shunda bot
    # boshqa foydalanuvchilarga xizmat qilishda davom etadi.
    asyncio.create_task(_process_session_and_notify(session_id, chat_id))


async def _process_session_and_notify(session_id, chat_id):
    """Sessiyadagi barcha rasmlarni qayta ishlaydi va natijani xabar qiladi."""
    import base64
    from PIL import Image

    data = session_store.get_session(session_id)
    if data is None:
        return

    questions = []
    total = len(data["images"])

    for i, img_entry in enumerate(data["images"], start=1):
        img_bytes = base64.b64decode(img_entry["b64"])
        pil_img = Image.open(io.BytesIO(img_bytes))
        result = process_single_image(pil_img, use_groq=True)
        questions.append({
            "source_image": img_entry["name"],
            "question": result.get("question", ""),
            "options": result.get("options", []),
            "correct_index": result.get("correct_index"),
            "success": result.get("success", False),
            "error": result.get("error"),
        })

        # Har 5 ta rasmda bir marta progress xabarini yangilaymiz (juda tez-tez
        # yubormaslik uchun - Telegram rate-limit qo'yishi mumkin)
        if i % 5 == 0 or i == total:
            try:
                await bot.send_message(chat_id, f"⏳ Qayta ishlandi: {i}/{total}")
            except Exception:
                pass

    session_store.update_session(session_id, questions=questions, status="ready_for_review", images=[])

    success_count = sum(1 for q in questions if q["success"])
    summary = (
        f"✅ Qayta ishlash tugadi!\n"
        f"Muvaffaqiyatli: {success_count}/{total}\n\n"
    )

    kb = review_keyboard(session_id)
    if kb:
        summary += "Natijalarni tekshirish va Word faylni yaratish uchun pastdagi tugmani bosing:"
        await bot.send_message(chat_id, summary, reply_markup=kb)
    else:
        # WEBAPP_BASE_URL sozlanmagan bo'lsa - to'g'ridan-to'g'ri Word yaratib yuboramiz
        out_path = f"/tmp/{session_id}.docx"
        build_docx(questions, out_path, title=data.get("default_filename", "Test Savollari"))
        await bot.send_document(chat_id, FSInputFile(out_path, filename=f"{data.get('default_filename','natija')}.docx"))
        session_store.clear_session(session_id)


async def main():
    if bot is None:
        raise RuntimeError(
            "BOT_TOKEN muhit o'zgaruvchisi (yoki Streamlit Secrets) topilmadi - "
            "botni ishga tushirib bo'lmaydi."
        )
    # handle_signals=False - bu funksiya asosiy thread'dan emas, balki
    # Streamlit background thread'idan chaqirilishi mumkin (qarang: run_in_background()
    # pastda). aiogram signal-handlerlarni faqat asosiy thread'da o'rnatishi mumkin,
    # shuning uchun thread ichida ishlatilganda buni o'chirib qo'yish SHART -
    # aks holda "signal only works in main thread" xatosi chiqadi.
    await dp.start_polling(
        bot,
        drop_pending_updates=True,
        handle_signals=False,
    )


# ── Streamlit uchun fon-thread orqali ishga tushirish ──────────────────────
#
# CloneBot/QuizMakerBot loyihalaringizdagi bilan bir xil naqsh:
# streamlit_app.py o'zining eng boshida (import bosqichida, @st.cache_resource
# bilan bezalgan funksiya orqali) shu run_in_background()ni chaqiradi.
#
# Ikki qatlamli himoya orqali botning bir nechta nusxasi bir vaqtda
# ishlab, Telegram'ga "Conflict: terminated by other getUpdates request"
# xatosini bermasligi ta'minlanadi:
#   1) @st.cache_resource (web_app.py tomonida) - Streamlit rerun bo'lganda
#      bu funksiya QAYTA chaqirilmaydi, faqat birinchi marta ishlaydi.
#   2) OS darajasidagi fayl-lock (fcntl.flock) - agar biror sabab bilan
#      ikkita ALOHIDA Python jarayoni (masalan Streamlit ikki marta qayta
#      ishga tushirilsa - "cold start") bir vaqtda ishga tushishga urinsa,
#      ikkinchisi lock ololmay, jimgina bosh tortadi.
_bot_thread = None
_BOT_LOCK_FILE = os.path.join(
    os.environ.get('SESSION_STORE_DIR') or __import__('tempfile').gettempdir(),
    "yhq_bot.lock",
)


def run_in_background():
    """web_app.py tomonidan chaqiriladi (Streamlit ilova ochilganda).
    Botni fon-thread'da ishga tushiradi va shu thread obyektini qaytaradi."""
    global _bot_thread
    import threading
    import fcntl

    if _bot_thread is not None and _bot_thread.is_alive():
        logger.info("Bot thread allaqachon ishlayapti - qayta ishga tushirilmadi")
        return _bot_thread

    if bot is None:
        logger.error("BOT_TOKEN yo'q - bot ishga tushirilmadi")
        return None

    # Eskirib qolgan lock faylni tozalash: agar lock faylda yozilgan PID
    # endi tizimda ishlamayotgan bo'lsa (masalan Streamlit Cloud oldingi
    # jarayonni kutilmagan tarzda o'chirgan bo'lsa - "finally" bloki
    # ishlamay qolgan holat), bu eski lock faylni avtomatik o'chiramiz.
    # Aks holda bot QAYTA HECH QACHON ishga tushmay qoladi (doimiy
    # "Conflict" holatiga o'xshab ko'rinadi, aslida faqat eski lock qoldig'i).
    if os.path.exists(_BOT_LOCK_FILE):
        try:
            with open(_BOT_LOCK_FILE, "r") as f:
                old_pid = int((f.read() or "0").strip())
            if old_pid and old_pid != os.getpid():
                try:
                    os.kill(old_pid, 0)  # jarayon hali tirikmi - signal yubormaydi, faqat tekshiradi
                except OSError:
                    # Jarayon o'lik - eski lock qoldiq, xavfsiz o'chiramiz
                    os.unlink(_BOT_LOCK_FILE)
                    logger.info("Eskirgan bot lock fayli tozalandi (egasi jarayon endi yo'q)")
        except Exception:
            pass

    try:
        lock_fd = open(_BOT_LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        logger.info(f"Bot lock olindi: {_BOT_LOCK_FILE}")
    except BlockingIOError:
        logger.warning("Boshqa bot instance allaqachon ishlayapti - bu nusxa ishga tushmaydi")
        return None
    except Exception as e:
        logger.warning(f"Lock fayl bilan muammo (davom etamiz): {e}")
        lock_fd = None

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(main())
        except Exception as e:
            logger.error(f"Bot thread xatolik bilan to'xtadi: {e}")
        finally:
            try:
                loop.close()
            except Exception:
                pass
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    lock_fd.close()
                    os.unlink(_BOT_LOCK_FILE)
                except Exception:
                    pass

    _bot_thread = threading.Thread(target=_run, daemon=True, name="TelegramBotPolling")
    _bot_thread.start()
    logger.info("Bot fon-thread'i ishga tushdi")
    return _bot_thread


if __name__ == "__main__":
    asyncio.run(main())
