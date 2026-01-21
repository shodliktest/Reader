import logging
import os
import asyncio
import sqlite3
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from yt_dlp import YoutubeDL

# --- SOZLAMALAR ---
TOKEN = "BU_YERGA_TOKEN_YOZING"
ADMIN_ID = 1416457518  # O'zingizning ID raqamingizni yozing

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- BAZA BILAN ISHLASH (Admin uchun) ---
def db_start():
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY)""")
    conn.commit()
    conn.close()

def add_user(user_id):
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users VALUES (?)", (user_id,))
        conn.commit()
    except:
        pass # Agar user bor bo'lsa, xato bermaydi
    finally:
        conn.close()

def get_users_count():
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    count = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return count

def get_all_users():
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    users = cur.execute("SELECT id FROM users").fetchall()
    conn.close()
    return users

# --- VIDEO YUKLASH FUNKSIYASI ---
def download_video(url):
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': 'downloads/%(title)s_%(id)s.%(ext)s',
        'max_filesize': 49 * 1024 * 1024, # 49MB limit (Telegram uchun)
        'noplaylist': True,
        # Cookie muammosi bo'lsa, shu yerga cookies.txt qo'shiladi
    }
    
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info), info.get('title', 'Video')
    except Exception as e:
        return None, str(e)

# --- REGEX (Linkni topish uchun) ---
# Instagram, TikTok, YouTube, Facebook linklarini aniqlaydi
LINK_PATTERN = r'(https?://(?:www\.)?(?:instagram\.com|tiktok\.com|facebook\.com|fb\.watch|youtube\.com|youtu\.be)/[^\s]+)'

# --- HANDLERLAR ---

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    add_user(message.from_user.id)
    await message.answer(
        f"Salom {message.from_user.full_name}! 👋\n\n"
        "Men universal yuklovchi botman.\n"
        "Meni guruhga qo'shsangiz, tashlangan linklarni avtomatik yuklab beraman.\n\n"
        "Qollab quvvatlanadigan tarmoqlar: \n"
        "📸 Instagram\n🎵 TikTok\n▶️ YouTube (Shorts/Video)\nf Facebook"
    )

# --- ADMIN PANEL ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        count = get_users_count()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Reklama yuborish", callback_data="broadcast")]
        ])
        await message.answer(f"👨‍💻 **Admin Panel**\n\n📊 Foydalanuvchilar: {count} ta", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "broadcast")
async def broadcast_ask(callback: types.CallbackQuery):
    await callback.message.answer("Reklama xabarini yuboring (Text, Rasm yoki Video):")
    # Bu yerda state mashina (FSM) ishlatish kerak, lekin soddalik uchun qoldiramiz.
    # Hozircha shunchaki statistika ko'rish yetarli.

# --- UNIVERSAL LINK HANDLER (Guruh va Shaxsiy) ---
@dp.message()
async def check_messages(message: types.Message):
    # 1. Userni bazaga qo'shamiz (har ehtimolga qarshi)
    add_user(message.from_user.id)
    
    # 2. Xabar ichidan link qidiramiz
    text = message.text or message.caption # Rasm tagiga yozilgan bo'lsa ham oladi
    if not text:
        return

    match = re.search(LINK_PATTERN, text)
    
    if match:
        url = match.group(0) # Topilgan birinchi link
        
        # Guruhda bo'lsa "Kuting..." deb yozib o'tirmasin, shovqin bo'ladi.
        # Shaxsiyda bo'lsa xabar beramiz.
        status_msg = None
        if message.chat.type == 'private':
            status_msg = await message.answer("⏳ Video yuklanmoqda...")
        else:
            # Guruhda reaksiyani bildirish (ixtiyoriy)
            await bot.send_chat_action(message.chat.id, "upload_video")

        # Yuklash jarayoni
        loop = asyncio.get_event_loop()
        file_path, title = await loop.run_in_executor(None, download_video, url)
        
        if file_path and os.path.exists(file_path):
            try:
                video_file = types.FSInputFile(file_path)
                caption = f"🎬 {title}\n👤 {message.from_user.mention_html()}"
                
                await bot.send_video(
                    chat_id=message.chat.id, 
                    video=video_file, 
                    caption=caption, 
                    reply_to_message_id=message.message_id, # Link egasiga reply qiladi
                    parse_mode="HTML"
                )
            except Exception as e:
                if status_msg: await status_msg.edit_text(f"Xatolik: {e}")
            finally:
                if status_msg: await status_msg.delete()
                try: os.remove(file_path) # Faylni o'chiramiz
                except: pass
        else:
            if status_msg:
                await status_msg.edit_text("❌ Videoni yuklab bo'lmadi yoki manzil noto'g'ri.")

async def main():
    db_start()
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
