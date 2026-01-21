import streamlit as st
import asyncio
import os
import threading
import sqlite3
import pytz
import pandas as pd
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile
from aiogram.client.default import DefaultBotProperties
import yt_dlp
import time

# --- 1. SOZLAMALAR ---
try:
    BOT_TOKEN = st.secrets["BOT_TOKEN"]
    ADMIN_ID = int(st.secrets["ADMIN_ID"])
except Exception:
    st.error("❌ Secrets topilmadi! .streamlit/secrets.toml faylini tekshiring.")
    st.stop()

DB_FILE = "users.db"

# --- 2. BAZA FUNKSIYALARI ---
def init_db():
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users
                        (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT, join_date TEXT)''')

def add_user_to_db(user: types.User):
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        tz = pytz.timezone('Asia/Tashkent')
        now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?)", 
                     (user.id, user.full_name, user.username, now))

def get_all_users_data():
    try:
        with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
            return pd.read_sql_query("SELECT * FROM users", conn)
    except: return pd.DataFrame()

# --- 3. VIDEO YUKLASH (WORKER) ---
async def download_worker(queue, bot: Bot):
    while True:
        url, message = await queue.get()
        status_msg = None
        filename = None
        try:
            status_msg = await message.reply("⏳ **So'rovingiz navbatga olindi...**")
            
            os.makedirs("downloads", exist_ok=True)
            output_template = f"downloads/{message.from_user.id}_%(id)s.%(ext)s"

            ydl_opts = {
                'format': 'best[ext=mp4]/best',
                'outtmpl': output_template,
                'max_filesize': 50 * 1024 * 1024,
                'quiet': True,
                'no_warnings': True
            }

            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=True))
            filename = yt_dlp.YoutubeDL(ydl_opts).prepare_filename(info)

            # Fayl kengaytmasi o'zgargan bo'lsa tekshirish
            if not os.path.exists(filename):
                base = os.path.splitext(filename)[0]
                for f in os.listdir("downloads"):
                    if f.startswith(os.path.basename(base)):
                        filename = os.path.join("downloads", f)
                        break

            if os.path.exists(filename) and os.path.getsize(filename) <= 50 * 1024 * 1024:
                await status_msg.edit_text("🚀 **Telegramga yuklanmoqda...**")
                await message.answer_video(FSInputFile(filename), caption=f"🎬 **{info.get('title')}**\n\n🤖 @{(await bot.get_me()).username}")
                await status_msg.delete()
            else:
                await status_msg.edit_text("❌ Fayl juda katta yoki yuklanmadi.")
        except Exception as e:
            if status_msg: await status_msg.edit_text(f"❌ Xatolik: {str(e)[:50]}")
        finally:
            if filename and os.path.exists(filename): os.remove(filename)
            queue.task_done()

# --- 4. ASOSIY BOT PROTSESSI ---
async def main_bot():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    init_db()
    
    queue = asyncio.Queue()
    asyncio.create_task(download_worker(queue, bot))

    @dp.message(Command("start"))
    async def cmd_start(m: types.Message):
        add_user_to_db(m.from_user)
        await m.answer(f"👋 Salom {m.from_user.full_name}! YouTube link yuboring.")

    @dp.message(F.text.contains("youtube.com") | F.text.contains("youtu.be"))
    async def handle_youtube(m: types.Message):
        await queue.put((m.text, m))

    @dp.message(F.reply_to_message)
    async def reply_handler(m: types.Message):
        if m.from_user.id == ADMIN_ID and m.reply_to_message.forward_from:
            await bot.copy_message(m.reply_to_message.forward_from.id, m.chat.id, m.message_id)
            await m.answer("✅ Javob yuborildi.")

    @dp.message()
    async def forward_to_admin(m: types.Message):
        if m.from_user.id != ADMIN_ID:
            await m.forward(ADMIN_ID)
            await m.answer("📨 Adminga yetkazildi.")

    # MUHIM: set_wakeup_fd xatosini oldini olish uchun handle_signals=False
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, handle_signals=False)

# --- 5. SINGLETON RUNNER ---
def start_executor():
    if "bot_thread" not in st.session_state:
        # Eski threadni tekshirish
        for t in threading.enumerate():
            if t.name == "BotThread": return

        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(main_bot())

        thread = threading.Thread(target=run_async, name="BotThread", daemon=True)
        thread.start()
        st.session_state.bot_thread = True

start_executor()

# --- 6. ADMIN PANEL (STREAMLIT) ---
st.title("🤖 YouTube Bot Admin")
df = get_all_users_data()
st.metric("Foydalanuvchilar", len(df))

tab1, tab2 = st.tabs(["📢 Broadcast", "📊 Foydalanuvchilar"])

with tab1:
    text = st.text_area("Xabar matni:")
    if st.button("Yuborish"):
        stats = {"s": 0, "f": 0}
        async def do_broadcast():
            b = Bot(token=BOT_TOKEN)
            for uid in df['user_id']:
                try:
                    await b.send_message(uid, text)
                    stats["s"] += 1
                except: stats["f"] += 1
            await b.session.close()
        asyncio.run(do_broadcast())
        st.success(f"Muvaffaqiyatli: {stats['s']}, Xato: {stats['f']}")

with tab2:
    st.dataframe(df)
