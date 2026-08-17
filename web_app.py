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
from processor import detect_photo_region

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


def send_docx_to_telegram(chat_id, file_path, filename):
    """Tayyor Word faylni Telegram Bot API orqali to'g'ridan-to'g'ri
    foydalanuvchi chatiga yuboradi."""
    if not BOT_TOKEN:
        st.error("BOT_TOKEN sozlanmagan - Word faylni Telegram'ga yubora olmayman.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    with open(file_path, 'rb') as f:
        files = {'document': (filename, f, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')}
        data = {'chat_id': chat_id}
        resp = requests.post(url, data=data, files=files, timeout=60)

    if resp.status_code != 200:
        st.error(f"Telegram'ga yuborishda xatolik: {resp.text}")
        return False
    return True


def main():
    ensure_bot_running()

    query_params = st.query_params
    session_id = query_params.get("session_id", "")

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

    if data.get("status") == "done":
        st.success("Bu partiya allaqachon yakunlangan. Word fayl chatga yuborilgan.")
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

    st.divider()

    # Har bir savol uchun variantlar ro'yxati session_state'da saqlanadi -
    # shunda "o'chirish" tugmasi bosilganda ro'yxat qayta tartiblanadi
    # (A, B, C, D tartibi hech qachon buzilmaydi - kim o'chirilsa, pastdagilar
    # yuqoriga ko'tariladi) va Streamlit qayta chizilganda yo'qolib qolmaydi.
    for i, q in enumerate(questions):
        opts_key = f"opts_{i}"
        if opts_key not in st.session_state:
            # Bo'sh (yoki faqat probel) variantlarni avtomatik olib tashlaymiz -
            # OCR ba'zan "D)" kabi bo'sh quti chiqarib qo'yishi mumkin, foydalanuvchi
            # har safar qo'lda o'chirib o'tirmasin.
            raw_opts = q.get("options", [])
            non_empty_opts = [o for o in raw_opts if o and o.strip()]
            st.session_state[opts_key] = non_empty_opts
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
            st.session_state[correct_key] = ci if ci is not None else 0

    def _remove_option(q_index, opt_index):
        opts_key = f"opts_{q_index}"
        correct_key = f"correct_{q_index}"
        opts = st.session_state[opts_key]
        # Har bir variantning joriy matnini widget'lardan (agar foydalanuvchi
        # tahrirlagan bo'lsa) o'qib olamiz, keyin o'chiriladigan indexni tashlaymiz
        current_texts = [
            st.session_state.get(f"opt_{q_index}_{j}", opts[j]) for j in range(len(opts))
        ]
        old_correct = st.session_state.get(correct_key, 0)

        del current_texts[opt_index]

        # To'g'ri javob indexini yangi ro'yxatga moslab qayta hisoblaymiz
        if opt_index < old_correct:
            new_correct = old_correct - 1
        elif opt_index == old_correct:
            new_correct = 0
        else:
            new_correct = old_correct

        st.session_state[opts_key] = current_texts
        # Eski text_input key'larini tozalaymiz, aks holda Streamlit eski
        # qiymatlarni indexlar siljigandan keyin ham ko'rsatib qo'yishi mumkin
        for j in range(len(opts)):
            st.session_state.pop(f"opt_{q_index}_{j}", None)
        st.session_state[correct_key] = min(new_correct, max(len(current_texts) - 1, 0))

    def _add_option(q_index):
        opts_key = f"opts_{q_index}"
        current_texts = [
            st.session_state.get(f"opt_{q_index}_{j}", opts) for j, opts in enumerate(st.session_state[opts_key])
        ]
        current_texts.append("")
        st.session_state[opts_key] = current_texts

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
                                    preview_crop = st_cropper(
                                        orig_img,
                                        realtime_update=True,
                                        box_color="#FF4B4B",
                                        aspect_ratio=None,
                                        return_type="box",
                                        default_coords=default_coords,
                                        key=f"cropper_{i}",
                                    )
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
            # va yashil rangga o'tadi), o'rtada tahrirlanadigan matn, o'ngda
            # o'chirish tugmasi. Hech qanday tugma bosish variantlarni
            # "yopib" yoki tozalab qo'ymaydi - faqat session_state yangilanadi.
            st.caption("To'g'ri javobni belgilash uchun harfni bosing:")
            edited_options = []
            for j in range(len(current_options)):
                is_correct = (st.session_state[correct_key] == j)
                col_lbl, col_opt, col_del = st.columns([1, 6, 1])
                with col_lbl:
                    lbl_type = "primary" if is_correct else "secondary"
                    if st.button(
                        ("✅ " if is_correct else "") + chr(65 + j),
                        key=f"setok_{i}_{j}",
                        type=lbl_type,
                        use_container_width=True,
                        help="To'g'ri javob sifatida belgilash",
                    ):
                        st.session_state[correct_key] = j
                        st.rerun()
                with col_opt:
                    val = st.text_input(
                        f"Variant {chr(65 + j)}",
                        value=current_options[j],
                        key=f"opt_{i}_{j}",
                        label_visibility="collapsed",
                        placeholder=f"Variant {chr(65 + j)}",
                    )
                    edited_options.append(val)
                with col_del:
                    st.button(
                        "🗑️", key=f"del_{i}_{j}",
                        help=f"{chr(65 + j)} variantni o'chirish",
                        on_click=_remove_option, args=(i, j),
                        disabled=len(current_options) <= 2,
                        use_container_width=True,
                    )
                if is_correct:
                    st.markdown(
                        '<div style="margin:-.5rem 0 .5rem 2px">'
                        '<span class="tp-badge ok">✓ To\'g\'ri javob</span></div>',
                        unsafe_allow_html=True,
                    )

            st.button(
                "➕ Yangi variant qo'shish", key=f"add_{i}",
                on_click=_add_option, args=(i,),
                use_container_width=True,
                disabled=len(current_options) >= 8,
            )

            chosen = st.session_state[correct_key] if edited_options else None
            if chosen is not None and chosen >= len(edited_options):
                chosen = 0
                st.session_state[correct_key] = 0

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
        build_docx(final_questions, out_path, title=clean_filename.replace(".docx", ""))

        with st.spinner("Word fayl yaratilmoqda va chatga yuborilmoqda..."):
            chat_id = data.get("telegram_chat_id")
            ok = send_docx_to_telegram(chat_id, out_path, clean_filename)

        if ok:
            st.success("✅ Word fayl chatga muvaffaqiyatli yuborildi! Bu oynani yopishingiz mumkin.")
            session_store.clear_session(session_id)
        else:
            st.error("Fayl yuborilmadi. Iltimos qaytadan urinib ko'ring.")


if __name__ == "__main__":
    main()
