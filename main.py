import streamlit as st
import asyncio
import os
import threading
import sqlite3
import pytz
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile
import yt_dlp

# --- 1. SOZLAMALAR (SECRETS) ---
try:
    BOT_TOKEN = st.secrets["BOT_TOKEN"]
    ADMIN_ID = int(st.secrets["ADMIN_ID"])
except:
    st.error("❌ Xatolik: Secrets topilmadi! .streamlit/secrets.toml faylini tekshiring.")
    st.stop()

# --- 2. BAZA (Foydalanuvchilar) ---
DB_FILE = "users.db"

def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT, join_date TEXT)''')
    conn.commit()
    conn.close()

def add_user_to_db(user: types.User):
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    tz = pytz.timezone('Asia/Tashkent')
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    try:
        c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?)", 
                  (user.id, user.full_name, user.username, now))
        conn.commit()
    except: pass
    finally: conn.close()

def get_all_users_data():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    import pandas as pd
    try:
        df = pd.read_sql_query("SELECT * FROM users", conn)
    except:
        df = pd.DataFrame()
    conn.close()
    return df

# --- 3. VIDEO YUKLASH TIZIMI (Navbat bilan) ---
async def download_worker(queue, bot):
    """
    Bu funksiya orqa fonda tinimsiz ishlab turadi va 
    navbatdagi videolarni yuklaydi.
    """
    while True:
        url, message = await queue.get()
        try:
            # 1. Foydalanuvchiga xabar: Navbatga olindi
            status_msg = await message.reply("⏳ **So'rovingiz qabul qilindi...**\nVideo yuklash boshlanmoqda, iltimos kuting.")
            
            temp_dir = "downloads"
            os.makedirs(temp_dir, exist_ok=True)
            output_template = f"{temp_dir}/%(id)s.%(ext)s"

            ydl_opts = {
                'format': 'best[ext=mp4]/best', # MP4 formatga harakat qiladi
                'outtmpl': output_template,
                'max_filesize': 50 * 1024 * 1024, # 50MB limit
                'noplaylist': True,
                'quiet': True
            }

            # 2. Videoni serverga yuklab olish
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=True))
            
            filename = ydl_opts['outtmpl'] % info
            # Agar format o'zgargan bo'lsa (mkv), faylni topish
            if not os.path.exists(filename):
                base_name = filename.rsplit('.', 1)[0]
                for f in os.listdir(temp_dir):
                    if f.startswith(os.path.basename(base_name)):
                        filename = os.path.join(temp_dir, f)
                        break
            
            # 3. Hajmni tekshirish va Telegramga yuborish
            if os.path.exists(filename):
                file_size = os.path.getsize(filename)
                if file_size > 50 * 1024 * 1024:
                    await status_msg.edit_text("❌ **Kechirasiz, video hajmi 50MB dan katta.**\nTelegram limiti tufayli yubora olmayman.")
                else:
                    await status_msg.edit_text("🚀 **Telegramga yuklanmoqda...**")
                    video_input = FSInputFile(filename)
                    
                    # Chiroyli izoh (Caption)
                    caption = (f"🎬 **{info.get('title', 'Video')}**\n\n"
                               f"👤 **Buyurtmachi:** {message.from_user.mention_html()}\n"
                               f"🤖 **Bot:** @{(await bot.get_me()).username}")
                    
                    await message.answer_video(video_input, caption=caption, parse_mode="HTML")
                    await status_msg.delete() # Eski "kuting" xabarini o'chiramiz
                
                # Serverdan o'chirish
                os.remove(filename)
            else:
                await status_msg.edit_text("❌ Video fayli topilmadi.")

        except Exception as e:
            # Xatolik bo'lsa
            await message.reply(f"❌ **Xatolik yuz berdi:**\nVideo topilmadi yoki havola noto'g'ri.\n({e})")
        finally:
            queue.task_done()

# --- 4. BOT MANTIQI ---
async def start_bot_process():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    init_db()
    
    # Navbat (Queue) yaratish
    queue = asyncio.Queue()
    # Workerni ishga tushirish
    asyncio.create_task(download_worker(queue, bot))

    # /start komandasi
    @dp.message(Command("start"))
    async def cmd_start(message: types.Message):
        add_user_to_db(message.from_user)
        await message.answer(f"👋 **Salom, {message.from_user.full_name}!**\n\n"
                             "🎥 Menga YouTube video havolasini yuboring.\n"
                             "📝 Agar admin bilan bog'lanmoqchi bo'lsangiz, shunchaki xabar yozing.")

    # Admin javobi (Reply)
    @dp.message(F.reply_to_message)
    async def admin_reply(message: types.Message):
        # Faqat admin reply qilsa
        if message.from_user.id == ADMIN_ID:
            try:
                # Agar user forward qilingan bo'lsa
                if message.reply_to_message.forward_from:
                    user_id = message.reply_to_message.forward_from.id
                    await bot.copy_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=message.message_id)
                    await message.react([types.ReactionTypeEmoji(emoji="👍")]) # Admin xabariga like
                else:
                    await message.reply("⚠️ User profili yopiq, javob yuborib bo'lmadi.")
            except Exception as e:
                await message.reply(f"Xatolik: {e}")

    # Barcha xabarlar
    @dp.message()
    async def handle_all(message: types.Message):
        text = message.text or ""
        # Link tekshirish
        if "youtube.com" in text or "youtu.be" in text:
            await queue.put((text, message)) # Navbatga qo'shish
        # Agar admin bo'lmasa, xabarni adminga forward qilish
        elif message.from_user.id != ADMIN_ID:
            await message.forward(chat_id=ADMIN_ID)
            await message.answer("📨 **Xabaringiz adminga yetkazildi.**")

    # KILLER WEBHOOK: Eski "osilgan" xabarlarni tozalash
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Botni ishga tushirish
    await dp.start_polling(bot)

# --- 5. GLOBAL SINGLETON (Bot faqat 1 marta ishga tushadi) ---
def run_bot_in_background():
    # Hozirgi barcha threadlarni tekshiramiz
    for thread in threading.enumerate():
        if thread.name == "BotThread":
            return # Agar bot ishlayotgan bo'lsa, qayta tushirmaymiz

    # Yangi thread ochamiz
    def loop_wrapper():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start_bot_process())

    t = threading.Thread(target=loop_wrapper, name="BotThread", daemon=True)
    t.start()

# Botni fon rejimida yoqish
run_bot_in_background()

# --- 6. ADMIN PANEL (Streamlit) ---
st.set_page_config(page_title="YouTube Bot Admin", page_icon="🤖", layout="wide")

st.title("🤖 Bot Boshqaruv Markazi")
st.markdown("---")

# Statistika qismi
df = get_all_users_data()
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("👥 Jami Foydalanuvchilar", len(df))
with col2:
    status = "🟢 Faol" if any(t.name == "BotThread" for t in threading.enumerate()) else "🔴 To'xtagan"
    st.metric("⚙️ Bot Statusi", status)
with col3:
    st.metric("🕒 Server Vaqti", datetime.now().strftime("%H:%M"))

# Tablar
tab1, tab2 = st.tabs(["📢 Reklama Yuborish", "📋 Foydalanuvchilar Ro'yxati"])

# 1-TAB: Xabar yuborish (Broadcast)
# 1-TAB: Xabar yuborish (Broadcast)
with tab1:
    st.subheader("Hammaga xabar yuborish")
    broadcast_text = st.text_area("Xabar matnini kiriting:", height=100)
    
    if st.button("🚀 Xabarni Yuborish"):
        if df.empty:
            st.warning("Foydalanuvchilar yo'q!")
        elif not broadcast_text:
            st.warning("Xabar matni bo'sh!")
        else:
            # Progress bar va foydalanuvchilar ro'yxati
            user_ids = df['user_id'].tolist()
            progress_bar = st.progress(0, text="Yuborish boshlandi...")
            
            # Broadcast funksiyasini ichida hisoblaymiz va return qilamiz
            async def send_broadcast():
                s_count = 0 # success
                f_count = 0 # fail
                temp_bot = Bot(token=BOT_TOKEN)
                
                for i, user_id in enumerate(user_ids):
                    try:
                        await temp_bot.send_message(chat_id=user_id, text=broadcast_text)
                        s_count += 1
                    except:
                        f_count += 1
                    
                    # Progress barni yangilash
                    percent = (i + 1) / len(user_ids)
                    progress_bar.progress(percent, text=f"Yuborilmoqda... {i+1}/{len(user_ids)}")
                
                await temp_bot.session.close()
                return s_count, f_count

            # Funksiyani ishga tushirib, natijani olamiz
            success_count, fail_count = asyncio.run(send_broadcast())
            
            progress_bar.progress(1.0, text="Yakunlandi!")
            st.success(f"✅ Natija:\n- Yuborildi: {success_count} ta\n- Yetib bormadi (blok): {fail_count} ta")
    
# 2-TAB: Jadval
with tab2:
    st.subheader("Barcha foydalanuvchilar")
    if not df.empty:
        st.dataframe(df, use_container_width=True)
    else:
        st.info("Hozircha foydalanuvchilar yo'q.")

