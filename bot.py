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
import html
import base64
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
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import session_store
from processor import process_single_image
from docx_builder import build_docx
from watermark import DEFAULT_SETTINGS, STYLE_LABELS, FONT_LABELS, normalize_settings, watermark_docx, preview_bytes, extract_docx_media_tolerant, extract_docx_media, extract_docx_media_tolerant

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

# Har bir chat uchun watermark sozlamalari. Sozlamalar sessiyaga ham nusxalanadi,
# shuning uchun Streamlit preview/generator aynan shu tanlovlardan foydalanadi.
_watermark_settings = {}
_wm_text_mode = set()

# Word fayllar uchun vaqtinchalik navbat: chat_id -> original DOCX ma'lumotlari.
# Fayl yuborilganda darhol qayta ishlanmaydi; foydalanuvchi avval tugmalar orqali
# "Tekshirish", "Preview" yoki "Watermark sozlamalari"ni tanlaydi.
_pending_docx = {}


def get_watermark_settings(chat_id):
    return normalize_settings(_watermark_settings.get(chat_id, DEFAULT_SETTINGS))


def watermark_settings_keyboard(chat_id):
    s = get_watermark_settings(chat_id)
    enabled = "ON 🟢" if s["enabled"] else "OFF 🔴"
    style = STYLE_LABELS.get(s["style"], s["style"])
    font = FONT_LABELS.get(s["font"], s["font"])
    text_short = s["text"] if len(s["text"]) <= 18 else s["text"][:18] + "…"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💧 Watermark: {enabled}", callback_data="wm_toggle")],
        [
            InlineKeyboardButton(text=f"📝 {text_short}", callback_data="wm_text"),
            InlineKeyboardButton(text=f"🎨 {style}", callback_data="wm_style"),
        ],
        [
            InlineKeyboardButton(text=f"👻 {s['opacity']}%", callback_data="wm_opacity"),
            InlineKeyboardButton(text=f"🔠 {s['size']}px", callback_data="wm_size"),
        ],
        [
            InlineKeyboardButton(text=f"🔤 {font}", callback_data="wm_font"),
            InlineKeyboardButton(text=f"📐 {s['angle']}°", callback_data="wm_angle"),
        ],
        [InlineKeyboardButton(text=f"🎨 Rang {s['color']}", callback_data="wm_color")],
        [InlineKeyboardButton(text="👁️ Preview", callback_data="wm_preview")],
    ])


def docx_web_keyboard(session_id):
    """Word fayli ostida ikkita MUSTAQIL Web sahifa tugmasi."""
    if not WEBAPP_BASE_URL:
        return None
    base = f"{WEBAPP_BASE_URL}/?session_id={session_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Tekshirish", url=f"{base}&page=watermark")],
        [InlineKeyboardButton(text="🏷️ Emblema almashtirish", url=f"{base}&page=emblem")],
        [InlineKeyboardButton(text="🖼️ Rasm almashtirish", url=f"{base}&page=image_replace")],
    ])


def docx_keyboard(chat_id):
    """Legacy callback keyboard; Web sahifalar alohida va mustaqil."""
    pending = _pending_docx_info(chat_id)
    if not pending or not WEBAPP_BASE_URL:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="docx_cancel")]
        ])
    return docx_web_keyboard(pending.get("session_id")) or InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="docx_cancel")]
    ])


def _pending_docx_info(chat_id):
    return _pending_docx.get(chat_id)


async def _send_docx_preview(message, chat_id):
    """Word ichidagi birinchi o'qiladigan/tiklanadigan rasmni preview qiladi.

    CRC xatosi bo'lgan bitta rasm boshqa rasmlarni to'xtatmaydi. Masalan,
    image14.jpeg buzilgan bo'lsa, image1-image13 yoki undan keyingi sog'lom
    rasm preview uchun ishlatiladi.
    """
    pending = _pending_docx_info(chat_id)
    if not pending:
        await message.answer("⚠️ Avval .docx fayl yuboring.")
        return

    settings = get_watermark_settings(chat_id)
    raw = pending["bytes"]
    try:
        media = extract_docx_media(raw)
        if not media:
            await message.answer("ℹ️ Word fayl ichida preview qilish uchun rasm topilmadi.")
            return

        repaired = sum(1 for x in media if x.get("repaired"))
        failed = [x for x in media if not x.get("data")]
        preview_data = None
        preview_name = None

        for item in media:
            data = item.get("data")
            if not data:
                continue
            try:
                preview_data = preview_bytes(data, settings)
                preview_name = item["name"]
                break
            except Exception as exc:
                item["error"] = str(exc)
                continue

        if preview_data is None:
            await message.answer(
                "❌ Preview uchun Word ichidagi rasmlarning hech birini o'qib bo'lmadi.\n"
                f"🖼 Jami: {len(media)} ta\n"
                f"⚠️ Tiklanmagan: {len(failed)} ta"
            )
            return

        from aiogram.types import BufferedInputFile
        note = [
            "👁️ <b>Watermark Preview</b>",
            f"🖼 Rasm: <code>{html.escape(preview_name or '')}</code>",
            f"📝 {html.escape(settings['text'])}",
            f"🎨 {STYLE_LABELS.get(settings['style'], settings['style'])}",
            f"👻 Shaffoflik: {settings['opacity']}%",
        ]
        if repaired:
            note.append(f"♻️ CRC tiklangan: {repaired} ta")
        if failed:
            note.append(f"⚠️ O'qilmagan rasm: {len(failed)} ta — boshqa rasmlar preview uchun ishlatildi")

        await message.answer_photo(
            BufferedInputFile(preview_data, filename="watermark_preview.png"),
            caption="\n".join(note),
        )
    except Exception as e:
        logger.exception("DOCX preview xatosi")
        await message.answer(f"❌ Preview qilishda xatolik: {html.escape(str(e))}")


async def _process_pending_docx(chat_id, message):
    """Tanlangan watermark sozlamalari bilan pending DOCX ni qayta ishlaydi."""
    pending = _pending_docx_info(chat_id)
    if not pending:
        await message.answer("⚠️ Word fayl topilmadi. Faylni qaytadan yuboring.")
        return
    settings = get_watermark_settings(chat_id)
    if not settings["enabled"]:
        await message.answer("ℹ️ Watermark o'chirilgan. Avval 💧 Watermark tugmasidan yoqing.", reply_markup=docx_keyboard(chat_id))
        return
    in_path = f"/tmp/{chat_id}_{pending['file_id']}_original.docx"
    out_path = f"/tmp/{chat_id}_{pending['file_id']}_watermarked.docx"
    try:
        with open(in_path, "wb") as f:
            f.write(pending["bytes"])
        await message.answer("💧 Word ichidagi rasmlar tekshirilmoqda va tanlangan watermark qo'shilmoqda...")
        _, changed = watermark_docx(in_path, out_path, settings)
        report = getattr(watermark_docx, "last_report", {}) or {}
        repaired = int(report.get("repaired", 0) or 0)
        failed_media = report.get("failed_media", []) or []
        status = [f"✅ Tekshiruv va watermark tugadi! {changed} ta rasm qayta ishlandi."]
        if repaired:
            status.append(f"♻️ {repaired} ta rasmning CRC muammosi tuzatildi.")
        if failed_media:
            status.append(f"⚠️ {len(failed_media)} ta rasmni tiklab bo'lmadi.")
        status.append(f"🎨 Uslub: {STYLE_LABELS.get(settings['style'], settings['style'])} • 👻 {settings['opacity']}% • 📝 {html.escape(settings['text'])}")
        await message.answer("\n".join(status), reply_markup=docx_keyboard(chat_id))
        await message.answer_document(FSInputFile(out_path, filename=f"watermarked_{pending['filename']}"))
    except Exception as e:
        logger.exception("DOCX watermark xatosi")
        await message.answer(f"❌ Word faylni qayta ishlashda xatolik: {html.escape(str(e))}", reply_markup=docx_keyboard(chat_id))
    finally:
        for path in (in_path, out_path):
            try:
                if os.path.exists(path): os.remove(path)
            except Exception: pass


def collecting_keyboard(count):
    """Rasm to'plash jarayonida ko'rsatiladigan inline tugmalar."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"✅ Tugatish ({count})", callback_data="finish"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel"),
        ]
    ])


def review_keyboard(session_id):
    """Qayta ishlangandan keyin - tahrirlash sahifasiga o'tish tugmasi.

    MUHIM: bu ataylab oddiy `url=` tugma (Telegram Mini App / WebAppInfo EMAS).
    Mini App rejimida Telegram o'zining tor WebView'ini ochadi, u yerda
    ekran eni juda cheklangan bo'lib, Streamlit'ning `st.columns` responsive
    xatti-harakati (tor kolonkalarni avtomatik vertikal stackka o'tkazishi)
    tugmalarni bir-birining ustiga chiqarib yuborardi. Oddiy `url=` tugma esa
    havolani qurilmaning ODATIY brauzerida (Chrome/Safari) ochadi - u yerda
    ekran kengligi WebView'dagidan farqli hisoblanadi va bu muammo yo'q.
    Sessiya avvalgidek `?session_id=` query parametri orqali ishlaydi -
    Telegram initData ishlatilmagani uchun boshqa hech narsa o'zgarmaydi.
    """
    if not WEBAPP_BASE_URL:
        return None
    url = f"{WEBAPP_BASE_URL}/?session_id={session_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Tekshirish", url=url)]
    ])


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Salom! 👋\n\n"
        "Menga YHQ test skrinshotlarini (rasm sifatida, bir nechtasini birma-bir) "
        "yoki ichida rasmlar bo'lgan <b>.zip</b> faylni yuboring.\n\n"
        "Barcha rasmlarni yuborib bo'lgach, <b>✅ Tugatish</b> tugmasini bosing - "
        "men ularni qayta ishlab, tekshirish uchun sizga havola yuboraman.\n\n"
        "💧 Watermarkni sozlash: /watermark\n"
        "📄 Tayyor Word faylga ham watermark qo'yish mumkin - .docx faylni shu yerga yuboring."
    )


@dp.message(Command("watermark"))
async def cmd_watermark(message: Message):
    chat_id = message.chat.id
    await message.answer(
        "💧 <b>Rasm Watermark sozlamalari</b>\n\n"
        "Bu sozlamalar Word sahifasiga emas, Word ichidagi rasmlarning O'ZIGA qo'llanadi.\n"
        "Tayyor .docx yuborsangiz ham shu sozlamalar ishlaydi.",
        reply_markup=watermark_settings_keyboard(chat_id),
    )


@dp.callback_query(F.data == "wm_toggle")
async def wm_toggle(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    s = get_watermark_settings(chat_id)
    s["enabled"] = not s["enabled"]
    _watermark_settings[chat_id] = s
    await callback.message.edit_reply_markup(reply_markup=watermark_settings_keyboard(chat_id))
    await callback.answer("Watermark yoqildi." if s["enabled"] else "Watermark o'chirildi.")


@dp.callback_query(F.data == "wm_style")
async def wm_style(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    s = get_watermark_settings(chat_id)
    order = ["diagonal", "center", "pattern", "corner", "double", "stamp", "outline"]
    s["style"] = order[(order.index(s["style"]) + 1) % len(order)]
    _watermark_settings[chat_id] = s
    await callback.message.edit_reply_markup(reply_markup=watermark_settings_keyboard(chat_id))
    await callback.answer(f"Dizayn: {s['style']}")


@dp.callback_query(F.data == "wm_text")
async def wm_text(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    _wm_text_mode.add(chat_id)
    current = get_watermark_settings(chat_id)["text"]
    await callback.message.answer(
        f"✏️ Yangi watermark matnini yuboring.\n\nHozirgi: <code>{html.escape(current)}</code>\n\n"
        "Masalan: <code>© QUIZMAKER</code> yoki <code>@MyTestBot</code>"
    )
    await callback.answer()


@dp.message(F.text)
async def wm_text_input(message: Message):
    chat_id = message.chat.id
    if chat_id not in _wm_text_mode:
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("❌ Matn bo'sh bo'lishi mumkin emas. Qayta yuboring.")
        return
    s = get_watermark_settings(chat_id)
    s["text"] = text[:120]
    _watermark_settings[chat_id] = s
    _wm_text_mode.discard(chat_id)
    await message.answer("✅ Watermark matni saqlandi.", reply_markup=watermark_settings_keyboard(chat_id))


@dp.callback_query(F.data == "wm_opacity")
async def wm_opacity(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    s = get_watermark_settings(chat_id)
    levels = [10, 15, 18, 22, 28, 35]
    current = s["opacity"]
    try:
        idx = levels.index(current)
        s["opacity"] = levels[(idx + 1) % len(levels)]
    except ValueError:
        s["opacity"] = 18
    _watermark_settings[chat_id] = s
    await callback.message.edit_reply_markup(reply_markup=watermark_settings_keyboard(chat_id))
    await callback.answer(f"Shaffoflik: {s['opacity']}%")


@dp.callback_query(F.data == "wm_size")
async def wm_size(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    s = get_watermark_settings(chat_id)
    levels = [20, 24, 28, 32, 38, 46]
    current = s["size"]
    try:
        idx = levels.index(current)
        s["size"] = levels[(idx + 1) % len(levels)]
    except ValueError:
        s["size"] = 30
    _watermark_settings[chat_id] = s
    await callback.message.edit_reply_markup(reply_markup=watermark_settings_keyboard(chat_id))
    await callback.answer(f"Hajm: {s['size']}px")


@dp.callback_query(F.data == "wm_font")
async def wm_font(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    s = get_watermark_settings(chat_id)
    order = ["sans", "serif", "mono"]
    s["font"] = order[(order.index(s.get("font", "sans")) + 1) % len(order)]
    _watermark_settings[chat_id] = s
    await callback.message.edit_reply_markup(reply_markup=watermark_settings_keyboard(chat_id))
    await callback.answer(f"Shrift: {FONT_LABELS[s['font']]}")


@dp.callback_query(F.data == "wm_angle")
async def wm_angle(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    s = get_watermark_settings(chat_id)
    levels = [-45, -35, -20, 0, 20, 35, 45]
    current = s.get("angle", -35)
    s["angle"] = levels[(levels.index(current) + 1) % len(levels)] if current in levels else -35
    _watermark_settings[chat_id] = s
    await callback.message.edit_reply_markup(reply_markup=watermark_settings_keyboard(chat_id))
    await callback.answer(f"Burchak: {s['angle']}°")


@dp.callback_query(F.data == "wm_color")
async def wm_color(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    s = get_watermark_settings(chat_id)
    colors = ["#FFFFFF", "#000000", "#FFD700", "#00E5FF", "#FF4D6D", "#7CFF6B"]
    current = s.get("color", "#FFFFFF")
    s["color"] = colors[(colors.index(current) + 1) % len(colors)] if current in colors else colors[0]
    _watermark_settings[chat_id] = s
    await callback.message.edit_reply_markup(reply_markup=watermark_settings_keyboard(chat_id))
    await callback.answer(f"Rang: {s['color']}")


@dp.callback_query(F.data == "wm_preview")
async def wm_preview(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    s = get_watermark_settings(chat_id)
    session_id = _active_sessions.get(chat_id)
    data = session_store.get_session(session_id) if session_id else None
    if not data or not data.get("images"):
        await callback.answer("Preview uchun avval kamida bitta rasm yuboring.", show_alert=True)
        return
    try:
        import base64
        raw = base64.b64decode(data["images"][0]["b64"])
        out = preview_bytes(raw, s)
        await callback.message.answer_photo(
            __import__('aiogram').types.BufferedInputFile(out, filename="watermark_preview.png"),
            caption=f"👁️ Preview\nMatn: <b>{html.escape(s['text'])}</b>\nDizayn: <b>{html.escape(s['style'])}</b>\nShaffoflik: <b>{s['opacity']}%</b>",
        )
        await callback.answer()
    except Exception as e:
        await callback.answer(f"Preview xatosi: {e}", show_alert=True)


def _get_or_create_session(chat_id, user_id):
    session_id = _active_sessions.get(chat_id)
    if session_id:
        data = session_store.get_session(session_id)
        if data and data.get("status") == "collecting":
            return session_id, data
    # Yangi sessiya
    session_id = session_store.new_session_id()
    data = session_store.create_session(session_id, user_id, chat_id)
    session_store.update_session(session_id, watermark_settings=get_watermark_settings(chat_id))
    data = session_store.get_session(session_id)
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
    """Rasm, ZIP yoki tayyor DOCX faylni qabul qiladi."""
    chat_id = message.chat.id

    doc = message.document
    fname = (doc.file_name or "").lower()

    file = await bot.get_file(doc.file_id)
    buf = io.BytesIO()
    await bot.download_file(file.file_path, destination=buf)
    file_bytes = buf.getvalue()

    if fname.endswith('.docx'):
        # Word ham rasm kabi aynan bitta "🔍 Tekshirish" Web tugmasi orqali
        # mavjud Streamlit sahifasiga yuboriladi.  DOCX baytlari sessiyada
        # saqlanadi, Web App esa CRC-tolerant media extractor orqali preview qiladi.
        session_id = session_store.new_session_id()
        session_store.create_session(
            session_id,
            telegram_user_id=message.from_user.id,
            telegram_chat_id=chat_id,
            default_filename=doc.file_name or "document.docx",
        )
        session_store.update_session(
            session_id,
            mode="docx",
            docx_b64=base64.b64encode(file_bytes).decode("ascii"),
            watermark_settings=get_watermark_settings(chat_id),
            status="ready_for_review",
        )
        mirror_path = f"/tmp/{chat_id}_{doc.file_id}_pending.docx"
        try:
            with open(mirror_path, "wb") as f:
                f.write(file_bytes)
            kb = docx_web_keyboard(session_id)
            caption = (
                "📄 <b>Word fayl qabul qilindi.</b>\n\n"
                "🔍 <b>Tekshirish</b> — natijalarni/watermarkni sozlash sahifasi.\n"
                "🏷️ <b>Emblema almashtirish</b> — alohida emblemani topish, belgilash va almashtirish sahifasi.\n\n"
                "⚠️ CRC muammoli bitta rasm qolgan rasmlarni to'xtatmaydi."
            )
            await message.answer_document(
                FSInputFile(mirror_path, filename=doc.file_name or "document.docx"),
                caption=caption,
                reply_markup=kb,
            )
        finally:
            try:
                os.remove(mirror_path)
            except Exception:
                pass

    elif fname.endswith('.zip'):
        session_id, data = _get_or_create_session(chat_id, message.from_user.id)
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
        session_id, data = _get_or_create_session(chat_id, message.from_user.id)
        count = await _add_image_bytes_to_session(session_id, file_bytes, doc.file_name)
        await message.answer(
            f"📷 {count} ta rasm qabul qilindi.",
            reply_markup=collecting_keyboard(count),
        )
    else:
        await message.answer("⚠️ Faqat rasm fayllari yoki .zip qabul qilinadi.")


@dp.callback_query(F.data == "docx_settings")
async def docx_settings(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    if not _pending_docx_info(chat_id):
        await callback.answer("Word fayl topilmadi. Qaytadan yuboring.", show_alert=True)
        return
    await callback.message.answer(
        "💧 <b>Word ichidagi rasmlar uchun Watermark sozlamalari</b>\n"
        "Tanlovlar shu Word faylga qo'llanadi.",
        reply_markup=watermark_settings_keyboard(chat_id),
    )
    await callback.answer()


@dp.callback_query(F.data == "docx_preview")
async def docx_preview(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    if not _pending_docx_info(chat_id):
        await callback.answer("Word fayl topilmadi. Qaytadan yuboring.", show_alert=True)
        return
    await _send_docx_preview(callback.message, chat_id)
    await callback.answer()


@dp.callback_query(F.data == "docx_check")
async def docx_check(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    if not _pending_docx_info(chat_id):
        await callback.answer("Word fayl topilmadi. Qaytadan yuboring.", show_alert=True)
        return
    await callback.answer("Tekshirish boshlandi...")
    await _process_pending_docx(chat_id, callback.message)


@dp.callback_query(F.data == "docx_cancel")
async def docx_cancel(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    _pending_docx.pop(chat_id, None)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Word fayl bekor qilindi.")


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
            # Asl rasmni ham saqlab qolamiz - Word faylga shu rasm ham
            # qo'shiladi (savol matnidan oldin, namunadagi kabi).
            "image_b64": img_entry["b64"],
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
        build_docx(questions, out_path, title=data.get("default_filename", "Test Savollari"), watermark_settings=data.get("watermark_settings"))
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
