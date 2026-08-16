"""
session_store.py
-----------------
Bot (aiogram) va Streamlit Mini App orasida ma'lumot almashish uchun oddiy,
RAM'dagi (xotiradagi) global saqlash joyi.

MUHIM: Bu global dict faqat BITTA server jarayoni (process) ichida ishlaydi.
Agar botni va Streamlit'ni ALOHIDA-ALOHIDA serverlarda (masalan botni bitta
Render/VPS'da, Streamlit'ni boshqa joyda - Streamlit Cloud'da) ishga
tushirsangiz, bu ikkalasi bir xil xotirani KO'RA OLMAYDI!

Shuning uchun ikkita ishlash rejimi bor:
1) Agar bot va Streamlit BITTA jarayonda (masalan bitta VPS'da, ikkalasi ham
   bir xil Python muhitida, masalan Streamlit threading orqali ishga tushirilsa)
   - shu holda oddiy global dict YETARLI.
2) Agar ular ALOHIDA serverlarda bo'lsa - session ma'lumotini JSON fayl
   ko'rinishida umumiy diskka (yoki vaqtinchalik bulut saqlash joyiga) yozish
   kerak bo'ladi. Quyidagi FileSessionStore shu holat uchun zaxira variant.

Standart holatda: agar SESSION_STORE_DIR muhit o'zgaruvchisi berilgan bo'lsa,
fayl-asosli saqlash ishlatiladi (bu Streamlit Cloud + tashqi bot kabi holatlar
uchun ishonchliroq). Aks holda - RAM'dagi dict ishlatiladi.

Har ikkala holatda ham: session ishi TUGAGANDAN keyin (Word fayl yuborilgach)
clear_session() albatta chaqirilishi kerak - shunda RAM/disk tozalanadi.
"""

import os
import json
import time
import uuid
import threading
import tempfile

SESSION_STORE_DIR = os.environ.get('SESSION_STORE_DIR', '')
SESSION_TTL_SECONDS = 60 * 60 * 3  # 3 soatdan keyin ishlatilmagan sessiyalar eskirgan hisoblanadi

_lock = threading.Lock()
_memory_store = {}  # session_id -> dict


def new_session_id():
    return uuid.uuid4().hex[:16]


def _file_path(session_id):
    base = SESSION_STORE_DIR or tempfile.gettempdir()
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"session_{session_id}.json")


def create_session(session_id, telegram_user_id, telegram_chat_id, default_filename="natija"):
    """Yangi bo'sh sessiya yaratadi."""
    data = {
        "session_id": session_id,
        "telegram_user_id": telegram_user_id,
        "telegram_chat_id": telegram_chat_id,
        "created_at": time.time(),
        "status": "collecting",  # collecting -> processing -> ready_for_review -> done
        "default_filename": default_filename,
        "images": [],       # base64 kodlangan rasm baytlari ro'yxati (vaqtinchalik)
        "questions": [],    # process qilingandan keyingi natijalar ro'yxati
    }
    _save_session(session_id, data)
    return data


def _save_session(session_id, data):
    with _lock:
        if SESSION_STORE_DIR:
            path = _file_path(session_id)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        else:
            _memory_store[session_id] = data


def get_session(session_id):
    with _lock:
        if SESSION_STORE_DIR:
            path = _file_path(session_id)
            if not os.path.exists(path):
                return None
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return _memory_store.get(session_id)


def update_session(session_id, **fields):
    data = get_session(session_id)
    if data is None:
        return None
    data.update(fields)
    _save_session(session_id, data)
    return data


def clear_session(session_id):
    """Sessiya ishi tugagach - RAM/diskdan butunlay o'chiradi."""
    with _lock:
        if SESSION_STORE_DIR:
            path = _file_path(session_id)
            if os.path.exists(path):
                os.remove(path)
        else:
            _memory_store.pop(session_id, None)


def cleanup_expired_sessions():
    """Uzoq vaqt ishlatilmagan (TTL'dan oshgan) sessiyalarni tozalaydi.
    Buni davriy ravishda (masalan har soatda bir marta) chaqirish tavsiya etiladi."""
    now = time.time()
    with _lock:
        if SESSION_STORE_DIR:
            base = SESSION_STORE_DIR or tempfile.gettempdir()
            if not os.path.isdir(base):
                return
            for fname in os.listdir(base):
                if not fname.startswith("session_") or not fname.endswith(".json"):
                    continue
                path = os.path.join(base, fname)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if now - data.get("created_at", 0) > SESSION_TTL_SECONDS:
                        os.remove(path)
                except Exception:
                    continue
        else:
            expired = [
                sid for sid, data in _memory_store.items()
                if now - data.get("created_at", 0) > SESSION_TTL_SECONDS
            ]
            for sid in expired:
                _memory_store.pop(sid, None)
