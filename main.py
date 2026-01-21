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

# --- SETUP ---
try:
    BOT_TOKEN = st.secrets["BOT_TOKEN"]
    ADMIN_ID = int(st.secrets["ADMIN_ID"])
except:
    st.error("Secrets sozlanmagan!")
    st.stop()

DB_FILE = "users.db"

# --- DATABASE ---
def init_db():
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT, join_date TEXT)")

def add_user(u: types.User):
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        now = datetime.now(pytz.timezone('Asia/Tashkent')).strftime("%Y-%m-%d %H:%M")
        conn.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?)", (u.id, u.full_name, u.username, now))

# --- DOWNLOADER CORE ---
async def download_worker(queue, bot: Bot):
    while True:
        url, msg = await queue.get()
        status = await msg.reply("⏳ **Tahlil qilinmoqda...**")
        
        os.makedirs("downloads", exist_ok=True)
        unique_id = f"v_{msg.from_user.id}_{int(time.time())}"
        path_template = f"downloads/{unique_id}.%(ext)s"
        
        try:
            # Eng barqaror sozlamalar
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[m4a]/best[ext=mp4]/best',
                'outtmpl': path_template,
                'max_filesize': 50*1024*1024,
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
                'ignoreerrors': False,
                'logtostderr': False,
                'no_color': True,
                'socket_timeout': 30,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
            }
            
            await status.edit_text("📥 **Yuklanmoqda...**")
            
            loop = asyncio.get_event_loop()
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Video haqida ma'lumot olish
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
                filename = ydl.prepare_filename(info)

            # Ba'zida kengaytma o'zgarishi mumkin (.mkv -> .mp4)
            if not os.path.exists(filename):
                for f in os.listdir("downloads"):
                    if f.startswith(unique_id):
                        filename = os.path.join("downloads", f)
                        break

            if os.path.exists(filename) and os.path.getsize(filename) > 0:
                await status.edit_text("🚀 **Yuborilmoqda...**")
                await msg.answer_video(
                    FSInputFile(filename), 
                    caption=f"🎬 **{info.get('title', 'Video')}**\n🤖 @{(await bot.get_me()).username}"
                )
                await status.delete()
            else:
                raise Exception("Fayl yuklanmadi")

        except Exception as e:
            err_str = str(e)
            if "403" in err_str or "429" in err_str:
                await status.edit_text("❌ **Server bloklandi.**\nInstagram/YouTube serverni vaqtinchalik blokladi. Birozdan so'ng urinib ko'ring.")
            elif "File is larger than" in err_str:
                await status.edit_text("❌ **Hajm juda katta (Max: 50MB).**")
            else:
                await status.edit_text(f"❌ **Xato yuz berdi.**\nLinkni tekshiring yoki Shorts bo'lsa qayta yuboring.")
        finally:
            if 'filename' in locals() and os.path.exists(filename):
                try: os.remove(filename)
                except: pass
            queue.task_done()

# --- BOT INTERFACE ---
async def start_bot():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    init_db()
    queue = asyncio.Queue()
    asyncio.create_task(download_worker(queue, bot))

    @dp.message(Command("start"))
    async def cmd_start(m: types.Message):
        add_user(m.from_user)
        await m.answer(f"👋 Salom {m.from_user.full_name}!\n\nYouTube/Shorts/Instagram linkini yuboring.")

    @dp.message(F.text.regexp(r'(https?://[^\s]+)'))
    async def handle_links(m: types.Message):
        await queue.put((m.text, m))

    @dp.message(F.reply_to_message)
    async def admin_reply(m: types.Message):
        if m.from_user.id == ADMIN_ID and m.reply_to_message.forward_from:
            await bot.copy_message(m.reply_to_message.forward_from.id, m.chat.id, m.message_id)
            await m.answer("✅ Yuborildi.")

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, handle_signals=False)

# --- SINGLETON ---
if "bot_running" not in st.session_state:
    if not any(t.name == "BotThread" for t in threading.enumerate()):
        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(start_bot())
        threading.Thread(target=run_async, name="BotThread", daemon=True).start()
    st.session_state.bot_running = True

# --- ADMIN INTERFACE ---
st.title("🤖 Neon Downloader")
with sqlite3.connect(DB_FILE) as conn:
    df = pd.read_sql_query("SELECT * FROM users", conn)

st.metric("Foydalanuvchilar", len(df))
tab1, tab2 = st.tabs(["📢 Xabar", "📋 Baza"])

with tab1:
    txt = st.text_area("Xabar:")
    if st.button("Yuborish"):
        stats = {"s": 0, "f": 0}
        async def bc():
            b = Bot(token=BOT_TOKEN)
            for u in df['user_id']:
                try: await b.send_message(u, txt); stats["s"]+=1
                except: stats["f"]+=1
            await b.session.close()
        asyncio.run(bc())
        st.success(f"S: {stats['s']}, F: {stats['f']}")

with tab2: st.dataframe(df, use_container_width=True)
        
