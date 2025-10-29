import logging
import re
import asyncio
import aiosqlite
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ContentType, ChatType
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from google.oauth2 import service_account
import gspread

# === БАЗОВІ НАЛАШТУВАННЯ ===
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
GROUP_CHAT_ID = -1003250890622   # оновлений ID супер-групи
DB_PATH = "feedback_messages.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# === GOOGLE SHEETS ІНТЕГРАЦІЯ ===
SERVICE_ACCOUNT_FILE = "service_account.json"
SPREADSHEET_ID = "YOUR_SPREADSHEET_ID"
SHEET_NAME = "Feedback"

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
)
gc = gspread.authorize(credentials)

async def run_export():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM feedback_messages ORDER BY id DESC")
            rows = await cursor.fetchall()

        sh = gc.open_by_key(SPREADSHEET_ID)
        worksheet = sh.worksheet(SHEET_NAME)
        worksheet.clear()
        headers = ["ID", "User ID", "Name", "Username", "Phone", "Type", "Text", "File ID", "Timestamp", "Status"]
        worksheet.append_row(headers)

        for r in rows:
            worksheet.append_row([str(x) if x is not None else "" for x in r])

        logging.info(f"✅ Експортовано {len(rows)} рядків у Google Sheets")
    except Exception as e:
        logging.error(f"❌ Помилка експорту у Google Sheets: {e}")

# === ІНІЦІАЛІЗАЦІЯ БАЗИ ДАНИХ ===
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS feedback_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            username TEXT,
            phone TEXT,
            message_type TEXT,
            message_text TEXT,
            media_file_id TEXT,
            timestamp TEXT,
            status TEXT
        )
        """)
        await db.commit()

# === ДОПОМІЖНА ФУНКЦІЯ ===
def user_block(user):
    return f"<b>{user.full_name}</b> (@{user.username or '—'}) [<code>{user.id}</code>]"

# === СТАРТ ===
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Вітаю! Ви можете надіслати будь-яке повідомлення — текст, фото чи відео.\n"
        "Якщо потрібно, просто напишіть свій номер телефону для зв’язку."
    )

# === ПАНЕЛЬ АДМІНІСТРАТОРА ===
@dp.message(F.text == "/panel")
async def admin_panel(message: Message):
    if message.chat.type != ChatType.SUPERGROUP:
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Розсилка номерів", callback_data="broadcast_numbers")],
        [InlineKeyboardButton(text="📝 Масова розсилка тексту", callback_data="broadcast_text")],
        [InlineKeyboardButton(text="🎯 Надіслати одному", callback_data="send_one")]
    ])
    await message.answer("🛠 Панель адміністратора", reply_markup=keyboard)

# === СТАТИСТИКА ===
@dp.message(F.text == "/stats")
async def show_stats(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM feedback_messages")
        total = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM feedback_messages WHERE DATE(timestamp)=DATE('now')")
        today = (await cursor.fetchone())[0]
    await message.answer(f"📊 Усього повідомлень: {total}\n📅 За сьогодні: {today}")

# === РУЧНИЙ ЕКСПОРТ ===
@dp.message(F.text == "/export")
async def manual_export(message: Message):
    await message.answer("⏳ Виконується експорт у Google Sheets...")
    await run_export()
    await message.answer("✅ Дані оновлено у таблиці.")

# === ХЕНДЛЕР ДЛЯ РОЗСИЛОК ===
@dp.callback_query(F.data == "broadcast_numbers")
async def broadcast_numbers(callback: CallbackQuery):
    await callback.message.answer("📣 Розсилка: запит номерів...")
    async with aiosqlite.connect(DB_PATH) as db:
        users = await db.execute("SELECT DISTINCT user_id FROM feedback_messages")
        for (uid,) in await users.fetchall():
            try:
                await bot.send_message(uid, "📞 Будь ласка, напишіть номер телефону, на який ви зареєстровані у застосунку TOTIS Pharma.")
            except Exception as e:
                logging.warning(f"❌ Не вдалося надіслати користувачу {uid}: {e}")
    await callback.message.answer("✅ Повідомлення відправлено всім користувачам.")

@dp.callback_query(F.data == "broadcast_text")
async def broadcast_text(callback: CallbackQuery):
    await callback.message.answer("📝 Надішліть наступним повідомленням текст для масової розсилки.")
    dp["awaiting_broadcast_text"] = True

@dp.message(F.text, F.chat.type == ChatType.SUPERGROUP)
async def handle_broadcast_text(message: Message):
    if dp.get("awaiting_broadcast_text"):
        text = message.text
        async with aiosqlite.connect(DB_PATH) as db:
            users = await db.execute("SELECT DISTINCT user_id FROM feedback_messages")
            for (uid,) in await users.fetchall():
                try:
                    await bot.send_message(uid, text)
                except Exception as e:
                    logging.warning(f"❌ Помилка надсилання користувачу {uid}: {e}")
        dp["awaiting_broadcast_text"] = False
        await message.answer("✅ Масова розсилка виконана.")

# === ВІДПОВІДЬ НА ПОВІДОМЛЕННЯ У ГРУПІ ===
@dp.message(F.reply_to_message)
async def reply_to_user(message: Message):
    if message.chat.id != GROUP_CHAT_ID:
        return
    match = re.search(r"\[(\d+)\]", message.reply_to_message.text or "")
    if match:
        target_user_id = int(match.group(1))
        try:
            if message.text:
                await bot.send_message(target_user_id, f"💬 Від адміністратора:\n{message.text}")
            elif message.photo:
                await bot.send_photo(target_user_id, message.photo[-1].file_id, caption=message.caption or "")
            elif message.document:
                await bot.send_document(target_user_id, message.document.file_id, caption=message.caption or "")
            await message.reply("✅ Повідомлення надіслано користувачу.")
        except Exception as e:
            await message.reply(f"❌ Помилка: {e}")

# === ЛОГІКА ЛОГУВАННЯ ПОВІДОМЛЕНЬ ===
@dp.message(F.chat.type == "private")
async def save_feedback(message: Message):
    user = message.from_user
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mtype = message.content_type
    mtext = message.text or message.caption or ""
    media_id = None

    if mtype in ["photo", "document", "video", "voice", "audio"]:
        if mtype == "photo":
            media_id = message.photo[-1].file_id
        elif mtype == "document":
            media_id = message.document.file_id
        elif mtype == "video":
            media_id = message.video.file_id
        elif mtype == "voice":
            media_id = message.voice.file_id
        elif mtype == "audio":
            media_id = message.audio.file_id

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO feedback_messages (user_id, user_name, username, message_type, message_text, media_file_id, timestamp, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'new')
        """, (user.id, user.full_name, user.username, mtype, mtext, media_id, timestamp))
        await db.commit()

    # Пересилка у групу
    text = f"🧾 <b>Нове повідомлення</b>\n{user_block(user)}\nТип: {mtype}\nТекст: {mtext or '—'}"
    try:
        if mtype == "photo":
            await bot.send_photo(GROUP_CHAT_ID, message.photo[-1].file_id, caption=text, parse_mode="HTML")
        elif mtype == "document":
            await bot.send_document(GROUP_CHAT_ID, message.document.file_id, caption=text, parse_mode="HTML")
        elif mtype == "video":
            await bot.send_video(GROUP_CHAT_ID, message.video.file_id, caption=text, parse_mode="HTML")
        else:
            await bot.send_message(GROUP_CHAT_ID, text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"❌ Не вдалося переслати повідомлення в групу: {e}")

    # Автоматичний експорт
    try:
        await run_export()
    except Exception as e:
        logging.warning(f"Помилка автологування у Google Sheets: {e}")

# === ХЕНДЛЕР ДЛЯ НОМЕРІВ (текстом) ===
@dp.message(F.chat.type == "private", F.text.regexp(r"^\+?\d{7,15}$"))
async def save_phone_textually(message: Message):
    user = message.from_user
    phone = message.text.strip()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO feedback_messages (user_id, user_name, username, phone, message_type, message_text, timestamp, status)
            VALUES (?, ?, ?, ?, 'text_phone', ?, ?, 'phone_received')
            ON CONFLICT(user_id) DO UPDATE SET phone=excluded.phone, timestamp=excluded.timestamp
        """, (user.id, user.full_name, user.username, phone, message.text, timestamp))
        await db.commit()

    msg = f"📞 <b>Користувач надіслав номер телефону</b>\n{user_block(user)}\nНомер: <code>{phone}</code>"
    await bot.send_message(GROUP_CHAT_ID, msg, parse_mode="HTML")

    await message.answer("✅ Дякуємо! Ваш номер збережено для зв’язку.", parse_mode="HTML")

    try:
        await run_export()
    except Exception as e:
        logging.warning(f"Помилка автологування у Google Sheets: {e}")

# === ЗАПУСК ===
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
