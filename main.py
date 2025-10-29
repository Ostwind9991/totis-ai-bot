import asyncio
import logging
import os
import json
from datetime import datetime
import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ContentType
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup, KeyboardButton,
    BotCommand, BotCommandScopeChat,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)

from google.oauth2.service_account import Credentials
import gspread
from dotenv import load_dotenv

# ==========================
# Init
# ==========================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
PDF_URL = os.getenv("PDF_URL")
DB_PATH = "links.db"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==========================
# DB
# ==========================
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
            group_message_id INTEGER,
            timestamp TEXT,
            reply_text TEXT,
            replied_by TEXT,
            reply_timestamp TEXT,
            status TEXT
        )
        """)
        await db.commit()


async def save_feedback(user, message_type, text=None, media_id=None, group_message_id=None, status="received"):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO feedback_messages (
            user_id, user_name, username, message_type, message_text,
            media_file_id, group_message_id, timestamp, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user.id,
            user.full_name,
            user.username,
            message_type,
            text,
            media_id,
            group_message_id,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            status
        ))
        await db.commit()


# ==========================
# Helpers
# ==========================
def user_block(user) -> str:
    un = f" (@{user.username})" if user.username else " (@None)"
    return f"👤 <a href='tg://user?id={user.id}'>{user.full_name}</a>{un}\nID: <code>{user.id}</code>"


broadcast_text_state = {}  # {admin_id: True/False}
send_one_state = {}        # {admin_id: {"phase": "ask_id"|"ask_msg", "user_id": int}}


async def get_all_user_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT DISTINCT user_id FROM feedback_messages WHERE user_id IS NOT NULL") as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]


# ==========================
# Start
# ==========================
@dp.message(CommandStart())
async def start_handler(message: Message):
    welcome = (
        "👋 Вітаємо в проєкті <b>«Тестування штучного інтелекту в застосунку TOTIS»</b>!\n\n"
        f"🧾 Інструкція:\n{PDF_URL}\n\n"
        "Надішліть повідомлення/фото/відео — воно автоматично потрапить у командний чат."
    )
    await message.answer(welcome, parse_mode="HTML")


# ==========================
# User → Group (private only)
# ==========================
@dp.message(
    F.chat.type == "private",
    F.content_type.in_({
        ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO,
        ContentType.VOICE, ContentType.DOCUMENT, ContentType.ANIMATION, ContentType.AUDIO
    }),
    (F.text == None) | (~F.text.startswith("/"))
)
async def forward_to_group(message: Message):
    user = message.from_user
    header = f"<b>Нове повідомлення</b>\n{user_block(user)}"

    sent = None
    text_to_save, media_id = None, None

    if message.text:
        text_to_save = message.text
        sent = await bot.send_message(GROUP_CHAT_ID, f"{header}\n\n{message.text}", parse_mode="HTML")

    elif message.photo:
        text_to_save = message.caption or ""
        media_id = message.photo[-1].file_id
        cap = f"🖼 Фото\n{header}" + (f"\n\n{text_to_save}" if text_to_save else "")
        sent = await bot.send_photo(GROUP_CHAT_ID, media_id, caption=cap, parse_mode="HTML")

    elif message.video:
        text_to_save = message.caption or ""
        media_id = message.video.file_id
        cap = f"🎥 Відео\n{header}" + (f"\n\n{text_to_save}" if text_to_save else "")
        sent = await bot.send_video(GROUP_CHAT_ID, media_id, caption=cap, parse_mode="HTML")

    elif message.voice:
        media_id = message.voice.file_id
        cap = f"🎙 Голосове повідомлення\n{header}"
        sent = await bot.send_voice(GROUP_CHAT_ID, media_id, caption=cap, parse_mode="HTML")

    elif message.document:
        text_to_save = message.caption or ""
        media_id = message.document.file_id
        cap = f"📎 Файл: <code>{message.document.file_name}</code>\n{header}" + (f"\n\n{text_to_save}" if text_to_save else "")
        sent = await bot.send_document(GROUP_CHAT_ID, media_id, caption=cap, parse_mode="HTML")

    elif message.animation:
        text_to_save = message.caption or ""
        media_id = message.animation.file_id
        cap = f"🎞 GIF/анімація\n{header}" + (f"\n\n{text_to_save}" if text_to_save else "")
        sent = await bot.send_animation(GROUP_CHAT_ID, media_id, caption=cap, parse_mode="HTML")

    elif message.audio:
        text_to_save = message.caption or ""
        media_id = message.audio.file_id
        cap = f"🎵 Аудіо: <code>{message.audio.file_name or 'audio'}</code>\n{header}" + (f"\n\n{text_to_save}" if text_to_save else "")
        sent = await bot.send_audio(GROUP_CHAT_ID, media_id, caption=cap, parse_mode="HTML")

    if sent:
        await save_feedback(user, message.content_type, text_to_save, media_id, sent.message_id)


# ==========================
# Group → User (reply to bot message)
# ==========================
@dp.message(F.chat.id == GROUP_CHAT_ID, F.reply_to_message)
async def reply_from_group(message: Message):
    me = await bot.get_me()
    if not message.reply_to_message.from_user or message.reply_to_message.from_user.id != me.id:
        return

    body = message.reply_to_message.caption or message.reply_to_message.text or ""
    import re
    m = re.search(r"ID:\s*(\d+)", body)
    if not m:
        return await bot.send_message(GROUP_CHAT_ID, "⚠️ Не знайдено ID користувача у вихідному повідомленні.")

    user_id = int(m.group(1))
    reply_text = message.text or "(без тексту)"

    try:
        await bot.send_message(user_id, f"💬 Відповідь від support.totis:\n\n{reply_text}")
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                UPDATE feedback_messages
                SET reply_text=?, replied_by=?, reply_timestamp=?, status=?
                WHERE user_id=?
            """, (reply_text, "support.totis", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "replied", user_id))
            await db.commit()
        await bot.send_message(GROUP_CHAT_ID, f"✅ Відповідь доставлена користувачу {user_id}")
    except Exception as e:
        await bot.send_message(GROUP_CHAT_ID, f"⚠️ Не вдалося надіслати користувачу {user_id}\n{e}")


# ==========================
# /stats (group only)
# ==========================
@dp.message(Command("stats"), F.chat.id == GROUP_CHAT_ID)
async def stats_handler(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM feedback_messages") as c:
            total = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM feedback_messages WHERE date(timestamp)=date('now')"
        ) as c:
            today = (await c.fetchone())[0]

    await message.answer(
        f"📊 <b>Статистика</b>\nЗа сьогодні: <b>{today}</b>\nВсього: <b>{total}</b>",
        parse_mode="HTML"
    )


# ==========================
# /export (group only)
# ==========================
async def run_export():
    creds_json = os.getenv("GOOGLE_KEY_JSON")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    sh = gc.open_by_key(sheet_id)
    ws = sh.sheet1

    ws.clear()
    ws.append_row([
        "id","user_id","user_name","username","phone","message_type",
        "message_text","media_file_id","group_message_id",
        "timestamp","reply_text","replied_by","reply_timestamp","status"
    ])

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM feedback_messages ORDER BY id") as cur:
            rows = await cur.fetchall()
            if rows:
                ws.append_rows([[str(x) if x is not None else "" for x in row] for row in rows])


@dp.message(Command("export"), F.chat.id == GROUP_CHAT_ID)
async def export_to_sheets(message: Message):
    await message.answer("📤 Експорт у Google Sheets…")
    try:
        await run_export()
        await message.answer("✅ Експорт завершено.")
    except Exception as e:
        await message.answer(f"⚠️ Помилка експорту:\n<code>{e}</code>", parse_mode="HTML")


# ==========================
# Admin Panel
# ==========================
@dp.message(Command("panel"), F.chat.id == GROUP_CHAT_ID)
async def admin_panel(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Розсилка «Поділитися номером»", callback_data="bcast_phones")],
        [InlineKeyboardButton(text="📝 Розсилка тексту", callback_data="bcast_text")],
        [InlineKeyboardButton(text="🎯 Надіслати одному користувачу", callback_data="send_one")]
    ])
    await message.answer("🛠 Панель адміністратора", reply_markup=kb)


@dp.callback_query(F.data == "bcast_phones")
async def on_bcast_phones(call: CallbackQuery):
    users = await get_all_user_ids()
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("📞 Поділитися номером", request_contact=True))

    ok, fail = 0, 0
    for uid in users:
        try:
            await bot.send_message(uid, "📞 Будь ласка, поділіться номером телефону", reply_markup=kb)
            ok += 1
        except Exception:
            fail += 1
    await call.message.answer(f"✅ Відправлено: {ok}, не доставлено: {fail}")
    await call.answer()


@dp.callback_query(F.data == "bcast_text")
async def on_bcast_text(call: CallbackQuery):
    admin_id = call.from_user.id
    broadcast_text_state[admin_id] = True
    await call.message.answer("✏️ Відправ наступним своїм повідомленням текст для масової розсилки (тільки текст).")
    await call.answer()


@dp.message(F.chat.id == GROUP_CHAT_ID)
async def handle_broadcast_and_send_one(message: Message):
    admin_id = message.from_user.id

    # Масова текстова розсилка
    if broadcast_text_state.get(admin_id):
        broadcast_text_state[admin_id] = False
        if not message.text or message.text.startswith("/"):
            return await message.answer("⚠️ Потрібен звичайний текст без /команди.")
        users = await get_all_user_ids()
        ok, fail = 0, 0
        for uid in users:
            try:
                await bot.send_message(uid, message.text)
                ok += 1
            except Exception:
                fail += 1
        return await message.answer(f"📝 Розсилка виконана. Успішно: {ok}, з помилкою: {fail}")

    # Персональна відправка
    st = send_one_state.get(admin_id)
    if st:
        if st["phase"] == "ask_id":
            try:
                uid = int(message.text.strip())
                st["user_id"] = uid
                st["phase"] = "ask_msg"
                return await message.answer(f"🟢 Ок. Тепер надішли текст/медіа для користувача <code>{uid}</code>.", parse_mode="HTML")
            except Exception:
                return await message.answer("⚠️ Надішли саме числовий user_id.")
        elif st["phase"] == "ask_msg":
            uid = st["user_id"]
            try:
                if message.text:
                    await bot.send_message(uid, message.text)
                elif message.photo:
                    await bot.send_photo(uid, message.photo[-1].file_id, caption=message.caption)
                elif message.video:
                    await bot.send_video(uid, message.video.file_id, caption=message.caption)
                elif message.document:
                    await bot.send_document(uid, message.document.file_id, caption=message.caption)
                elif message.voice:
                    await bot.send_voice(uid, message.voice.file_id, caption=message.caption)
                else:
                    return await message.answer("⚠️ Підтримуються текст/фото/відео/файл/voice.")
                await message.answer(f"✅ Надіслано користувачу {uid}")
            except Exception as e:
                await message.answer(f"⚠️ Не вдалося надіслати користувачу {uid}\n{e}")
            finally:
                send_one_state.pop(admin_id, None)
            return


@dp.callback_query(F.data == "send_one")
async def on_send_one(call: CallbackQuery):
    admin_id = call.from_user.id
    send_one_state[admin_id] = {"phase": "ask_id"}
    await call.message.answer("Введи user_id одержувача (числом).")
    await call.answer()


# ==========================
# Commands scope for group
# ==========================
async def set_group_commands():
    cmds = [
        BotCommand(command="stats", description="Переглянути статистику"),
        BotCommand(command="export", description="Експорт у Google Sheets"),
        BotCommand(command="panel", description="Панель адміністратора"),
    ]
    await bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=GROUP_CHAT_ID))


# ==========================
# Run
# ==========================
async def main():
    await init_db()
    await set_group_commands()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
