import os
import re
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import Message, User
from aiogram.exceptions import TelegramMigrateToChat

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "").strip())
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("totis-bot")

bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# ---------------- HELPERS ----------------
def fmt_header(user: User) -> str:
    """Формує заголовок для повідомлення у групу"""
    fullname = user.full_name
    uname = f"@{user.username}" if user.username else "—"
    return (
        "<b>Тестування штучного інтелекту в застосунку TOTIS</b>\n"
        "<b>Нове повідомлення</b>\n"
        f"👤 {fullname} ({uname})\n"
        f"🆔 <code>{user.id}</code>\n\n"
    )

async def send_to_group_safe(text=None, photo_id=None, document_id=None, caption=None):
    """Надсилає повідомлення в групу з перевіркою міграції."""
    global GROUP_CHAT_ID
    try:
        if photo_id:
            msg = await bot.send_photo(GROUP_CHAT_ID, photo_id, caption=caption or text)
        elif document_id:
            msg = await bot.send_document(GROUP_CHAT_ID, document_id, caption=caption or text)
        else:
            msg = await bot.send_message(GROUP_CHAT_ID, text)
        return msg.message_id
    except TelegramMigrateToChat as e:
        GROUP_CHAT_ID = e.params.migrate_to_chat_id
        return await bot.send_message(GROUP_CHAT_ID, text or caption)
    except Exception as e:
        log.error(f"Помилка надсилання в групу: {e}")

# ---------------- START ----------------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return
    await message.answer("👋 Вітаємо! Надішліть текст, фото або файл — я передам його адміністраторам TOTIS.")

# ---------------- USER → GROUP ----------------
@dp.message(F.chat.type == ChatType.PRIVATE)
async def user_to_group(message: Message):
    me = await bot.me()
    if message.from_user.id == me.id:
        return

    header = fmt_header(message.from_user)
    caption = message.caption or ""
    text = message.text or caption

    if message.photo:
        photo = message.photo[-1]
        await send_to_group_safe(photo_id=photo.file_id, caption=f"{header}{caption}")
    elif message.document:
        await send_to_group_safe(document_id=message.document.file_id, caption=f"{header}{caption}")
    else:
        await send_to_group_safe(text=f"{header}{text}")

# ---------------- GROUP → USER ----------------
@dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.reply_to_message)
async def reply_to_user(message: Message):
    try:
        replied = message.reply_to_message
        if not replied:
            return

        # шукаємо ID у тексті або підписі
        payload = (replied.text or "") + "\n" + (replied.caption or "")
        match = re.search(r"🆔 <code>(\d+)</code>", payload)
        if not match:
            return

        user_id = int(match.group(1))
        me = await bot.me()
        if user_id == me.id:
            return

        if message.text:
            await bot.send_message(user_id, f"💬 <b>Відповідь адміністратора:</b>\n\n{message.text}")
        elif message.photo:
            await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption or "")
        elif message.document:
            await bot.send_document(user_id, message.document.file_id, caption=message.caption or "")
        else:
            await bot.send_message(user_id, "💬 Адміністратор надіслав відповідь.")

        await message.reply("✅ Надіслано користувачу.", reply=False)
        log.info(f"✅ Reply sent to user {user_id}")

    except Exception as e:
        log.error(f"Помилка надсилання відповіді: {e}")
        await message.reply(f"❌ Помилка надсилання користувачу: {e}", reply=False)

# ---------------- RUN ----------------
async def main():
    me = await bot.me()
    log.info(f"✅ Bot started as @{me.username} ({me.id})")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
