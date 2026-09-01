"""
session_store.py
-----------------
Bot (aiogram) va Streamlit Mini App orasida ma'lumot almashish uchun saqlash
qatlami. Uchta ishlash rejimi mavjud, ustuvorlik tartibida tanlanadi:

1) SUPABASE_URL + SUPABASE_KEY berilgan bo'lsa -> Supabase (Postgres, REST
   API orqali) ishlatiladi. BU ENG ISHONCHLI VARIANT: Streamlit Cloud
   konteyneri qayta ishga tushsa yoki "uyqudan uyg'onsa" ham, yangi kod
   deploy qilinsa ham - sessiyalar Supabase'da saqlanib qoladi, chunki bu
   butunlay tashqi, doimiy (persistent) ma'lumotlar bazasi.
2) SESSION_STORE_DIR muhit o'zgaruvchisi berilgan bo'lsa -> fayl-asosli
   saqlash (vaqtinchalik diskka JSON). Streamlit Cloud konteyneri qayta
   ishga tushirilganda yo'qolib ketishi mumkin - shuning uchun zaxira variant.
3) Hech biri berilmasa -> oddiy RAM'dagi dict. Faqat bot va Streamlit BITTA
   jarayonda ishlaganda ishlatilishi kerak.

Har uchala holatda ham: session ishi TUGAGANDAN keyin (Word fayl yuborilgach)
clear_session() albatta chaqirilishi kerak.

--- Supabase jadvalini yaratish uchun SQL (bir marta, Supabase SQL editor'da) ---
create table if not exists bot_sessions (
    session_id text primary key,
    data jsonb not null,
    created_at double precision not null
);
"""

import os
import json
import time
import uuid
import threading
import tempfile

try:
    import requests as _requests
except ImportError:  # requirements.txt'da bor, lekin himoya sifatida
    _requests = None

SESSION_STORE_DIR = os.environ.get('SESSION_STORE_DIR', '')
SESSION_TTL_SECONDS = 60 * 60 * 3  # 3 soatdan keyin ishlatilmagan sessiyalar eskirgan hisoblanadi

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '') or os.environ.get('SUPABASE_SERVICE_KEY', '')
SUPABASE_TABLE = os.environ.get('SUPABASE_SESSIONS_TABLE', 'bot_sessions')
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY and _requests)
SUPABASE_ASSET_TABLE = os.environ.get('SUPABASE_ASSET_TABLE', 'bot_user_assets')

_lock = threading.Lock()
_memory_store = {}  # session_id -> dict
_user_assets = {}   # telegram_chat_id -> persistent-ish asset metadata


def new_session_id():
    return uuid.uuid4().hex[:16]


def _file_path(session_id):
    base = SESSION_STORE_DIR or tempfile.gettempdir()
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"session_{session_id}.json")


def _supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def _supabase_upsert(session_id, data):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = _supabase_headers()
    headers["Prefer"] = "resolution=merge-duplicates"
    payload = {"session_id": session_id, "data": data, "created_at": data.get("created_at", time.time())}
    resp = _requests.post(url, headers=headers, json=payload, timeout=10)
    resp.raise_for_status()


def _supabase_get(session_id):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = _supabase_headers()
    params = {"session_id": f"eq.{session_id}", "select": "data"}
    resp = _requests.get(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    return rows[0]["data"]


def _supabase_delete(session_id):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = _supabase_headers()
    params = {"session_id": f"eq.{session_id}"}
    resp = _requests.delete(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()


def _supabase_list_all():
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
    headers = _supabase_headers()
    params = {"select": "session_id,created_at"}
    resp = _requests.get(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()




# Image ZIP assetlar ataylab faqat RAM'da saqlanadi.
# Multipart uploadda har bir ZIP qismi darhol ochiladi va rasmlar bitta
# `images` ro'yxatiga birlashtiriladi; ZIP baytlarining o'zi saqlanmaydi.
# Bu Streamlit + bot bitta Python processida ishlaganda umumiy cache vazifasini bajaradi.
# Restart/deploy bo'lsa RAM tozalanadi va ZIP qismlari qayta yuboriladi.
_user_assets = {}   # str(chat_id) -> {"image_zip": {images, part_names, part_count, total_bytes, ...}}


def get_user_asset(chat_id, name="image_zip"):
    """RAM'dagi foydalanuvchi assetini qaytaradi.

    Image ZIP uchun Telegram file_id emas, ZIP baytlari va ZIPdan chiqarilgan
    rasmlar RAM'da saqlanadi. Shu sababli Streamlit qayta yuklanganda ham shu
    process tirik bo'lsa, web sahifa rasmlarni bevosita RAM'dan oladi.
    """
    if chat_id is None:
        return None
    with _lock:
        data = _user_assets.get(str(chat_id), {})
        return data.get(name)


def set_user_asset(chat_id, name, asset):
    """Assetni faqat RAM'ga yozadi. Image ZIP cache uchun disk/Supabase ishlatilmaydi."""
    if chat_id is None:
        return asset
    key = str(chat_id)
    with _lock:
        current = _user_assets.get(key, {})
        current[name] = asset
        current["updated_at"] = time.time()
        _user_assets[key] = current
    return asset


def clear_user_asset(chat_id, name="image_zip"):
    """Foydalanuvchining RAM'dagi assetini o'chiradi."""
    if chat_id is None:
        return False
    with _lock:
        key = str(chat_id)
        current = _user_assets.get(key)
        if not current or name not in current:
            return False
        current.pop(name, None)
        current["updated_at"] = time.time()
        if current:
            _user_assets[key] = current
        else:
            _user_assets.pop(key, None)
        return True


def load_local_user_asset(chat_id):
    """Eski API mosligi uchun RAM assetlarini qaytaradi."""
    if chat_id is None:
        return {}
    return _user_assets.get(str(chat_id), {})


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
        if USE_SUPABASE:
            _supabase_upsert(session_id, data)
        elif SESSION_STORE_DIR:
            path = _file_path(session_id)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        else:
            _memory_store[session_id] = data


def get_session(session_id):
    with _lock:
        if USE_SUPABASE:
            return _supabase_get(session_id)
        elif SESSION_STORE_DIR:
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
    """Sessiya ishi tugagach - saqlash joyidan butunlay o'chiradi."""
    with _lock:
        if USE_SUPABASE:
            _supabase_delete(session_id)
        elif SESSION_STORE_DIR:
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
        if USE_SUPABASE:
            try:
                rows = _supabase_list_all()
            except Exception:
                return
            for row in rows:
                if now - row.get("created_at", 0) > SESSION_TTL_SECONDS:
                    try:
                        _supabase_delete(row["session_id"])
                    except Exception:
                        continue
        elif SESSION_STORE_DIR:
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

