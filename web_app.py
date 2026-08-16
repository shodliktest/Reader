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
import requests
import streamlit as st

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

    # Har bir savolni tahrirlash uchun forma
    edited_questions = []
    for i, q in enumerate(questions):
        with st.expander(
            f"{i + 1}). {q['question'][:60]}{'...' if len(q['question']) > 60 else ''}"
            + ("" if q.get("success") else "  ⚠️ Tekshiring"),
            expanded=not q.get("success", True),
        ):
            if q.get("error"):
                st.warning(f"OCR ogohlantirishi: {q['error']}")

            if q.get("image_b64"):
                try:
                    import base64 as _b64
                    st.image(_b64.b64decode(q["image_b64"]), caption="Asl rasm", width=250)
                except Exception:
                    pass

            question_text = st.text_area(
                "Savol matni", value=q.get("question", ""), key=f"q_{i}", height=80,
            )

            options = q.get("options", [])
            edited_options = []
            for j, opt in enumerate(options):
                edited_options.append(
                    st.text_input(f"Variant {chr(65 + j)}", value=opt, key=f"opt_{i}_{j}")
                )

            correct_index = q.get("correct_index")
            if edited_options:
                option_labels = [f"{chr(65 + j)}) {t[:40]}" for j, t in enumerate(edited_options)]
                default_idx = correct_index if correct_index is not None and correct_index < len(edited_options) else 0
                chosen = st.radio(
                    "To'g'ri javob",
                    options=list(range(len(edited_options))),
                    format_func=lambda idx: option_labels[idx],
                    index=default_idx,
                    key=f"correct_{i}",
                )
            else:
                chosen = None

            edited_questions.append({
                "question": question_text,
                "options": edited_options,
                "correct_index": chosen,
                # Asl rasm - Word faylga o'sha savoldan oldin qo'shiladi
                "image_b64": q.get("image_b64"),
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
