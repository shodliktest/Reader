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
except:
    st.error("❌ Secrets sozlanmagan!")
    st.stop()

DB_FILE = "users.db"

# --- 2. BAZA FUNKSIYALARI ---
def init_db():
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT, join_date TEXT)")

def add_user(u: types.User):
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        now = datetime.now(pytz.timezone('Asia/Tashkent')).strftime("%Y-%m-%d %H:%M")
        conn.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?)", (u.id, u.full_name, u.username, now))

# --- 3. MULTI-PLATFORM DOWNLOADER (YouTube, Shorts, Instagram) ---
async def download_worker(queue, bot: Bot):
    while True:
        url, msg = await queue.get()
        status = await msg.reply("⏳ **So'rov tahlil qilinmoqda...**")
        
        # Fayl nomi va yo'li
        os.makedirs("downloads", exist_ok=True)
        unique_id = f"{msg.from_user.id}_{int(time.time())}"
        path_template = f"downloads/{unique_id}.%(ext)s"
        
        try:
            # yt-dlp sozlamalari (Instagram va Shorts uchun optimallashtirilgan)
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': path_template,
                'max_filesize': 50*1024*1024,
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'add_header': [
                    'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                ]
            }
            
            await status.edit_text("📥 **Video yuklanmoqda...**")
            
            loop = asyncio.get_event_loop()
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
                filename = ydl.prepare_filename(info)

            # Ba'zida kengaytma o'zgarishi mumkin, tekshiramiz
            if not os.path.exists(filename):
                for f in os.listdir("downloads"):
                    if f.startswith(unique_id):
                        filename = os.path.join("downloads", f)
                        break

            if os.path.exists(filename) and os.path.getsize(filename) > 0:
                await status.edit_text("🚀 **Telegramga yuborilmoqda...**")
                await msg.answer_video(
                    FSInputFile(filename), 
                    caption=f"🎬 **{info.get('title', 'Video')}**\n\n🌐 **Manba:** {info.get('extractor_key', 'Internet')}\n🤖 @{(await bot.get_me()).username}"
                )
                await status.delete()
            else:
                raise Exception("Fayl bo'sh yoki yuklanmadi.")

        except Exception as e:
            error_msg = str(e)
            if "File is larger than" in error_msg:
                await status.edit_text("❌ **Hajm juda katta!**\nTelegram 50MB gacha fayllarni qabul qiladi.")
            else:
                await status.edit_text(f"❌ **Xatolik:**\nUshbu havola qo'llab-quvvatlanmaydi yoki serverda cheklov bor.")
        finally:
            # Tozalash
            try:
                if filename and os.path.exists(filename): os.remove(filename)
            except: pass
            queue.task_done()

# --- 4. HANDLERS ---
async def start_bot():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    init_db()
    queue = asyncio.Queue()
    asyncio.create_task(download_worker(queue, bot))

    @dp.message(Command("start"))
    async def cmd_start(m: types.Message):
        add_user(m.from_user)
        await m.answer(f"👋 **Salom {m.from_user.full_name}!**\n\nYouTube, Shorts yoki Instagram linkini yuboring.")

    # Linklarni tutish (YouTube va Instagram uchun)
    @dp.message(F.text.regexp(r'(https?://[^\s]+)'))
    async def handle_links(m: types.Message):
        url = m.text
        if any(x in url for x in ["youtube.com", "youtu.be", "instagram.com", "shorts", "reel"]):
            await queue.put((url, m))
        elif m.from_user.id != ADMIN_ID:
            await m.forward(ADMIN_ID)
            await m.answer("📨 **Adminga yetkazildi.**")

    @dp.message(F.reply_to_message)
    async def admin_reply(m: types.Message):
        if m.from_user.id == ADMIN_ID and m.reply_to_message.forward_from:
            await bot.copy_message(m.reply_to_message.forward_from.id, m.chat.id, m.message_id)
            await m.answer("✅ Yuborildi.")

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, handle_signals=False)

# --- 5. SINGLETON RUNNER ---
if "bot_running" not in st.session_state:
    bot_exists = any(t.name == "BotThread" for t in threading.enumerate())
    if not bot_exists:
        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(start_bot())
        threading.Thread(target=run_async, name="BotThread", daemon=True).start()
    st.session_state.bot_running = True

# --- 6. ADMIN PANEL ---
st.title("🤖 Neon Downloader Admin")
try:
    with sqlite3.connect(DB_FILE) as conn:
        df = pd.read_sql_query("SELECT * FROM users", conn)
except: df = pd.DataFrame()

st.metric("Foydalanuvchilar", len(df))
tab1, tab2 = st.tabs(["📢 Broadcast", "📋 Baza"])

with tab1:
    txt = st.text_area("Xabar matni:")
    if st.button("Yuborish"):
        res = {"s": 0, "f": 0}
        async def do_bc():
            b = Bot(token=BOT_TOKEN)
            for u in df['user_id']:
                try: await b.send_message(u, txt); res["s"]+=1
                except: res["f"]+=1
            await b.session.close()
        asyncio.run(do_bc())
        st.success(f"✅ Tayyor! Yetkazildi: {res['s']}, Xato: {res['f']}")

with tab2: st.dataframe(df, use_container_width=True)
                
