"""
web_app.py
----------
IKKI VAZIFANI BAJARADIGAN yagona fayl (avvalgi QuizMakerBot/CloneBot uslubida):

1) Streamlit sahifasi (asosiy oqim) - Telegram Mini App sifatida ochilib,
   natijalarni tekshirish/tahrirlash imkonini beradi.
2) Fon jarayoni sifatida Telegram botning o'zini (aiogram polling) BIR MARTA,
   background thread'da ishga tushiradi - shunda alohida "python bot.py"
   serverini ishga tushirish shart emas, hammasi shu Streamlit ilova ichida
   ishlaydi (xuddi sizning QuizMakerBot/CloneBot loyihalaringizdagi kabi).

MUHIM CHEKLOV: Streamlit Cloud ilova hech kim ochmagan paytda "uxlab qoladi"
(sleep), shunda fon-thread ham to'xtaydi va bot javob bermay qoladi. Buni
oldini olish uchun UptimeRobot (https://uptimerobot.com, bepul) kabi xizmat
orqali ilova URL'ini har 5 daqiqada "ping" qilib turish tavsiya etiladi -
xuddi avvalgi loyihalaringizda qilingani kabi.

ISHGA TUSHIRISH (GitHub + Streamlit Cloud orqali):
    1. Barcha .py fayllarni, requirements.txt, packages.txt ni GitHub repo'ga yuklang
    2. Streamlit Cloud'da "New app" -> shu repo -> Main file: web_app.py
    3. "Secrets" bo'limiga quyidagilarni yozing (Streamlit Cloud sozlamalarida):
         BOT_TOKEN = "sizning_tokeningiz"
         GROQ_API_KEY = "sizning_kalitingiz"
    4. Deploy qilganingizdan keyin ilova manzili (https://...streamlit.app)
       chiqadi - shu manzilni WEBAPP_BASE_URL sifatida ham "Secrets"ga qo'shing
       (o'zining URL'iga o'zi ishora qiladi, chunki Mini App shu yerning o'zi).

MUHIM: BOT_TOKEN muhit o'zgaruvchisi bu yerda ham kerak (Word faylni
foydalanuvchiga qaytarib yuborish uchun, hamda botning o'zini ishga tushirish uchun).
"""

import os
import sys
import io
import base64
import json
import zipfile
import requests
import streamlit as st
from PIL import Image

try:
    from streamlit_cropper import st_cropper
    _CROPPER_AVAILABLE = True
except ImportError:
    _CROPPER_AVAILABLE = False

# MUHIM: xuddi bot.py'dagi kabi - bu faylning o'zi joylashgan papka har doim
# sys.path'da bo'lishini ta'minlaymiz, aks holda "import bot", "import
# session_store" kabi lokal modullarni topishda muammo chiqishi mumkin
# (ba'zi hosting muhitlarida, masalan Streamlit Cloud'ning uv-asosli
# ishga tushirish jarayonida).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import session_store
from docx_builder import build_docx
from watermark import DEFAULT_SETTINGS, normalize_settings, preview_bytes, extract_docx_media_tolerant, watermark_docx, STYLE_LABELS, FONT_LABELS
from processor import detect_photo_region
from emblem import (detect_emblem, replace_emblem_on_image, replace_emblems_in_docx,
                    replace_emblems_in_docx_bytes, annotate_detection)

# Streamlit Cloud'da API kalitlar "Secrets" bo'limida saqlanadi (st.secrets),
# lekin bot.py va processor.py bularni oddiy os.environ orqali o'qiydi -
# shuning uchun mavjud bo'lsa, Secrets qiymatlarini avtomatik environ'ga
# nusxalab qo'yamiz. Bu orqali boshqa fayllarni o'zgartirish shart bo'lmaydi.
if hasattr(st, 'secrets'):
    for _key in ('BOT_TOKEN', 'GROQ_API_KEY', 'WEBAPP_BASE_URL'):
        try:
            if _key in st.secrets and not os.environ.get(_key):
                os.environ[_key] = str(st.secrets[_key])
        except Exception:
            pass

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')

st.set_page_config(page_title="Test natijalarini tekshirish", layout="centered")

# --- "edit.html" (TestPro) uslubidagi karta-asosli dizayn -----------------
# Bu faqat vizual qatlam - pastdagi mantiq (session_state) hech narsani
# yo'qotmasligi uchun alohida ishlab chiqilgan (pastga qarang).
_CARD_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Unbounded:wght@700;800;900&display=swap');

:root {
  --tp-accent: #4F46E5;
  --tp-accent-glow: rgba(79,70,229,0.15);
  --tp-green: #059669;
  --tp-red: #E11D48;
  --tp-border: rgba(148,163,184,0.25);
  --tp-bg-2: rgba(148,163,184,0.06);
}

html, body, [class*="css"] { font-family: 'Plus Jakarta Sans', sans-serif; }

/* Savol kartasi */
.tp-card {
  border: 1.5px solid var(--tp-border);
  border-radius: 16px;
  margin-bottom: 14px;
  overflow: hidden;
  background: var(--tp-bg-2);
}
.tp-card-hdr {
  display: flex; align-items: center; gap: .5rem;
  padding: .55rem .9rem;
  background: rgba(148,163,184,0.10);
  border-bottom: 1px solid var(--tp-border);
  font-family: 'Unbounded', sans-serif;
  font-size: .68rem; font-weight: 800;
  color: var(--tp-accent);
}
.tp-card-hdr .tp-warn { margin-left: auto; color: #D97706; font-family: 'Plus Jakarta Sans', sans-serif; font-weight: 700; font-size: .72rem; }
.tp-card-body { padding: .85rem .9rem 1rem; }

/* To'g'ri javob belgisi ustidagi label */
.tp-opt-lbl {
  font-family: 'Unbounded', sans-serif; font-weight: 800; font-size: .78rem;
}

/* Streamlit radio -> variant tanlagichni "chip" ko'rinishiga yaqinlashtiramiz */
div[data-testid="stRadio"] > div { gap: .35rem; }
div[data-testid="stRadio"] label {
  border: 1.5px solid var(--tp-border);
  border-radius: 10px;
  padding: .4rem .65rem;
  margin-bottom: .3rem;
  width: 100%;
  transition: all .15s ease;
}
div[data-testid="stRadio"] label:has(input:checked) {
  border-color: rgba(16,185,129,0.5);
  background: rgba(16,185,129,0.08);
}

.tp-badge {
  display: inline-block; font-size: .72rem; font-weight: 700;
  padding: .2rem .6rem; border-radius: 99px;
  border: 1px solid var(--tp-border); color: #64748B;
  margin-right: .4rem;
}
.tp-badge.ok { border-color: rgba(16,185,129,0.35); color: var(--tp-green); background: rgba(16,185,129,0.08); }
.tp-badge.warn { border-color: rgba(245,158,11,0.35); color: #D97706; background: rgba(245,158,11,0.08); }

hr.tp-sep { border: none; border-top: 1px solid var(--tp-border); margin: .6rem 0; }

/* ------------------------------------------------------------------ */
/* VARIANT QATORI: radio-doira + matn + o'chirish tugmasi BIR QATORDA  */
/* ------------------------------------------------------------------ */
/* Qatorning o'zini qat'iy gorizontal, hech qachon o'ralmaydigan (nowrap)
   flex qilib belgilaymiz - aks holda tor (mobil) ekranda Streamlit
   ustunlarni avtomatik ravishda vertikal ustma-ust joylashtirib
   yuboradi (aynan shu narsa "doira va X bir-birining tagida" ko'rinishga
   sabab bo'lgan edi). */
div[data-testid="stHorizontalBlock"]:has(textarea[placeholder^="Variant"]) {
  display: flex !important;
  flex-direction: row !important;
  flex-wrap: nowrap !important;
  align-items: flex-start !important;
  gap: 0.4rem !important;
  width: 100% !important;
}

div[data-testid="stHorizontalBlock"]:has(textarea[placeholder^="Variant"]) > div:first-child,
div[data-testid="stHorizontalBlock"]:has(textarea[placeholder^="Variant"]) [data-testid="column"]:first-of-type,
div[data-testid="stHorizontalBlock"]:has(textarea[placeholder^="Variant"]) > div:last-child,
div[data-testid="stHorizontalBlock"]:has(textarea[placeholder^="Variant"]) [data-testid="column"]:last-of-type {
  flex: 0 0 auto !important;
  width: auto !important;
  min-width: 0 !important;
  flex-shrink: 0 !important;
  flex-grow: 0 !important;
  padding-top: 0.3rem !important;
}
div[data-testid="stHorizontalBlock"]:has(textarea[placeholder^="Variant"]) > div:nth-child(2),
div[data-testid="stHorizontalBlock"]:has(textarea[placeholder^="Variant"]) [data-testid="column"]:nth-of-type(2) {
  flex: 1 1 auto !important;
  min-width: 0 !important;
}

div[data-testid="stHorizontalBlock"]:has(textarea[placeholder^="Variant"]) textarea {
  border-radius: 10px !important;
  background-color: #F8F9FA !important;
  border: 1px solid var(--tp-border) !important;
  padding: 0.4rem 0.6rem !important;
  width: 100% !important;
  box-sizing: border-box !important;
  resize: none !important;
  line-height: 1.35 !important;
  overflow-y: hidden !important;
}

div[data-testid="stHorizontalBlock"]:has(textarea[placeholder^="Variant"]) [data-testid="column"]:first-of-type button {
  width: 30px !important; height: 30px !important; min-width: 30px !important;
  border-radius: 50% !important; padding: 0 !important;
  font-size: 0 !important;
  border: 2px solid var(--tp-border) !important;
  background: #fff !important;
  box-shadow: none !important;
}
div[data-testid="stHorizontalBlock"]:has(textarea[placeholder^="Variant"]) [data-testid="column"]:first-of-type button[kind="primary"] {
  border-color: var(--tp-accent) !important;
  box-shadow: inset 0 0 0 8px #fff, inset 0 0 0 30px var(--tp-accent) !important;
}
div[data-testid="stHorizontalBlock"]:has(textarea[placeholder^="Variant"]) [data-testid="column"]:last-of-type button {
  width: 34px !important; height: 34px !important; min-width: 34px !important;
  border-radius: 10px !important; padding: 0 !important;
}

/* Qo'lda kesish (streamlit-cropper) uchun xavfsizlik to'sig'i: rasmni
   oldindan kichraytirsak ham, ba'zi qurilmalarda kesish maydoni bir
   necha piksel tashqariga chiqishi mumkin - shu qoida uni har doim
   ekran/konteyner kengligiga qat'iy cheklaydi va gorizontal scrollni
   yashiradi, shunda o'ng tomon "kesilib" ko'rinmay qolmaydi. */
.main .block-container { overflow-x: hidden !important; }
div[data-testid="stHorizontalBlock"]:has(textarea[placeholder^="Variant"]) { overflow-x: hidden !important; }
img[alt="Cropper"], div:has(> img[alt="Cropper"]) {
  max-width: 100% !important;
  width: 100% !important;
  height: auto !important;
}
</style>
"""
st.markdown(_CARD_CSS, unsafe_allow_html=True)


# --- Botni fon-thread sifatida BIR MARTA ishga tushirish ---
#
# CloneBot/QuizMakerBot loyihalaringizdagi bilan bir xil naqsh:
# @st.cache_resource orqali Streamlit'ning o'zi "bu funksiya faqat BIR MARTA
# ishlasin" degan kafolatni beradi - keyingi har qanday sahifa yangilanishi
# (rerun, boshqa foydalanuvchi so'rovi va h.k.) da qayta CHAQIRILMAYDI, faqat
# birinchi natija keshdan qaytariladi. Haqiqiy polling esa bot.py'dagi
# run_in_background() ichida - u yana OS-darajasidagi fayl-lock bilan ham
# himoyalangan (ikkinchi bosqich xavfsizlik).
@st.cache_resource
def _start_bot_once():
    try:
        import bot as bot_module
        t = bot_module.run_in_background()
        if t is None:
            st.warning(
                "Bot fon-jarayoni ishga tushmadi (BOT_TOKEN yo'q yoki boshqa "
                "nusxa allaqachon ishlayapti)."
            )
        return t
    except Exception as e:
        st.warning(f"Bot ishga tushirishda xatolik: {e}")
        return None


def ensure_bot_running():
    if not BOT_TOKEN:
        return
    _start_bot_once()


def send_docx_to_telegram(chat_id, file_path_or_bytes, filename, session_id=None):
    """Send a DOCX to Telegram.

    Accepts either a filesystem path or raw bytes.  Bytes are preferred for
    emblem replacement because Streamlit reruns/concurrent sessions can make
    shared /tmp paths race with cleanup.
    """
    if not BOT_TOKEN:
        st.error("BOT_TOKEN sozlanmagan - Word faylni Telegram'ga yubora olmayman.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    reply_markup = None
    if session_id:
        base = f"{os.environ.get('WEBAPP_BASE_URL', '').rstrip('/')}/?session_id={session_id}"
        if base.startswith("http"):
            reply_markup = {
                "inline_keyboard": [
                    [{"text": "🔍 Tekshirish", "url": f"{base}&page=watermark"}],
                    [{"text": "🏷️ Emblema almashtirish", "url": f"{base}&page=emblem"}],
                    [{"text": "🖼️ Rasm almashtirish", "url": f"{base}&page=image_replace"}],
                ]
            }

    try:
        if isinstance(file_path_or_bytes, (bytes, bytearray, memoryview)):
            file_obj = io.BytesIO(bytes(file_path_or_bytes))
            file_obj.name = filename
            files = {'document': (filename, file_obj,
                                  'application/vnd.openxmlformats-officedocument.wordprocessingml.document')}
            data = {'chat_id': chat_id}
            if reply_markup:
                data['reply_markup'] = json.dumps(reply_markup, ensure_ascii=False)
            resp = requests.post(url, data=data, files=files, timeout=90)
        else:
            with open(file_path_or_bytes, 'rb') as f:
                files = {'document': (filename, f,
                                      'application/vnd.openxmlformats-officedocument.wordprocessingml.document')}
                data = {'chat_id': chat_id}
                if reply_markup:
                    data['reply_markup'] = json.dumps(reply_markup, ensure_ascii=False)
                resp = requests.post(url, data=data, files=files, timeout=90)
    except OSError as exc:
        st.error(f"❌ Word faylga kirishda xatolik: {exc}")
        return False
    except requests.RequestException as exc:
        st.error(f"❌ Telegram tarmoq xatosi: {exc}")
        return False

    if resp.status_code != 200:
        st.error(f"Telegram'ga yuborishda xatolik: {resp.text}")
        return False
    return True


def _session_image_sources(data, docx_mode=False):
    """Web editor uchun Word/test rasmlarini yagona ro'yxatga aylantiradi."""
    sources = []
    if docx_mode:
        docx_bytes = base64.b64decode(data.get("docx_b64", ""))
        try:
            media_entries = extract_docx_media_tolerant(docx_bytes)
        except Exception:
            media_entries = []
        for idx, item in enumerate(media_entries):
            if item.get("data"):
                sources.append({
                    "name": item.get("name", f"image_{idx+1}"),
                    "data": item["data"],
                    "crc_bad": bool(item.get("crc_bad")),
                })
    else:
        for idx, q in enumerate(data.get("questions", [])):
            b64 = q.get("image_b64")
            if not b64:
                continue
            try:
                raw = base64.b64decode(b64)
            except Exception:
                continue
            sources.append({"name": q.get("source_image", f"test_{idx+1}.jpg"), "data": raw, "crc_bad": False})
    return sources


def _render_emblem_editor(data, docx_mode=True):
    """V10 Pro — independent emblem replacement workspace.

    The selected old sample defines the replacement unit:
      • crop only the round emblem -> only the round emblem is replaced;
      • crop emblem + Telegram icon together -> the whole block is replaced;
      • if a different image has the same block at another position/size, the
        detector searches for it automatically.
    """
    st.title("🏷️ Emblema almashtirish — PRO V11")
    st.caption("Emblema almashtirish watermark sahifasidan mustaqil ishlaydi. Qaysi qismni eski namuna sifatida belgilasangiz, aynan o‘sha qism qidiriladi va almashtiriladi.")

    sources = _session_image_sources(data, docx_mode=docx_mode)
    if not sources:
        st.error("❌ Fayl/test rasmlaridan foydalanish uchun o‘qiladigan rasm topilmadi.")
        return

    # --- 1. Old sample -------------------------------------------------
    st.subheader("1️⃣ Eski emblemani qanday ko‘rsatamiz?")
    source_mode = st.radio(
        "Usul",
        ["📤 Alohida rasm yuklash", "✂️ Fayldagi/test rasmdan kesib belgilash"],
        horizontal=True,
        key="v9_old_source_mode",
    )

    old_bytes = None
    old_name = ""
    if source_mode.startswith("📤"):
        old_file = st.file_uploader(
            "Eski emblema / watermark namunasini yuklang",
            type=["png", "jpg", "jpeg", "webp"],
            key="v9_old_emblem_file",
            help="Faqat emblem bo‘lsa — faqat emblemni yuboring. Telegram belgisi ham almashtirilsa — ikkalasini birga qamrab olgan rasmni yuboring.",
        )
        if old_file:
            old_bytes = old_file.getvalue()
            old_name = old_file.name
    else:
        names = [x["name"] for x in sources]
        idx = st.selectbox(
            "Emblema bor rasm",
            range(len(names)),
            format_func=lambda i: f"{i+1}. {names[i]}" + (" ⚠️ CRC" if sources[i].get("crc_bad") else ""),
            key="v9_crop_source_idx",
        )
        src = sources[idx]["data"]
        try:
            src_img = Image.open(io.BytesIO(src)).convert("RGB")
        except Exception as exc:
            st.error(f"❌ Rasmni ochib bo‘lmadi: {exc}")
            return
        st.info("💡 Muhim: Telegram belgisi va dumaloq emblema birga almashtirilsa, **ikkalasini bitta quti ichiga birga oling**. Faqat dumaloq emblema almashtirilsa, faqat uni qirqing.")
        if _CROPPER_AVAILABLE:
            crop_key = f"v9_emblem_cropper_{idx}_{sources[idx]['name']}"
            crop = st_cropper(
                src_img,
                realtime_update=True,
                box_color="#ff4b4b",
                aspect_ratio=None,
                return_type="image",
                key=crop_key,
            )
            if crop is not None:
                cbuf = io.BytesIO(); crop.convert("RGBA").save(cbuf, format="PNG")
                crop_bytes = cbuf.getvalue()
                st.image(crop_bytes, caption="Tanlanayotgan eski namuna", width=280)
                a, b = st.columns(2)
                with a:
                    if st.button("✅ Shu qismni qabul qilish", use_container_width=True, type="primary", key="v9_accept_crop"):
                        st.session_state["v9_old_template_b64"] = base64.b64encode(crop_bytes).decode("ascii")
                        st.session_state["v9_old_template_name"] = f"crop:{sources[idx]['name']}"
                        st.rerun()
                with b:
                    if st.button("🗑️ Tanlangan namunani tozalash", use_container_width=True, key="v9_clear_crop"):
                        st.session_state.pop("v9_old_template_b64", None)
                        st.session_state.pop("v9_old_template_name", None)
                        st.rerun()
        else:
            st.warning("streamlit-cropper o‘rnatilmagan. requirements.txt dagi streamlit-cropper ni o‘rnating.")

        saved = st.session_state.get("v9_old_template_b64")
        if saved:
            old_bytes = base64.b64decode(saved)
            old_name = st.session_state.get("v9_old_template_name", "crop")

    if old_bytes:
        st.success(f"✅ Eski namuna tayyor: {old_name}")
        st.image(old_bytes, caption="Eski namuna — tizim aynan shu blokni qidiradi", width=300)
        st.caption("Qora/oq fonli namuna ham ishlaydi. V11 kichik emblemani (hatto ~18–25 px) alohida qidiradi, fon rangini emas shakl/rang/kontur tuzilmasini solishtiradi. Eng yaxshi natija: shaffof PNG, lekin majburiy emas.")

    # --- 2. New sample -------------------------------------------------
    st.subheader("2️⃣ Yangi emblema")
    new_file = st.file_uploader(
        "Yangi emblemani yuklang",
        type=["png", "jpg", "jpeg", "webp"],
        key="v9_new_emblem_file",
        help="Shaffof PNG tavsiya etiladi. Yangi rasmning o‘zida ortiqcha katta bo‘sh fon bo‘lmasin.",
    )
    new_bytes = new_file.getvalue() if new_file else None
    if new_bytes:
        st.image(new_bytes, caption="Yangi emblema", width=300)

    # --- 3. Size/quality controls -------------------------------------
    st.subheader("3️⃣ O‘lcham, tozalash va aniqlik")
    c1, c2, c3 = st.columns(3)
    with c1:
        scale = st.slider("📐 Eski o‘lchamga qo‘shimcha", -25, 35, 0, 1, key="v9_emblem_scale")
    with c2:
        opacity = st.slider("👻 Shaffoflik", 10, 100, 100, 1, key="v9_emblem_opacity")
    with c3:
        confidence = st.slider("🎯 Minimal ishonch", 35, 90, 45, 1, key="v9_emblem_conf", help="35–45%: juda kichik/siqilgan emblemalar uchun tolerant. 55%+: noto‘g‘ri topish xavfini kamaytiradi.")

    c4, c5 = st.columns(2)
    with c4:
        stretch = st.checkbox("📏 Yangi emblemani aniqlangan qutiga to‘liq moslashtirish", value=False, key="v9_emblem_stretch",
                              help="O‘chirilsa, yangi rasm proporsiyasi saqlanadi. Yoqilsa, yangi rasm aniqlangan eski blokni to‘liq yopadi.")
    with c5:
        cleanup = st.slider("🧽 Eski belgini tozalash zaxirasi", 0, 15, 6, 1, key="v9_cleanup_padding",
                            help="Eski Telegram/emblem chetida qolib ketmasligi uchun aniqlangan qutidan necha foiz tashqariga tozalash.")

    if old_bytes and new_bytes:
        st.divider()
        # --- 4. Preview ------------------------------------------------
        st.subheader("4️⃣ Tekshirish va Preview")
        names = [x["name"] for x in sources]
        idx = st.selectbox(
            "Preview uchun rasm",
            range(len(names)),
            format_func=lambda i: f"{i+1}. {names[i]}" + (" ⚠️ CRC tiklangan" if sources[i].get("crc_bad") else ""),
            key="v9_preview_image_idx",
        )
        target = sources[idx]["data"]

        a, b = st.columns(2)
        with a:
            if st.button("🔍 Emblemani avtomatik topish", use_container_width=True, key="v9_detect"):
                det = detect_emblem(old_bytes, target, min_confidence=confidence/100.0)
                if det.found:
                    st.image(annotate_detection(target, det), caption=f"✅ Topildi • {det.confidence:.0%} • {det.method}", use_container_width=True)
                    st.success(f"📍 x={det.x}, y={det.y} • {det.w}×{det.h}px • {det.reason}")
                    st.caption("🟢 Ramka — aynan shu qism almashtiriladi. Faqat emblem kesilgan bo‘lsa faqat emblem; emblem + Telegram birga kesilgan bo‘lsa butun blok almashtiriladi. Fon (oq/qora) qidiruvga to‘sqinlik qilmaydi.")
                else:
                    st.warning(f"⚠️ Topilmadi: {det.reason}")
        with b:
            if st.button("👁️ Almashtirish Preview", use_container_width=True, type="primary", key="v9_preview"):
                out_img, det = replace_emblem_on_image(
                    target, old_bytes, new_bytes,
                    scale_percent=scale, opacity=opacity,
                    min_confidence=confidence/100.0,
                    stretch=stretch, clean_old=True, cleanup_padding=cleanup,
                )
                if det.found:
                    x1, x2 = st.columns(2)
                    with x1: st.image(target, caption=f"OLD • {det.w}×{det.h}px", use_container_width=True)
                    with x2: st.image(out_img, caption=f"NEW • +{scale}% • {det.confidence:.0%}", use_container_width=True)
                    st.success("✅ Preview tayyor. Eski blok avval tozalandi, keyin yangi emblema joylashtirildi.")
                else:
                    st.warning(f"⚠️ Bu rasmda emblema topilmadi: {det.reason}")

        if st.button("📊 Barcha Word rasmlarida tekshirish", use_container_width=True, key="v9_scan_all"):
            found = missing = 0
            progress = st.progress(0)
            rows = []
            for i, item in enumerate(sources, 1):
                det = detect_emblem(old_bytes, item["data"], min_confidence=confidence/100.0)
                if det.found:
                    found += 1
                    rows.append(f"✅ {item['name']} — {det.confidence:.0%} • {det.w}×{det.h} • {det.method}")
                else:
                    missing += 1
                    rows.append(f"⚠️ {item['name']} — topilmadi • {det.reason}")
                progress.progress(i / len(sources))
            st.success(f"Tekshiruv: {len(sources)} ta • ✅ {found} topildi • ⚠️ {missing} topilmadi")
            with st.expander("Batafsil natija", expanded=True):
                st.text("\n".join(rows))

        # --- 5. Apply ---------------------------------------------------
        st.divider()
        st.subheader("5️⃣ Yakuniy almashtirish")
        st.info("🔒 Har bir Word rasmi mustaqil tekshiriladi. Bir rasmda CRC yoki detection xatosi bo‘lsa ham qolgan rasmlar davom etadi. Namuna faqat emblem bo‘lsa — faqat emblem; emblem + Telegram birga tanlangan bo‘lsa — ikkalasi birga almashtiriladi.")
        if st.button("🏷️ Emblemani barcha Word rasmlariga almashtirish", use_container_width=True, type="primary", key="v9_apply"):
            try:
                docx_bytes = base64.b64decode(data.get("docx_b64", ""), validate=True)
                if not docx_bytes:
                    raise ValueError("DOCX ma'lumoti bo'sh.")
                with st.spinner("🔍 Har bir Word rasmini tekshirish, eski belgini tozalash va yangi emblemani joylashtirish..."):
                    # IMPORTANT: process in memory.  This removes the /tmp output
                    # race that previously caused "No such file or directory"
                    # when Telegram sending happened after a Streamlit rerun.
                    output_bytes, report = replace_emblems_in_docx_bytes(
                        docx_bytes, old_bytes, new_bytes,
                        scale_percent=scale, opacity=opacity,
                        min_confidence=confidence/100.0,
                        stretch=stretch, clean_old=True, cleanup_padding=cleanup,
                    )
                if not output_bytes or len(output_bytes) < 100:
                    raise IOError("Yakuniy DOCX hosil bo'lmadi yoki bo'sh chiqdi.")
                ok = send_docx_to_telegram(
                    data.get("telegram_chat_id"), output_bytes,
                    data.get("default_filename", "emblem_replaced.docx"),
                    session_id=data["session_id"],
                )
                if ok:
                    st.success(f"✅ Tayyor! {report['found']} ta rasmda almashtirildi • Topilmagan: {report['not_found']} • CRC tiklangan: {report['repaired']} • Xatolik: {report['failed']}")
                    if report.get("not_found") or report.get("failed"):
                        with st.expander("⚠️ Tafsilotlar"):
                            for d in report["details"]:
                                if d.get("status") != "replaced":
                                    st.write(f"• {d['name']} — {d.get('status')} — {d.get('reason','')}")
                    session_store.update_session(data["session_id"], status="ready_for_review")
            except Exception as e:
                st.error(f"❌ Emblemalarni almashtirishda xatolik: {e}")
    else:
        st.warning("⬆️ Avval eski namuna (yuklash yoki rasmdan kesish) va yangi emblemani bering.")

def _render_watermark_editor(data, docx_mode=False):
    """Shared watermark editor for image sessions and DOCX sessions.

    DOCX mode reads media through extract_docx_media_tolerant(), so a single
    stale CRC (for example word/media/image14.jpeg) never prevents preview of
    the other usable images.
    """
    if docx_mode:
        docx_bytes = base64.b64decode(data.get("docx_b64", ""))
        media_entries = extract_docx_media_tolerant(docx_bytes)
        readable = [m for m in media_entries if m.get("data")]
        if not readable:
            st.error("❌ Word fayl ichidan o'qiladigan rasm topilmadi.")
            bad = [m["name"] for m in media_entries]
            if bad:
                st.caption("Tekshirilgan rasmlar: " + ", ".join(bad[:8]))
            return
        data["_docx_media_entries"] = readable
        questions = [{"image_b64": base64.b64encode(m["data"]).decode("ascii")} for m in readable]
    else:
        questions = data.get("questions", [])

    wm_initial = normalize_settings(data.get("watermark_settings", DEFAULT_SETTINGS))
    for _k, _v in wm_initial.items():
        st.session_state.setdefault(f"wm_{_k}", _v)
    st.session_state.setdefault("wm_show_preview", False)

    st.subheader("💧 Rasm Watermark — to'liq sozlash")
    st.caption("Watermark Word sahifasiga emas, Word ichidagi rasmlarning O'ZIGA qo'yiladi." if docx_mode else "Watermark Word sahifasiga emas, Word ichidagi rasmlarning O'ZIGA qo'yiladi.")

    wm_enabled = st.checkbox("Watermarkni yoqish", key="wm_enabled")
    preset_names = ["✏️ Maxsus matn", "© QuizMaker Bot", "QUIZMAKER", "@MyTestBot", "CONFIDENTIAL", "SAMPLE"]
    current_text = st.session_state.get("wm_text", DEFAULT_SETTINGS["text"])
    preset_index = 0 if current_text not in preset_names[1:] else preset_names.index(current_text)
    preset = st.selectbox("📝 Yozuv namunasi", preset_names, index=preset_index, key="wm_preset")
    if preset != "✏️ Maxsus matn":
        st.session_state["wm_text"] = preset
    wm_text = st.text_input("✏️ Watermark yozuvi", key="wm_text", max_chars=120)

    style_options = {"Diagonal":"diagonal","Markaziy":"center","Takrorlanuvchi":"pattern","Burchak":"corner","Ikki diagonal":"double","Stamp":"stamp","Kontur":"outline"}
    reverse_style = {v:k for k,v in style_options.items()}
    current_style_label = reverse_style.get(st.session_state.get("wm_style", "diagonal"), "Diagonal")
    c1,c2,c3=st.columns(3)
    with c1:
        wm_style_label=st.selectbox("🎨 Uslub / dizayn", list(style_options.keys()), index=list(style_options.keys()).index(current_style_label), key="wm_style_label")
    with c2:
        wm_opacity=st.slider("👻 Shaffoflik (%)",1,100,int(st.session_state.get("wm_opacity",18)),1,key="wm_opacity")
    with c3:
        wm_size=st.slider("🔠 Yozuv hajmi",8,120,int(st.session_state.get("wm_size",30)),2,key="wm_size")
    c4,c5,c6=st.columns(3)
    with c4:
        wm_angle=st.slider("📐 Burchak",-180,180,int(st.session_state.get("wm_angle",-35)),5,key="wm_angle")
    with c5:
        wm_color=st.color_picker("🎨 Yozuv rangi",value=st.session_state.get("wm_color","#FFFFFF"),key="wm_color")
    with c6:
        wm_font_label=st.selectbox("🔤 Shrift",["Sans","Serif","Mono"],index={"sans":0,"serif":1,"mono":2}.get(st.session_state.get("wm_font","sans"),0),key="wm_font_label")
    c7,c8,c9=st.columns(3)
    with c7:
        wm_bold=st.checkbox("B 🔥 Qalin yozuv",value=bool(st.session_state.get("wm_bold",True)),key="wm_bold")
    with c8:
        wm_stroke=st.slider("🖊️ Kontur qalinligi",0,10,int(st.session_state.get("wm_stroke",0)),1,key="wm_stroke")
    with c9:
        wm_gap=st.slider("↔️ Pattern oralig'i",40,500,int(st.session_state.get("wm_pattern_gap",180)),10,key="wm_pattern_gap")

    st.session_state["wm_style"]=style_options[wm_style_label]
    font_map={"Sans":"sans","Serif":"serif","Mono":"mono"}
    wm_settings=normalize_settings({"enabled":wm_enabled,"text":wm_text,"style":st.session_state["wm_style"],"opacity":wm_opacity,"angle":wm_angle,"size":wm_size,"color":wm_color,"bold":wm_bold,"font":font_map[wm_font_label],"stroke":wm_stroke,"pattern_gap":wm_gap})
    session_store.update_session(data["session_id"], watermark_settings=wm_settings)

    readable_count=len(readable) if docx_mode else sum(1 for q in questions if q.get("image_b64"))
    st.caption(f"🖼️ Preview manbasi: {readable_count} ta o'qiladigan rasm. CRC xatosi bor rasm bo'lsa ham qolganlari ko'rsatiladi.")
    preview_source=questions[0].get("image_b64") if questions else None
    p1,p2,p3=st.columns(3)
    with p1:
        if st.button("👁️ Preview",use_container_width=True,type="primary"):
            st.session_state["wm_show_preview"]=True
    with p2:
        if st.button("🔄 Standart",use_container_width=True):
            for k,v in DEFAULT_SETTINGS.items(): st.session_state[f"wm_{k}"]=v
            st.rerun()
    with p3:
        if st.button("🙈 Yopish",use_container_width=True): st.session_state["wm_show_preview"]=False

    if st.session_state.get("wm_show_preview") and preview_source:
        try:
            st.image(preview_bytes(base64.b64decode(preview_source),wm_settings),caption=f"👁️ Preview • {wm_text} • {wm_style_label} • {wm_opacity}%",use_container_width=True)
            if docx_mode:
                bad=[m["name"] for m in media_entries if m.get("data") is None]
                repaired=[m["name"] for m in media_entries if m.get("crc_bad") and m.get("data") is not None]
                if repaired: st.info(f"♻️ CRC tuzatilgan holda o'qildi: {', '.join(repaired[:3])}" + (" …" if len(repaired)>3 else ""))
                if bad: st.warning(f"⚠️ Haqiqatan o'qilmaydigan rasm(lar) o'tkazib yuborildi: {', '.join(bad[:3])}" + (" …" if len(bad)>3 else ""))
        except Exception as e:
            st.warning(f"Preview yaratilmadi: {e}")

    st.divider()
    if docx_mode:
        if st.button("💧 Watermarkni Word rasmlariga qo'llash",use_container_width=True,type="primary"):
            import tempfile, os
            in_path=f"/tmp/{data['session_id']}_input.docx"; out_path=f"/tmp/{data['session_id']}_watermarked.docx"
            try:
                with open(in_path,"wb") as f: f.write(docx_bytes)
                watermark_docx(in_path,out_path,wm_settings)
                report=getattr(watermark_docx,"last_report",{}) or {}
                ok=send_docx_to_telegram(data.get("telegram_chat_id"),out_path,data.get("default_filename","watermarked.docx"), session_id=data["session_id"])
                if ok:
                    st.success(f"✅ Word tayyor. {report.get('changed',0)} ta rasm watermarklandi; ♻️ CRC: {report.get('repaired',0)}")
                    session_store.update_session(data["session_id"],status="done")
            except Exception as e:
                st.error(f"❌ Wordni qayta ishlashda xatolik: {e}")
            finally:
                for pp in (in_path,out_path):
                    try: os.remove(pp)
                    except Exception: pass
    return

def _image_bytes_preview(raw, max_side=900):
    """Rasmni Streamlit preview uchun xavfsiz ochadi."""
    im = Image.open(io.BytesIO(raw))
    im.load()
    return im.convert("RGB")


def _image_similarity(a_bytes, b_bytes):
    """Ikki rasmning ko'rinish o'xshashligini taxminiy 0..1 ballga aylantiradi.
    Bu aynan bir xil rasmni topishdan tashqari, o'xshash sahifa/formatdagi
    rasmlarni ham yuqoriga chiqaradi."""
    try:
        a = _image_bytes_preview(a_bytes).resize((96, 96), Image.Resampling.LANCZOS)
        b = _image_bytes_preview(b_bytes).resize((96, 96), Image.Resampling.LANCZOS)
        import numpy as np
        aa = np.asarray(a, dtype=np.float32) / 255.0
        bb = np.asarray(b, dtype=np.float32) / 255.0
        # Geometrik ko'rinish
        mse = float(np.mean((aa - bb) ** 2))
        pixel_score = max(0.0, 1.0 - mse / 0.20)
        # Rang taqsimoti
        ah, _ = np.histogram(aa.reshape(-1), bins=32, range=(0,1), density=True)
        bh, _ = np.histogram(bb.reshape(-1), bins=32, range=(0,1), density=True)
        ah = ah / (np.linalg.norm(ah) + 1e-8)
        bh = bh / (np.linalg.norm(bh) + 1e-8)
        hist_score = float(np.clip(np.dot(ah, bh), 0, 1))
        # O'lcham nisbati
        ai = _image_bytes_preview(a_bytes)
        bi = _image_bytes_preview(b_bytes)
        ar = ai.width / max(ai.height, 1)
        br = bi.width / max(bi.height, 1)
        ratio_score = max(0.0, 1.0 - abs(ar-br) / max(ar, br, 1e-6))
        return float(np.clip(0.65*pixel_score + 0.25*hist_score + 0.10*ratio_score, 0, 1))
    except Exception:
        return 0.0


def _replace_docx_media_bytes(docx_bytes, media_name, new_image_bytes):
    """DOCX ichidagi word/media/<media_name> ni yangi rasm bilan almashtiradi.
    ZIP metadata va qolgan fayllar saqlanadi."""
    src = io.BytesIO(docx_bytes)
    out = io.BytesIO()
    replaced = False
    with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.startswith("word/media/") and os.path.basename(info.filename) == media_name:
                data = new_image_bytes
                replaced = True
            zout.writestr(info, data)
    if not replaced:
        raise FileNotFoundError(f"Word media topilmadi: {media_name}")
    return out.getvalue()


def _safe_image_for_word(raw_bytes, target_name):
    """ZIP'dan kelgan rasmni Word media faylining mavjud formatiga moslaydi.
    Masalan, word/media/image1.jpeg o'rniga PNG baytlarini yozib yuborish
    Word viewer'larda muammo berishi mumkin. Shu sababli target extension
    saqlanadi va rasm qayta encode qilinadi."""
    ext = os.path.splitext(target_name)[1].lower()
    fmt = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.webp': 'WEBP',
           '.bmp': 'BMP', '.tif': 'TIFF', '.tiff': 'TIFF'}.get(ext)
    if not fmt:
        return raw_bytes
    try:
        im = Image.open(io.BytesIO(raw_bytes))
        im.load()
        if fmt == 'JPEG' and im.mode not in ('RGB', 'L'):
            bg = Image.new('RGB', im.size, 'white')
            if 'A' in im.getbands():
                bg.paste(im, mask=im.getchannel('A'))
            else:
                bg.paste(im.convert('RGB'))
            im = bg
        elif fmt != 'JPEG' and im.mode not in ('RGB', 'RGBA', 'L'):
            im = im.convert('RGBA' if 'A' in im.getbands() else 'RGB')
        out = io.BytesIO()
        save_kwargs = {'format': fmt}
        if fmt in ('JPEG', 'WEBP'):
            save_kwargs['quality'] = 95
        im.save(out, **save_kwargs)
        return out.getvalue()
    except Exception:
        return raw_bytes


def _extract_uploaded_image_zip(uploaded_bytes):
    """Foydalanuvchi yuborgan rasmlar.zip ichidan xavfsiz rasm katalogi."""
    items = []
    with zipfile.ZipFile(io.BytesIO(uploaded_bytes), 'r') as zf:
        for info in zf.infolist():
            name = info.filename.replace('\\', '/')
            if info.is_dir() or name.startswith('__MACOSX/'):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in VALID_IMAGE_EXT:
                continue
            # Zip-slip va haddan tashqari katta entrylardan himoya.
            if '..' in name.split('/') or info.file_size > 30 * 1024 * 1024:
                continue
            raw = zf.read(info)
            try:
                im = Image.open(io.BytesIO(raw)); im.load()
                # preview/algoritm uchun haqiqiy image ekanini tekshiramiz.
                im.convert('RGB')
                items.append({'name': os.path.basename(name), 'path': name, 'data': raw})
            except Exception:
                continue
    return items


def _render_image_replace_editor(data):
    """PRO image replacement workflow.

    Yangi rasmlar faqat foydalanuvchi yuborgan ZIP'dan olinadi.
    1) Word'dagi eski rasm tanlanadi.
    2) 'Almashtirish' bosilganda rasmlar.zip so'raladi.
    3) ZIP ochiladi, 'Tekshirish' bosilganda eski rasm bilan barcha ZIP
       rasmlari taqqoslanadi va eng o'xshashlari reyting qilinadi.
    4) Variant tanlanadi -> OLD/NEW/boshqa variantlar preview qilinadi.
    5) Faqat tasdiqlangandan keyin Word sessiyada yangilanadi.
    6) Alohida tugma orqali yakuniy Word Telegramga yuboriladi.
    """
    st.title("🖼️ Rasm almashtirish — PRO")
    st.caption("Yangi rasmlar manbasi faqat siz yuboradigan rasmlar.zip. Word yaratish/yuborishdan oldin barcha variantlarni tekshirish mumkin.")

    docx_b64 = data.get("docx_b64", "")
    if not docx_b64:
        st.error("❌ Word fayl ma'lumoti topilmadi.")
        return
    try:
        docx_bytes = base64.b64decode(docx_b64, validate=True)
        sources = _session_image_sources(data, docx_mode=True)
    except Exception as exc:
        st.error(f"❌ Word rasmlarini o'qib bo'lmadi: {exc}")
        return

    valid = []
    for item in sources:
        try:
            _image_bytes_preview(item["data"])
            valid.append(item)
        except Exception:
            pass
    if not valid:
        st.warning("⚠️ Word ichida o'qiladigan rasm topilmadi.")
        return

    # Har bir qayta yuklanishda tanlovlar saqlanadi, lekin yangi Word/ZIP uchun
    # aniq sessiya kaliti ishlatiladi.
    sid = data.get("session_id", "unknown")
    st.session_state.setdefault("imgrep_zip_items", [])
    st.session_state.setdefault("imgrep_ranked", [])
    st.session_state.setdefault("imgrep_candidate_idx", None)
    st.session_state.setdefault("imgrep_zip_name", "")
    st.session_state.setdefault("imgrep_working_docx_b64", docx_b64)

    names = [x["name"] for x in valid]
    selected = st.selectbox(
        "1️⃣ Word ichidan almashtiriladigan AVVALGI rasmni tanlang",
        range(len(valid)),
        format_func=lambda i: f"{i+1}. {names[i]}" + (" ⚠️ CRC" if valid[i].get("crc_bad") else ""),
        key=f"imgrep_source_idx_{sid}",
    )
    old_item = valid[selected]
    old_bytes = old_item["data"]

    c1, c2 = st.columns(2)
    with c1:
        st.image(old_bytes, caption=f"🟥 AVVALGI • {old_item['name']}", use_container_width=True)
    with c2:
        st.info("🔁 Almashtirish uchun pastdagi tugma orqali rasmlar.zip yuboring.")

    st.divider()
    st.subheader("2️⃣ Yangi rasmlar manbasi")
    if st.button("🔄 ZIPni almashtirish / rasmlar.zip yuborish", use_container_width=True, type="primary", key="imgrep_ask_zip"):
        st.session_state["imgrep_waiting_zip"] = True
        st.session_state["imgrep_zip_items"] = []
        st.session_state["imgrep_ranked"] = []
        st.session_state["imgrep_candidate_idx"] = None
        st.rerun()

    if st.session_state.get("imgrep_waiting_zip") or not st.session_state.get("imgrep_zip_items"):
        zip_file = st.file_uploader(
            "📦 Rasmlar ZIP faylini tanlang",
            type=["zip"],
            key=f"imgrep_zip_uploader_{sid}",
            help="ZIP ichiga PNG/JPG/JPEG/WEBP/BMP/TIFF rasmlarni joylang. Bot boshqa manbadan yangi rasm olmaydi.",
        )
        if zip_file:
            try:
                raw_zip = zip_file.getvalue()
                items = _extract_uploaded_image_zip(raw_zip)
                if not items:
                    st.error("❌ ZIP ichidan o'qiladigan rasm topilmadi.")
                else:
                    st.session_state["imgrep_zip_items"] = items
                    st.session_state["imgrep_zip_name"] = zip_file.name
                    st.session_state["imgrep_waiting_zip"] = False
                    st.session_state["imgrep_ranked"] = []
                    st.session_state["imgrep_candidate_idx"] = None
                    st.success(f"✅ {zip_file.name}: {len(items)} ta rasm qabul qilindi.")
            except zipfile.BadZipFile:
                st.error("❌ Fayl haqiqiy ZIP emas yoki buzilgan.")
            except Exception as exc:
                st.error(f"❌ ZIPni ochishda xatolik: {exc}")

    zip_items = st.session_state.get("imgrep_zip_items", [])
    if not zip_items:
        st.info("⬆️ Avval rasmlar.zip yuboring. Keyin '🔍 Tekshirish' orqali mos rasmlarni topamiz.")
        return

    st.success(f"📦 Manba: {st.session_state.get('imgrep_zip_name','rasmlar.zip')} • {len(zip_items)} ta rasm")

    st.subheader("3️⃣ Tekshirish")
    st.write("Eski rasm ZIP ichidagi barcha rasmlar bilan solishtiriladi. Eng moslari yuqoriga chiqadi.")
    if st.button("🔍 Tekshirish — eng o'xshash rasmlarni topish", use_container_width=True, type="primary", key="imgrep_check"):
        ranked = []
        progress = st.progress(0)
        for i, item in enumerate(zip_items):
            sc = _image_similarity(old_bytes, item["data"])
            ranked.append((sc, i))
            progress.progress((i + 1) / len(zip_items))
        ranked.sort(reverse=True, key=lambda x: x[0])
        st.session_state["imgrep_ranked"] = ranked[:12]
        st.session_state["imgrep_candidate_idx"] = None
        st.success(f"✅ {len(zip_items)} ta rasm tekshirildi. Eng mos {min(12,len(ranked))} ta variant tayyor.")

    ranked = st.session_state.get("imgrep_ranked", [])
    if not ranked:
        st.info("🔍 'Tekshirish' tugmasini bosing.")
        return

    st.subheader("4️⃣ Eng o'xshash variantlar")
    cols = st.columns(min(4, len(ranked)))
    for n, (sc, idx) in enumerate(ranked):
        item = zip_items[idx]
        with cols[n % len(cols)]:
            st.image(item["data"], caption=f"#{n+1} • {sc:.0%}\n{item['name']}", use_container_width=True)
            if st.button("✅ Tanlash", key=f"imgrep_zip_pick_{sid}_{idx}", use_container_width=True):
                st.session_state["imgrep_candidate_idx"] = idx
                st.rerun()

    candidate_idx = st.session_state.get("imgrep_candidate_idx")
    if candidate_idx is None or not (0 <= candidate_idx < len(zip_items)):
        st.info("⬆️ Variantlardan birini tanlang — keyin OLD/NEW preview chiqadi.")
        return

    candidate = zip_items[candidate_idx]
    candidate_bytes = candidate["data"]
    score = _image_similarity(old_bytes, candidate_bytes)

    st.divider()
    st.subheader("5️⃣ Yakuniy Preview — AVVALGI / HOZIRGI")
    a, b = st.columns(2)
    with a:
        st.image(old_bytes, caption=f"🟥 AVVALGI RASM • {old_item['name']}", use_container_width=True)
    with b:
        st.image(candidate_bytes, caption=f"🟩 HOZIRGI RASM • {candidate['name']} • {score:.0%}", use_container_width=True)
    st.success(f"Tanlangan variant: {candidate['name']} • vizual moslik: {score:.0%}")

    # Shu rasmni Word'ga qo'llashdan oldin yana bir qat'iy tasdiq.
    st.subheader("6️⃣ Tasdiqlash")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 Boshqa variantni tanlash", use_container_width=True):
            st.session_state["imgrep_candidate_idx"] = None
            st.rerun()
    with c2:
        if st.button("✅ Shu rasmni Word'ga qo'llash", use_container_width=True, type="primary"):
            try:
                replacement = _safe_image_for_word(candidate_bytes, old_item["name"])
                working_docx = base64.b64decode(st.session_state.get("imgrep_working_docx_b64", docx_b64), validate=True)
                output_bytes = _replace_docx_media_bytes(working_docx, old_item["name"], replacement)
                st.session_state["imgrep_working_docx_b64"] = base64.b64encode(output_bytes).decode("ascii")
                # Muhim: keyingi Tekshirish sahifasi ham aynan yangilangan Word'ni ko'radi.
                session_store.update_session(sid, docx_b64=base64.b64encode(output_bytes).decode("ascii"), status="ready_for_review")
                st.session_state["imgrep_candidate_idx"] = None
                st.session_state["imgrep_ranked"] = []
                st.success("✅ Rasm Word ichiga qo'llandi. Hali Telegramga yuborilmadi.")
                st.info("Endi boshqa rasmni ham almashtirishingiz yoki 🔍 Tekshirish sahifasiga o'tib yakuniy Word yaratishingiz mumkin.")
            except Exception as exc:
                st.error(f"❌ Rasmni Word'ga qo'llashda xatolik: {exc}")

    st.divider()
    st.subheader("7️⃣ Yakuniy Word")
    st.caption("Barcha rasm almashtirishlar tugagachgina Word'ni Telegramga yuboring.")
    working = st.session_state.get("imgrep_working_docx_b64", docx_b64)
    if st.button("📄 Yangilangan Wordni Telegramga yuborish", use_container_width=True, type="primary", key="imgrep_send_final"):
        try:
            out = base64.b64decode(working, validate=True)
            filename = data.get("default_filename", "rasm_almashtirilgan.docx")
            if not filename.lower().endswith(".docx"):
                filename += ".docx"
            ok = send_docx_to_telegram(data.get("telegram_chat_id"), out, filename, session_id=sid)
            if ok:
                session_store.update_session(sid, docx_b64=working, status="ready_for_review")
                st.success("✅ Yakuniy Word Telegramga yuborildi.")
        except Exception as exc:
            st.error(f"❌ Word yuborishda xatolik: {exc}")


def main():
    ensure_bot_running()

    query_params = st.query_params
    session_id = query_params.get("session_id", "")
    page = query_params.get("page", "watermark")

    if not session_id:
        st.title("🤖 Bot ishlamoqda")
        st.write(
            "Bu sahifa Telegram botning fon-jarayonini ham o'zida ishlatadi. "
            "Botga o'ting va rasm/zip yuborib boshlang."
        )
        st.caption(
            "Eslatma: agar bu sahifani hech kim ochmasa, Streamlit Cloud ilovani "
            "'uxlatib qo'yishi' mumkin va bot javob to'xtatadi. Buni oldini olish "
            "uchun UptimeRobot kabi xizmat bilan bu sahifani muntazam ping qiling."
        )
        return

    data = session_store.get_session(session_id)
    if data is None:
        st.warning(
            "Bu sessiya topilmadi yoki muddati o'tgan. "
            "Botga qaytib, rasmlarni qaytadan yuboring."
        )
        return

    if data.get("mode") == "docx" and data.get("docx_b64"):
        st.title("📄 Word fayl")
        st.caption(f"Fayl: {data.get('default_filename','document.docx')}")
        if page == "emblem":
            _render_emblem_editor(data, docx_mode=True)
        elif page == "image_replace":
            _render_image_replace_editor(data)
        else:
            _render_watermark_editor(data, docx_mode=True)
        return

    questions = data.get("questions", [])
    if not questions:
        st.info("Hozircha natija yo'q. Iltimos kuting yoki botga qaytib tekshiring.")
        return

    st.title("📋 Natijalarni tekshirish")
    st.caption(f"Jami {len(questions)} ta savol topildi. Kerak bo'lsa tahrirlang.")

    # Fayl nomi maydoni - standart qiymat zip fayl nomi (yoki bot bergan default)
    default_name = data.get("default_filename", "natija")
    filename = st.text_input("📁 Word fayl nomi", value=default_name)

    # ------------------------------------------------------------------
    # RASM WATERMARK SOZLAMALARI — to'liq boshqaruv + jonli preview
    # ------------------------------------------------------------------
    wm_initial = normalize_settings(data.get("watermark_settings", DEFAULT_SETTINGS))
    for _k, _v in wm_initial.items():
        st.session_state.setdefault(f"wm_{_k}", _v)
    st.session_state.setdefault("wm_show_preview", False)

    st.subheader("💧 Rasm Watermark — to'liq sozlash")
    st.caption("Watermark Word sahifasiga emas, Word ichidagi rasmlarning O'ZIGA qo'yiladi.")

    wm_enabled = st.checkbox("Watermarkni yoqish", key="wm_enabled")

    preset_names = ["✏️ Maxsus matn", "© QuizMaker Bot", "QUIZMAKER", "@MyTestBot", "CONFIDENTIAL", "SAMPLE"]
    current_text = st.session_state.get("wm_text", DEFAULT_SETTINGS["text"])
    preset_index = 0 if current_text not in preset_names[1:] else preset_names.index(current_text)
    preset = st.selectbox("📝 Yozuv namunasi", preset_names, index=preset_index, key="wm_preset")
    if preset != "✏️ Maxsus matn":
        st.session_state["wm_text"] = preset
    wm_text = st.text_input("✏️ Watermark yozuvi", key="wm_text", max_chars=120, help="Istalgan yozuvni kiriting.")

    style_options = {
        "Diagonal": "diagonal", "Markaziy": "center", "Takrorlanuvchi": "pattern",
        "Burchak": "corner", "Ikki diagonal": "double", "Stamp": "stamp", "Kontur": "outline",
    }
    reverse_style = {v:k for k,v in style_options.items()}
    current_style_label = reverse_style.get(st.session_state.get("wm_style", "diagonal"), "Diagonal")

    c1, c2, c3 = st.columns(3)
    with c1:
        wm_style_label = st.selectbox("🎨 Uslub / dizayn", list(style_options.keys()), index=list(style_options.keys()).index(current_style_label), key="wm_style_label")
    with c2:
        wm_opacity = st.slider("👻 Shaffoflik (%)", 1, 100, int(st.session_state.get("wm_opacity", 18)), 1, key="wm_opacity")
    with c3:
        wm_size = st.slider("🔠 Yozuv hajmi", 8, 120, int(st.session_state.get("wm_size", 30)), 2, key="wm_size")

    c4, c5, c6 = st.columns(3)
    with c4:
        wm_angle = st.slider("📐 Burchak", -180, 180, int(st.session_state.get("wm_angle", -35)), 5, key="wm_angle")
    with c5:
        wm_color = st.color_picker("🎨 Yozuv rangi", value=st.session_state.get("wm_color", "#FFFFFF"), key="wm_color")
    with c6:
        wm_font_label = st.selectbox("🔤 Shrift", ["Sans", "Serif", "Mono"], index={"sans":0,"serif":1,"mono":2}.get(st.session_state.get("wm_font","sans"),0), key="wm_font_label")

    c7, c8, c9 = st.columns(3)
    with c7:
        wm_bold = st.checkbox("B 🔥 Qalin yozuv", value=bool(st.session_state.get("wm_bold", True)), key="wm_bold")
    with c8:
        wm_stroke = st.slider("🖊️ Kontur qalinligi", 0, 10, int(st.session_state.get("wm_stroke", 0)), 1, key="wm_stroke")
    with c9:
        wm_gap = st.slider("↔️ Pattern oralig'i", 40, 500, int(st.session_state.get("wm_pattern_gap", 180)), 10, key="wm_pattern_gap")

    st.session_state["wm_style"] = style_options[wm_style_label]
    font_map = {"Sans":"sans", "Serif":"serif", "Mono":"mono"}
    wm_settings = normalize_settings({
        "enabled": wm_enabled,
        "text": wm_text,
        "style": st.session_state["wm_style"],
        "opacity": wm_opacity,
        "angle": wm_angle,
        "size": wm_size,
        "color": wm_color,
        "bold": wm_bold,
        "font": font_map[wm_font_label],
        "stroke": wm_stroke,
        "pattern_gap": wm_gap,
    })

    # Preview uchun faqat birinchi rasmga bog'lanib qolmaymiz.  Biror rasm
    # (masalan DOCX ichidagi image14.jpeg) CRC yoki boshqa sabab bilan
    # o'qilmasa, keyingi sog'lom rasmga o'tamiz.
    preview_candidates = [q.get("image_b64") for q in questions if q.get("image_b64")]
    preview_source = None
    preview_source_index = None
    preview_errors = []
    for _idx, _candidate in enumerate(preview_candidates):
        try:
            _raw = base64.b64decode(_candidate, validate=True)
            # PIL header/load tekshiruvi: preview uchun haqiqatan o'qiladigan
            # rasm ekanini oldindan tekshiramiz.
            with Image.open(io.BytesIO(_raw)) as _im:
                _im.verify()
            preview_source = _candidate
            preview_source_index = _idx
            break
        except Exception as _exc:
            preview_errors.append(f"{_idx + 1}-rasm: {_exc}")

    p1, p2, p3 = st.columns(3)
    with p1:
        if st.button("👁️ Preview", use_container_width=True, type="primary"):
            st.session_state["wm_show_preview"] = True
    with p2:
        if st.button("🔄 Standart", use_container_width=True):
            for k,v in DEFAULT_SETTINGS.items(): st.session_state[f"wm_{k}"] = v
            st.rerun()
    with p3:
        if st.button("🙈 Yopish", use_container_width=True):
            st.session_state["wm_show_preview"] = False

    if st.session_state.get("wm_show_preview"):
        if preview_source:
            try:
                _preview = preview_bytes(base64.b64decode(preview_source), wm_settings)
                st.image(
                    _preview,
                    caption=f"👁️ Preview • rasm {preview_source_index + 1} • {wm_text} • {wm_style_label} • {wm_opacity}%",
                    use_container_width=True,
                )
                if preview_source_index and preview_errors:
                    st.info(
                        f"♻️ Birinchi {preview_source_index} ta rasm o'qilmadi; Preview avtomatik ravishda "
                        f"{preview_source_index + 1}-rasmga o'tdi."
                    )
            except Exception as e:
                st.warning(f"Preview yaratilmadi: {e}")
        elif preview_candidates:
            st.warning(
                "⚠️ Preview uchun rasmlar topildi, ammo ularning barchasi o'qilmadi. "
                "Bitta buzilgan rasm sabab butun tizim to'xtamaydi, lekin bu partiyada o'qiladigan rasm qolmagan."
            )
            with st.expander("Texnik tafsilotlar"):
                for _err in preview_errors:
                    st.code(_err)
        else:
            st.info("Preview uchun kamida bitta rasm kerak.")

    st.divider()

    # Har bir savol uchun variantlar ro'yxati session_state'da saqlanadi.
    #
    # MUHIM TUZATISH: har bir variant endi index (0,1,2...) emas, balki
    # BARQAROR, hech qachon o'zgarmaydigan ID (uuid) orqali kuzatiladi.
    # Oldingi versiyada widget key'lari "opt_{savol}_{index}" edi - biror
    # variant o'chirilganda pastdagilarning INDEXI siljib qolgani uchun,
    # Streamlit ularni "xuddi shu key" deb hisoblab ESKI matnni ko'rsatib
    # qo'yardi (go'yo variant o'zgarib/almashib ketgandek ko'rinardi).
    # Endi har bir variant {"id": "...", "text": "..."} ko'rinishida
    # saqlanadi va widget key'i shu barqaror id'ga bog'lanadi - shuning
    # uchun o'chirish/qo'shish paytida boshqa variantlar hech qachon
    # "sakrab" yoki almashib qolmaydi.
    import uuid as _uuid

    for i, q in enumerate(questions):
        opts_key = f"opts_{i}"
        if opts_key not in st.session_state:
            # Bo'sh (yoki faqat probel) variantlarni avtomatik olib tashlaymiz -
            # OCR ba'zan "D)" kabi bo'sh quti chiqarib qo'yishi mumkin, foydalanuvchi
            # har safar qo'lda o'chirib o'tirmasin.
            raw_opts = q.get("options", [])
            non_empty_opts = [o for o in raw_opts if o and o.strip()]
            st.session_state[opts_key] = [
                {"id": _uuid.uuid4().hex[:8], "text": o} for o in non_empty_opts
            ]
        correct_key = f"correct_{i}"
        if correct_key not in st.session_state:
            ci = q.get("correct_index")
            # correct_index ham bo'sh variantlar olib tashlangandan keyingi
            # yangi ro'yxatga mos ravishda qayta hisoblanadi
            raw_opts = q.get("options", [])
            if ci is not None and 0 <= ci < len(raw_opts):
                removed_before = sum(
                    1 for o in raw_opts[:ci] if not (o and o.strip())
                )
                ci = ci - removed_before if raw_opts[ci] and raw_opts[ci].strip() else 0
            # correct_key endi variant ID'sini saqlaydi (index emas) -
            # shunda variantlar tartibi o'zgarganda ham to'g'ri javob
            # "adashib" qolmaydi.
            opts_list = st.session_state[opts_key]
            ci = ci if ci is not None else 0
            st.session_state[correct_key] = (
                opts_list[ci]["id"] if opts_list and 0 <= ci < len(opts_list) else None
            )

    def _remove_option(q_index, opt_id):
        opts_key = f"opts_{q_index}"
        correct_key = f"correct_{q_index}"
        opts_list = st.session_state[opts_key]
        if len(opts_list) <= 2:
            return
        # Har bir variantning joriy matnini widget'lardan (agar foydalanuvchi
        # tahrirlagan bo'lsa) o'qib olamiz - id barqaror bo'lgani uchun bu
        # doim to'g'ri variantga mos keladi.
        for opt in opts_list:
            opt["text"] = st.session_state.get(f"opt_{q_index}_{opt['id']}", opt["text"])
        new_list = [opt for opt in opts_list if opt["id"] != opt_id]
        st.session_state[opts_key] = new_list
        # O'chirilgan variantning widget key'ini ham tozalaymiz
        st.session_state.pop(f"opt_{q_index}_{opt_id}", None)
        # Agar o'chirilgan variant to'g'ri javob bo'lsa - birinchi qolganini
        # to'g'ri javob qilib belgilaymiz
        if st.session_state.get(correct_key) == opt_id:
            st.session_state[correct_key] = new_list[0]["id"] if new_list else None

    def _add_option(q_index):
        opts_key = f"opts_{q_index}"
        opts_list = st.session_state[opts_key]
        for opt in opts_list:
            opt["text"] = st.session_state.get(f"opt_{q_index}_{opt['id']}", opt["text"])
        new_id = _uuid.uuid4().hex[:8]
        opts_list.append({"id": new_id, "text": ""})
        st.session_state[opts_key] = opts_list

    def _option_height(text: str) -> int:
        """Variant matni uzunligiga qarab textarea balandligini hisoblaydi -
        qisqa variant 1 qatorga ixcham sig'adi, uzun variant esa (10
        qatorgacha) avtomatik kengayadi. Shunda hech qanday scrollbar yoki
        kesilgan matn qolmaydi, va qisqa variantlar bo'sh joy egallamaydi."""
        chars_per_line = 38  # taxminan shu ustun kengligiga to'g'ri keladi
        text = text or ""
        # Foydalanuvchi Enter bosgan qatorlarni ham hisobga olamiz
        wrapped_lines = 0
        for line in text.split("\n"):
            wrapped_lines += max(1, -(-len(line) // chars_per_line))  # ceil
        lines = max(1, min(10, wrapped_lines))
        line_h = 24  # bitta matn qatorining taxminiy pikselli balandligi
        padding = 22  # textarea ichki padding + border
        return max(68, lines * line_h + padding)

    edited_questions = []
    for i, q in enumerate(questions):
        # "edit.html" uslubidagi doim-ochiq karta: st.expander o'rniga -
        # chunki expander holati (ochiq/yopiq) foydalanuvchi bosgan joyni
        # keyingi rerun'da eslab qololmaydi va tasodifan yopilib qoladi.
        # st.container(border=True) esa hech qachon o'z-o'zidan yopilmaydi -
        # karta har doim ochiq turadi, hech narsa yo'qolmaydi.
        num_label = f"{i + 1}-savol"
        card = st.container(border=True)
        with card:
            hdr_col1, hdr_col2 = st.columns([5, 2])
            with hdr_col1:
                st.markdown(
                    f'<span class="tp-badge">{num_label}</span>', unsafe_allow_html=True
                )
            with hdr_col2:
                if not q.get("success", True):
                    st.markdown(
                        '<span class="tp-badge warn">⚠️ Tekshiring</span>',
                        unsafe_allow_html=True,
                    )
            if q.get("error"):
                st.warning(f"OCR ogohlantirishi: {q['error']}")

            # --- Rasm bilan ishlash: yoqish/o'chirish + kerakli qismini kesish ---
            use_image_key = f"use_image_{i}"
            crop_box_key = f"crop_box_{i}"
            final_image_b64 = None

            if q.get("image_b64"):
                if use_image_key not in st.session_state:
                    st.session_state[use_image_key] = True

                # Yakuniy (Word faylga tushadigan) rasm alohida saqlanadi.
                # Bu birinchi marta ishga tushganda ORIGINAL rasmga teng bo'ladi
                # va faqat foydalanuvchi "Kesish" tugmasini bossagina o'zgaradi -
                # sichqonchani sudrab yurishning o'zi hech narsani kesib qo'ymaydi.
                final_image_key = f"final_image_{i}"
                if final_image_key not in st.session_state:
                    st.session_state[final_image_key] = q["image_b64"]

                crop_mode_key = f"crop_mode_{i}"
                if crop_mode_key not in st.session_state:
                    st.session_state[crop_mode_key] = False

                st.checkbox(
                    "🖼️ Bu savolga rasm qo'shilsin (Word faylda)",
                    key=use_image_key,
                )

                if st.session_state[use_image_key]:
                    try:
                        orig_bytes = base64.b64decode(q["image_b64"])
                        orig_img = Image.open(io.BytesIO(orig_bytes)).convert("RGB")

                        current_final_bytes = base64.b64decode(st.session_state[final_image_key])
                        is_cropped = st.session_state[final_image_key] != q["image_b64"]

                        st.image(
                            current_final_bytes,
                            caption="Hozir Word faylga shu rasm tushadi"
                            + (" (kesilgan)" if is_cropped else " (original)"),
                            width=250,
                        )

                        # Kerakli (foto) qismini avtomatik aniqlashga harakat qilamiz -
                        # topilsa, foydalanuvchi bitta tugma bosish bilan (cropper
                        # oynasini ochmasdan) o'sha qismini qabul qilishi mumkin.
                        auto_box_key = f"auto_box_{i}"
                        if auto_box_key not in st.session_state:
                            st.session_state[auto_box_key] = detect_photo_region(orig_img)
                        auto_box = st.session_state[auto_box_key]

                        if auto_box:
                            col_auto_btn, col_crop_btn, col_reset_btn = st.columns(3)
                        else:
                            col_auto_btn = None
                            col_crop_btn, col_reset_btn = st.columns(2)

                        if auto_box:
                            with col_auto_btn:
                                if st.button("✨ Avtomatik kesish", key=f"auto_crop_{i}"):
                                    ax0, ay0, ax1, ay1 = auto_box
                                    auto_cropped = orig_img.crop((ax0, ay0, ax1, ay1))
                                    buf = io.BytesIO()
                                    auto_cropped.convert("RGB").save(buf, format="JPEG", quality=90)
                                    st.session_state[final_image_key] = base64.b64encode(buf.getvalue()).decode("ascii")
                                    st.session_state[crop_mode_key] = False
                                    st.rerun()
                        with col_crop_btn:
                            if st.button("✂️ Qo'lda kesish", key=f"open_crop_{i}"):
                                st.session_state[crop_mode_key] = True
                        with col_reset_btn:
                            if is_cropped and st.button("↩️ Original", key=f"reset_crop_{i}"):
                                st.session_state[final_image_key] = q["image_b64"]
                                st.session_state[crop_mode_key] = False
                                st.rerun()

                        if st.session_state[crop_mode_key]:
                            # auto_box yuqorida allaqachon hisoblangan
                            if auto_box:
                                st.caption(
                                    "✨ Kerakli qism avtomatik aniqlandi (pastda ko'k "
                                    "chegara bilan belgilangan). Kerak bo'lsa chegarani "
                                    "qo'lda sudrab tuzating, so'ng 'Shu ko'rinishda "
                                    "saqlash' tugmasini bosing:"
                                )
                            else:
                                st.caption(
                                    "Kerakli qismini chegaralarni sudrab tanlang, "
                                    "keyin pastdagi 'Shu ko'rinishda saqlash' tugmasini bosing:"
                                )

                            if _CROPPER_AVAILABLE:
                                default_coords = None
                                if auto_box:
                                    ax0, ay0, ax1, ay1 = auto_box
                                    # streamlit-cropper eski/yangi versiyalarida
                                    # default_coords format tartibi farq qilishi
                                    # mumkin - shuning uchun xato chiqsa,
                                    # parametrsiz (butun rasm) holatga qaytamiz.
                                    default_coords = (ax0, ax1, ay0, ay1)

                                try:
                                    # streamlit-cropper katta (masalan, telefon
                                    # kamerasidan olingan 3000px+) rasmni to'g'ridan-
                                    # to'g'ri qabul qilganda, ba'zan uning ichki
                                    # kenglik-hisoblashi tor (mobil) ekranga to'g'ri
                                    # moslashmay, kesish maydoni o'ngga tashqariga
                                    # chiqib ketadi. Buning oldini olish uchun
                                    # cropper'ga har doim kichraytirilgan nusxa
                                    # beramiz, keyin tanlangan koordinatalarni
                                    # asl o'lchamga qaytarib masshtablaymiz - shunda
                                    # ekranga har doim sig'adi, natija sifati esa
                                    # (original piksellarda kesiladi) pasaymaydi.
                                    _CROP_DISPLAY_MAX_W = 700
                                    ow, oh = orig_img.size
                                    if ow > _CROP_DISPLAY_MAX_W:
                                        _scale = _CROP_DISPLAY_MAX_W / ow
                                        display_img = orig_img.resize(
                                            (_CROP_DISPLAY_MAX_W, max(1, round(oh * _scale)))
                                        )
                                    else:
                                        _scale = 1.0
                                        display_img = orig_img

                                    display_default_coords = None
                                    if default_coords is not None:
                                        dx0, dx1, dy0, dy1 = default_coords
                                        display_default_coords = (
                                            round(dx0 * _scale), round(dx1 * _scale),
                                            round(dy0 * _scale), round(dy1 * _scale),
                                        )

                                    preview_crop = st_cropper(
                                        display_img,
                                        realtime_update=True,
                                        box_color="#FF4B4B",
                                        aspect_ratio=None,
                                        return_type="box",
                                        default_coords=display_default_coords,
                                        key=f"cropper_{i}",
                                    )
                                    # Kichraytirilgan nusxada tanlangan koordinatalarni
                                    # asl (to'liq o'lchamdagi) rasmga qaytarib
                                    # masshtablaymiz.
                                    preview_crop = {
                                        "left": round(preview_crop["left"] / _scale),
                                        "top": round(preview_crop["top"] / _scale),
                                        "width": round(preview_crop["width"] / _scale),
                                        "height": round(preview_crop["height"] / _scale),
                                    }
                                except Exception:
                                    preview_crop = st_cropper(
                                        orig_img,
                                        realtime_update=True,
                                        box_color="#FF4B4B",
                                        aspect_ratio=None,
                                        return_type="box",
                                        key=f"cropper_{i}",
                                    )
                                # streamlit-cropper "box" qiymatlarini ORIGINAL rasm
                                # o'lchamiga moslab (ekranga moslab kichraytirilgan
                                # bo'lsa ham) avtomatik qaytaradi, shuning uchun
                                # qo'shimcha masshtablash shart emas.
                                left = preview_crop["left"]
                                top = preview_crop["top"]
                                right = left + preview_crop["width"]
                                bottom = top + preview_crop["height"]
                                preview_img = orig_img.crop((left, top, right, bottom))
                            else:
                                # Zaxira variant: streamlit-cropper o'rnatilmagan bo'lsa,
                                # slayder orqali chegara belgilab kesish. Slayderlarning
                                # boshlang'ich qiymati ham avtomatik aniqlangan hududga
                                # o'rnatiladi (topilgan bo'lsa).
                                st.caption(
                                    "⚠️ Aniqroq (sichqoncha bilan) kesish uchun "
                                    "`pip install streamlit-cropper` kerak. Hozircha "
                                    "slayder orqali kesish mumkin:"
                                )
                                w, h = orig_img.size
                                if auto_box:
                                    ax0, ay0, ax1, ay1 = auto_box
                                else:
                                    ax0, ay0, ax1, ay1 = 0, 0, w, h
                                left, right = st.slider(
                                    "Chap - o'ng chegara", 0, w, (ax0, ax1), key=f"cropx_{i}"
                                )
                                top, bottom = st.slider(
                                    "Yuqori - past chegara", 0, h, (ay0, ay1), key=f"cropy_{i}"
                                )
                                preview_img = orig_img.crop((left, top, right, bottom))

                            st.image(preview_img, caption="Kesish natijasi (hali saqlanmagan)", width=250)

                            col_save_btn, col_cancel_btn = st.columns(2)
                            with col_save_btn:
                                if st.button("✅ Shu ko'rinishda saqlash", key=f"save_crop_{i}", type="primary"):
                                    buf = io.BytesIO()
                                    preview_img.convert("RGB").save(buf, format="JPEG", quality=90)
                                    st.session_state[final_image_key] = base64.b64encode(buf.getvalue()).decode("ascii")
                                    st.session_state[crop_mode_key] = False
                                    st.rerun()
                            with col_cancel_btn:
                                if st.button("❌ Bekor qilish", key=f"cancel_crop_{i}"):
                                    st.session_state[crop_mode_key] = False
                                    st.rerun()

                        final_image_b64 = st.session_state[final_image_key]
                    except Exception as e:
                        st.warning(f"Rasmni ko'rsatishda xatolik: {e}")
                        final_image_b64 = q.get("image_b64")
                else:
                    st.caption("🚫 Bu savol uchun rasm Word faylga qo'shilmaydi.")

            question_text = st.text_area(
                "Savol matni", value=q.get("question", ""), key=f"q_{i}", height=80,
            )

            opts_key = f"opts_{i}"
            correct_key = f"correct_{i}"
            current_options = st.session_state[opts_key]

            # "edit.html" uslubi: har bir variant o'z qatorida - chap tomonda
            # A/B/C/D belgi-tugma (bosilsa o'sha variant TO'G'RI javob bo'ladi
            # va yashil rangga o'tadi), o'rtada tahrirlanadigan matn, ENG
            # O'NGDA (qatorning oxirida) o'chirish (✕) tugmasi - xuddi
            # edit.html'dagi kabi. Har bir variant BARQAROR id orqali
            # kuzatilgani uchun (yuqoriga qarang) - biror variantni o'chirish
            # boshqa qatorlarning matnini yoki holatini HECH QACHON
            # "surib" yubormaydi, faqat o'sha bitta qator yo'qoladi.
            st.caption("🔘 To'g'ri javobni belgilash uchun doirani bosing • ✕ variantni o'chiradi")
            edited_options = []
            for j, opt in enumerate(current_options):
                opt_id = opt["id"]
                is_correct = (st.session_state[correct_key] == opt_id)
                col_lbl, col_opt, col_del = st.columns([1, 8, 1], vertical_alignment="center")
                with col_lbl:
                    lbl_type = "primary" if is_correct else "secondary"
                    if st.button(
                        " ",
                        key=f"setok_{i}_{opt_id}",
                        type=lbl_type,
                        use_container_width=True,
                        help="To'g'ri javob sifatida belgilash",
                    ):
                        st.session_state[correct_key] = opt_id
                        st.rerun()
                with col_opt:
                    _current_text = st.session_state.get(f"opt_{i}_{opt_id}", opt["text"])
                    val = st.text_area(
                        f"Variant {chr(65 + j)}",
                        value=opt["text"],
                        key=f"opt_{i}_{opt_id}",
                        label_visibility="collapsed",
                        placeholder=f"Variant {chr(65 + j)}",
                        height=_option_height(_current_text),
                    )
                    edited_options.append(val)
                with col_del:
                    st.button(
                        "✕", key=f"del_{i}_{opt_id}",
                        help=f"{chr(65 + j)} variantni o'chirish",
                        on_click=_remove_option, args=(i, opt_id),
                        disabled=len(current_options) <= 2,
                        use_container_width=True,
                    )

            st.button(
                "➕ Yangi variant qo'shish", key=f"add_{i}",
                on_click=_add_option, args=(i,),
                use_container_width=True,
                disabled=len(current_options) >= 8,
            )

            # correct_key variant ID sifatida saqlanadi - Word fayl uchun
            # esa yakuniy INDEX kerak, shuning uchun joriy tartibga qarab
            # id'ni indexga aylantiramiz.
            correct_id = st.session_state[correct_key]
            chosen = next(
                (idx for idx, opt in enumerate(current_options) if opt["id"] == correct_id),
                0 if edited_options else None,
            )

            edited_questions.append({
                "question": question_text,
                "options": edited_options,
                "correct_index": chosen,
                # Foydalanuvchi tanlagan/kesgan yakuniy rasm (yoki checkbox
                # o'chirilgan bo'lsa - None, ya'ni Word faylga rasm qo'shilmaydi)
                "image_b64": final_image_b64,
            })

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        skip_review = st.button("▶️ Tekshirmasdan davom etish", use_container_width=True)
    with col2:
        confirm = st.button("✅ Tayyor - Word yaratish", type="primary", use_container_width=True)

    if skip_review or confirm:
        final_questions = questions if skip_review else edited_questions
        clean_filename = (filename or default_name).strip() or "natija"
        if not clean_filename.lower().endswith(".docx"):
            clean_filename += ".docx"

        out_path = f"/tmp/{session_id}_final.docx"
        # Joriy watermark sozlamalarini sessiyaga saqlaymiz - bot va keyingi
        # qayta ochilishlarda aynan shu konfiguratsiya ishlatiladi.
        session_store.update_session(session_id, watermark_settings=wm_settings)
        build_docx(
            final_questions,
            out_path,
            title=clean_filename.replace(".docx", ""),
            watermark_settings=wm_settings,
        )

        with open(out_path, "rb") as _f:
            generated_docx_b64 = base64.b64encode(_f.read()).decode("ascii")
        session_store.update_session(
            session_id,
            mode="docx",
            docx_b64=generated_docx_b64,
            default_filename=clean_filename,
            questions=final_questions,
            status="ready_for_review",
        )

        with st.spinner("Word fayl yaratilmoqda va chatga yuborilmoqda..."):
            chat_id = data.get("telegram_chat_id")
            ok = send_docx_to_telegram(chat_id, out_path, clean_filename, session_id=session_id)

        if ok:
            st.success("✅ Word fayl chatga yuborildi. Endi fayl ostidagi 🔍 Tekshirish yoki 🏷️ Emblema almashtirish tugmasidan kerakli alohida sahifani oching.")
        else:
            st.error("Fayl yuborilmadi. Iltimos qaytadan urinib ko'ring.")


if __name__ == "__main__":
    main()
