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

# --- 2. DATABASE (SQLite) ---
DB_FILE = "users.db"

def init_db():
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT, join_date TEXT)''')
        conn.commit()

def add_user_to_db(user: types.User):
    with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
        c = conn.cursor()
        tz = pytz.timezone('Asia/Tashkent')
        now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?)", 
                  (user.id, user.full_name, user.username, now))
        conn.commit()

def get_all_users_data():
    try:
        with sqlite3.connect(DB_FILE, check_same_thread=False) as conn:
            return pd.read_sql_query("SELECT * FROM users", conn)
    except Exception:
        return pd.DataFrame()

# --- 3. DOWNLOADER LOGIC ---
async def download_worker(queue, bot: Bot):
    """Navbatdagi videolarni yuklovchi worker"""
    while True:
        url, message = await queue.get()
        status_msg = None
        filename = None
        try:
            status_msg = await message.reply("⏳ **So'rovingiz qabul qilindi...**\nVideo yuklash boshlanmoqda, iltimos kuting.")
            
            temp_dir = "downloads"
            os.makedirs(temp_dir, exist_ok=True)
            # Fayl nomida ID ishlatamiz (conflictlarni oldini olish uchun)
            output_template = os.path.join(temp_dir, f"{message.from_user.id}_%(id)s.%(ext)s")

            ydl_opts = {
                'format': 'best[ext=mp4]/best',
                'outtmpl': output_template,
                'max_filesize': 50 * 1024 * 1024,
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True
            }

            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=True))
            
            # Haqiqiy fayl nomini aniqlash
            filename = yt_dlp.YoutubeDL(ydl_opts).prepare_filename(info)
            # Ba'zida kengaytma o'zgarishi mumkin
            if not os.path.exists(filename):
                base = os.path.splitext(filename)[0]
                for f in os.listdir(temp_dir):
                    if f.startswith(os.path.basename(base)):
                        filename = os.path.join(temp_dir, f)
                        break

            if os.path.exists(filename):
                file_size = os.path.getsize(filename)
                if file_size > 50 * 1024 * 1024:
                    await status_msg.edit_text("❌ **Hajm juda katta (Max: 50MB).**\nTelegram botlar 50MB dan ortiq faylni yubora olmaydi.")
                else:
                    await status_msg.edit_text("🚀 **Telegramga yuklanmoqda...**")
                    video_input = FSInputFile(filename)
                    caption = (f"🎬 **{info.get('title', 'Video')}**\n\n"
                               f"👤 **Foydalanuvchi:** {message.from_user.mention_html()}\n"
                               f"🤖 **Bot:** @{(await bot.get_me()).username}")
                    
                    await message.answer_video(video_input, caption=caption, parse_mode="HTML")
                    await status_msg.delete()
            else:
                await status_msg.edit_text("❌ Xatolik: Video yuklanmadi.")

        except Exception as e:
            if status_msg:
                await status_msg.edit_text(f"❌ **Xatolik:**\nLink noto'g'ri yoki video juda katta.\n({str(e)[:50]}...)")
        finally:
            if filename and os.path.exists(filename):
                try: os.remove(filename)
                except: pass
            queue.task_done()

# --- 4. BOT HANDLERS ---
async def start_bot_process():
    # Killer Webhook & Singleton protection
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    init_db()
    
    queue = asyncio.Queue()
    asyncio.create_task(download_worker(queue, bot))

    @dp.message(Command("start"))
    async def cmd_start(message: types.Message):
        add_user_to_db(message.from_user)
        await message.answer(f"👋 **Salom, {message.from_user.full_name}!**\n\n"
                             "🎥 YouTube havolasini yuboring, men uni yuklab beraman.")

    @dp.message(F.reply_to_message)
    async def admin_reply(message: types.Message):
        if message.from_user.id == ADMIN_ID and message.reply_to_message.forward_from:
            try:
                user_id = message.reply_to_message.forward_from.id
                await bot.copy_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=message.message_id)
                await message.set_reaction(reactions=[types.ReactionTypeEmoji(emoji="👍")])
            except:
                await message.reply("⚠️ Xabar yuborilmadi (User bloklagan yoki profil yopiq).")

    @dp.message()
    async def handle_all(message: types.Message):
        if not message.text: return
        
        url_keywords = ["youtube.com", "youtu.be", "shorts"]
        if any(key in message.text for key in url_keywords):
            await queue.put((message.text, message))
        elif message.from_user.id != ADMIN_ID:
            await message.forward(chat_id=ADMIN_ID)
            await message.answer("📨 **Xabaringiz adminga yetkazildi.**")

    # Killer: Tozalash va Polling boshlash
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

# --- 5. SINGLETON THREAD GUARD ---
def bot_control():
    if "bot_active" not in st.session_state:
        # Eski threadni qidirish
        existing_thread = False
        for thread in threading.enumerate():
            if thread.name == "BotThread":
                existing_thread = True
                break
        
        if not existing_thread:
            def run_loop():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(start_bot_process())
            
            t = threading.Thread(target=run_loop, name="BotThread", daemon=True)
            t.start()
            st.session_state.bot_active = True

bot_control()

# --- 6. ADMIN PANEL (Streamlit) ---
st.set_page_config(page_title="Admin Panel", page_icon="⚙️", layout="wide")

st.title("🚀 YouTube Downloader Admin Panel")
st.divider()

# Statistika
df = get_all_users_data()
c1, c2, c3 = st.columns(3)
with c1: st.metric("👥 Foydalanuvchilar", len(df))
with c2: 
    bot_alive = any(t.name == "BotThread" for t in threading.enumerate())
    st.metric("🤖 Bot Statusi", "🟢 Faol" if bot_alive else "🔴 Kutishda")
with c3: st.metric("📅 Bugun", datetime.now().strftime("%d-%m-%Y"))

tab1, tab2 = st.tabs(["📢 Xabar Tarqatish", "📋 Ma'lumotlar Bazasi"])

with tab1:
    st.subheader("Barcha foydalanuvchilarga xabar yuborish")
    msg_text = st.text_area("Xabar matni:", placeholder="Salom...")
    
    if st.button("Yuborishni boshlash"):
        if df.empty:
            st.error("Bazada foydalanuvchilar yo'q!")
        elif not msg_text:
            st.warning("Xabar matni bo'sh!")
        else:
            ids = df['user_id'].tolist()
            prog = st.progress(0, text="Tayyorlanmoqda...")
            s_count, f_count = 0, 0
            
            # Broadcast uchun alohida vaqtinchalik loop
            async def broadcast():
                nonlocal s_count, f_count
                temp_bot = Bot(token=BOT_TOKEN)
                for i, uid in enumerate(ids):
                    try:
                        await temp_bot.send_message(uid, msg_text, parse_mode="HTML")
                        s_count += 1
                    except:
                        f_count += 1
                    prog.progress((i + 1) / len(ids), text=f"Yuborilmoqda: {i+1}/{len(ids)}")
                await temp_bot.session.close()

            asyncio.run(broadcast())
            st.success(f"✅ Tugadi! Yetkazildi: {s_count}, Bloklangan: {f_count}")

with tab2:
    st.subheader("Foydalanuvchilar jadvali")
    if not df.empty:
        st.dataframe(df, use_container_width=True)
        st.download_button("Excel yuklab olish", df.to_csv(index=False), "users.csv", "text/csv")
    else:
        st.info("Baza bo'sh.")

st.sidebar.markdown(f"**Admin ID:** `{ADMIN_ID}`")
if st.sidebar.button("🔄 Sahifani yangilash"):
    st.rerun()
