import asyncio
import logging
import os
import sys
import io
import re
from datetime import datetime
from dotenv import load_dotenv

# Third-party libraries
import aiohttp
import qrcode
from supabase import create_client, Client

# Aiogram framework
from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile, FSInputFile, URLInputFile
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ==========================================================
# ⚙️ CONFIGURATION & CREDENTIALS
# ==========================================================

load_dotenv()

# Token
BOT_TOKEN = os.getenv("BOT_TOKEN", "8355482594:AAEu2Qf2hJZ8k2VuWIZXNP0H3Odrk5XvUAc")

# --- External API Keys ---
REMOVE_BG_API_KEY = "AMZSfD6x6XoyDQAr8TyBhfWr"
REMOVE_BG_URL = "https://api.remove.bg/v1.0/removebg"

# YouTube API (RapidAPI)
RAPID_API_KEY = "40ecafdbb2msh659a694212a0df9p1754f2jsn551438949f8f"
# We use 'yt-api' service from RapidAPI which is compatible with this logic
RAPID_API_HOST = "yt-api.p.rapidapi.com"
RAPID_API_URL = "https://yt-api.p.rapidapi.com/dl"

# --- Supabase Configuration ---
SUPABASE_URL = "https://hybsbsjpeoxxjilizoiy.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imh5YnNic2pwZW94eGppbGl6b2l5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzA0NDAxMzcsImV4cCI6MjA4NjAxNjEzN30.UxyJrvDSgZX18idLxuOR4YP7XJxV5JcBqoR3xZ0aAW4"

# ==========================================================
# 🔧 INITIALIZATION
# ==========================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("✅ Supabase connected successfully.")
except Exception as e:
    logger.error(f"❌ Failed to connect to Supabase: {e}")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# ==========================================================
# 🧠 STATES (FSM)
# ==========================================================

class BotStates(StatesGroup):
    waiting_for_bg_photo = State()
    waiting_for_yt_url = State()
    waiting_for_qr_text = State()

# ==========================================================
# 🎹 KEYBOARDS (Updated)
# ==========================================================

def main_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎨 Remove Background")],
            [KeyboardButton(text="📜 History")],
            # Removed Text to Voice, Added Download Video
            [KeyboardButton(text="📥 Download Video YouTube"), KeyboardButton(text="🧾 Make QR Code")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Select a tool..."
    )

def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Cancel")]],
        resize_keyboard=True
    )

# ==========================================================
# 🗄️ DATABASE FUNCTIONS
# ==========================================================

async def db_save_history(user_id: int, username: str, tool_type: str, file_id: str = None):
    try:
        data = {
            "user_id": user_id,
            "username": username or "Unknown",
            "tool_type": tool_type,
            "file_id": file_id
        }
        await asyncio.to_thread(lambda: supabase.table("history").insert(data).execute())
        logger.info(f"💾 Saved history: {tool_type} (File: {file_id})")
    except Exception as e:
        logger.error(f"⚠️ DB Insert Error: {e}")

async def db_get_history(user_id: int):
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table("history")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(5) 
            .execute()
        )
        return response.data
    except Exception as e:
        logger.error(f"⚠️ DB Fetch Error: {e}")
        return []

# ==========================================================
# 🛠️ UTILS
# ==========================================================

def extract_video_id(url):
    """Extracts YouTube Video ID from various URL formats."""
    regex = r'(?:v=|\/)([0-9A-Za-z_-]{11}).*'
    match = re.search(regex, url)
    return match.group(1) if match else None

# ==========================================================
# 🎮 GENERAL HANDLERS
# ==========================================================

@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    
    welcome_pic = "https://img.freepik.com/free-vector/cute-robot-holding-clipboard-cartoon-vector-icon-illustration-science-technology-icon-isolated_138676-5162.jpg"
    
    await message.answer_photo(
        photo=welcome_pic,
        caption=(
            f"👋 <b>Hello, {message.from_user.first_name}!</b>\n\n"
            "🤖 I am your <b>AI Multi-Tool Assistant</b>.\n"
            "👇 <b>Select a tool to begin:</b>"
        ),
        reply_markup=main_menu_kb()
    )

@router.message(F.text == "❌ Cancel")
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🚫 Operation cancelled.", reply_markup=main_menu_kb())

# ==========================================================
# 🎨 TOOL: REMOVE BACKGROUND
# ==========================================================

@router.message(F.text == "🎨 Remove Background")
async def nav_remove_bg(message: types.Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_bg_photo)
    await message.answer("📤 <b>Send me a photo</b> to remove the background.", reply_markup=cancel_kb())

@router.message(BotStates.waiting_for_bg_photo, F.photo)
async def process_remove_bg(message: types.Message, state: FSMContext):
    status_msg = await message.answer("⏳ <b>Processing image...</b> please wait.")
    
    try:
        photo = message.photo[-1]
        file_io = io.BytesIO()
        file_info = await bot.get_file(photo.file_id)
        await bot.download_file(file_info.file_path, file_io)
        file_io.seek(0)

        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field('image_file', file_io, filename='input.jpg')
            form.add_field('size', 'auto')
            headers = {'X-Api-Key': REMOVE_BG_API_KEY}
            
            async with session.post(REMOVE_BG_URL, data=form, headers=headers) as resp:
                if resp.status == 200:
                    result_data = await resp.read()
                    output_file = BufferedInputFile(result_data, filename="no_bg.png")
                    sent_msg = await message.answer_document(
                        output_file, 
                        caption="✅ <b>Background Removed!</b>",
                        reply_markup=main_menu_kb()
                    )
                    await db_save_history(message.from_user.id, message.from_user.username, "Remove Background", sent_msg.document.file_id)
                    await state.clear()
                else:
                    await message.answer("⚠️ API Error. Try another photo.", reply_markup=main_menu_kb())
                    await state.clear()
    except Exception as e:
        logger.error(f"Remove BG Error: {e}")
        await message.answer("⚠️ Error occurred.", reply_markup=main_menu_kb())
        await state.clear()
    finally:
        try: await bot.delete_message(message.chat.id, status_msg.message_id)
        except: pass

# ==========================================================
# 📥 TOOL: DOWNLOAD YOUTUBE VIDEO
# ==========================================================

@router.message(F.text == "📥 Download Video YouTube")
async def nav_yt(message: types.Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_yt_url)
    await message.answer("🔗 <b>Send me a YouTube Link</b>", reply_markup=cancel_kb())

@router.message(BotStates.waiting_for_yt_url, F.text)
async def process_yt(message: types.Message, state: FSMContext):
    if message.text == "❌ Cancel":
        await cmd_cancel(message, state)
        return

    video_id = extract_video_id(message.text)
    if not video_id:
        await message.answer("⚠️ <b>Invalid URL.</b> Please send a valid YouTube link.")
        return

    status_msg = await message.answer("⏳ <b>Searching & Downloading...</b>")

    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "x-rapidapi-key": RAPID_API_KEY,
                "x-rapidapi-host": RAPID_API_HOST
            }
            params = {"id": video_id}

            async with session.get(RAPID_API_URL, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # Logic to find the best mp4 format with audio
                    # This depends on the specific RapidAPI response structure of 'yt-api'
                    # Usually it returns a 'formats' list or direct links.
                    # Fallback simple logic: look for 'link' or iterate formats.
                    
                    download_url = None
                    title = data.get('title', 'YouTube Video')
                    
                    # Try to find a direct link in common keys
                    # Some APIs return dictionary with 'link', others list of 'formats'
                    if 'link' in data:
                        download_url = data['link'] # Some endpoints do this
                    elif 'formats' in data:
                        # Find first mp4 with audio
                        for fmt in data['formats']:
                             # Basic filter logic
                            download_url = fmt.get('url')
                            break # Just take the first one for this example
                    # For yt-api specifically, sometimes it returns thumbnail and links directly
                    # Let's assume standard response structure or dict access
                    
                    # If using 'yt-api.p.rapidapi.com/dl':
                    # It usually returns { "status": "OK", "id": "...", "title": "...", "thumbnail": "...", "formats": [...] }
                    
                    if not download_url and 'formats' in data:
                         download_url = data['formats'][-1]['url'] # Last one is often highest quality in some APIs

                    if download_url:
                        # Send Video
                        sent_msg = await message.answer_video(
                            video=URLInputFile(download_url, filename=f"{title}.mp4"),
                            caption=f"🎥 <b>{title}</b>",
                            reply_markup=main_menu_kb()
                        )
                        
                        # Save to DB (Video file_id)
                        await db_save_history(
                            message.from_user.id, 
                            message.from_user.username, 
                            "YouTube Video", 
                            sent_msg.video.file_id
                        )
                        await state.clear()
                    else:
                        await message.answer("⚠️ Could not retrieve download link.", reply_markup=main_menu_kb())
                        await state.clear()

                else:
                    await message.answer(f"⚠️ API Error: {resp.status}", reply_markup=main_menu_kb())
                    await state.clear()

    except Exception as e:
        logger.error(f"YouTube Error: {e}")
        # If file is too large for Telegram Bot API (50MB limit for URL upload), this might fail
        await message.answer("⚠️ Error: Video might be too large or restricted.", reply_markup=main_menu_kb())
        await state.clear()
    finally:
        try: await bot.delete_message(message.chat.id, status_msg.message_id)
        except: pass

# ==========================================================
# 🧾 TOOL: QR CODE GENERATOR
# ==========================================================

@router.message(F.text == "🧾 Make QR Code")
async def nav_qr(message: types.Message, state: FSMContext):
    await state.set_state(BotStates.waiting_for_qr_text)
    await message.answer("🔗 <b>Send me a Link or Text</b> for the QR code.", reply_markup=cancel_kb())

@router.message(BotStates.waiting_for_qr_text, F.text)
async def process_qr(message: types.Message, state: FSMContext):
    if message.text == "❌ Cancel":
        await cmd_cancel(message, state)
        return

    status_msg = await message.answer("⏳ <b>Generating QR...</b>")
    try:
        data = message.text
        qr_img = qrcode.make(data)
        bio = io.BytesIO()
        qr_img.save(bio)
        bio.seek(0)
        output_file = BufferedInputFile(bio.read(), filename="qrcode.png")
        
        sent_msg = await message.answer_photo(
            output_file,
            caption=f"🧾 <b>QR Generated!</b>",
            reply_markup=main_menu_kb()
        )
        
        photo_id = sent_msg.photo[-1].file_id
        await db_save_history(message.from_user.id, message.from_user.username, "QR Code Generator", file_id=photo_id)
        await state.clear()

    except Exception as e:
        logger.error(f"QR Error: {e}")
        await message.answer("⚠️ Failed.", reply_markup=main_menu_kb())
    finally:
        try: await bot.delete_message(message.chat.id, status_msg.message_id)
        except: pass

# ==========================================================
# 📜 TOOL: HISTORY
# ==========================================================

@router.message(F.text == "📜 History")
async def nav_history(message: types.Message):
    status_msg = await message.answer("⏳ <b>Fetching history...</b>")
    
    try:
        records = await db_get_history(message.from_user.id)
        
        if not records:
            await message.answer("📭 <b>No history found.</b>", reply_markup=main_menu_kb())
        else:
            await message.answer("📜 <b>Here are your recent files:</b>")
            
            for item in records:
                tool = item.get("tool_type", "Tool")
                file_id = item.get("file_id")
                created_at = item.get("created_at", "")[:16].replace("T", " ")
                
                caption_text = f"📅 {created_at}\n🔧 {tool}"

                if file_id:
                    try:
                        if "Remove Background" in tool:
                            await message.answer_document(file_id, caption=caption_text)
                        elif "QR" in tool:
                            await message.answer_photo(file_id, caption=caption_text)
                        elif "YouTube" in tool:
                            await message.answer_video(file_id, caption=caption_text)
                        else:
                            await message.answer(f"🔹 {caption_text}")
                    except Exception as e:
                        await message.answer(f"⚠️ File unavailable: {caption_text}")
                else:
                    await message.answer(f"🔹 {caption_text}")
                
                await asyncio.sleep(0.3)

    except Exception as e:
        logger.error(f"History Error: {e}")
        await message.answer("⚠️ Could not load history.", reply_markup=main_menu_kb())
    finally:
        try: await bot.delete_message(message.chat.id, status_msg.message_id)
        except: pass

# ==========================================================
# 🚀 ENTRY POINT
# ==========================================================

async def main():
    logger.info("🚀 Bot is starting...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Bot stopped.")