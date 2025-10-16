import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ContentType
from aiogram.filters import CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

# Завантажуємо змінні з .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID"))
PDF_URL = os.getenv("PDF_URL")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Словник для зв’язку user_message_id ↔ group_message_id
message_links = {}


@dp.message(CommandStart())
async def start_handler(message: Message):
    welcome_text = (
        "👋 Вітаємо в проєкті <b>«Тестування штучного інтелекту в застосунку TOTIS»</b>!\n\n"
        "🧾 Ознайомтесь з інструкцією за посиланням:\n"
        f"{PDF_URL}\n\n"
        "Після цього можете надіслати своє повідомлення, фото або відео — "
        "воно автоматично потрапить до команди розробників."
    )
    await message.answer(welcome_text, parse_mode="HTML")


# Обробник повідомлень від користувачів
@dp.message(F.content_type.in_({ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO, ContentType.VOICE}))
async def forward_to_group(message: Message):
    user = message.from_user
    user_info = f"👤 Від: {user.full_name or 'Невідомо'} (ID: {user.id})"

    if message.text:
        caption = f"✉️ Нове повідомлення від користувача\n{user_info}\n\n{message.text}"
        sent = await bot.send_message(GROUP_CHAT_ID, caption)
    elif message.photo:
        caption = f"🖼 Фото від користувача\n{user_info}"
        sent = await bot.send_photo(GROUP_CHAT_ID, photo=message.photo[-1].file_id, caption=caption)
    elif message.video:
        caption = f"🎥 Відео від користувача\n{user_info}"
        sent = await bot.send_video(GROUP_CHAT_ID, video=message.video.file_id, caption=caption)
    elif message.voice:
        caption = f"🎙 Голосове повідомлення від користувача\n{user_info}"
        sent = await bot.send_voice(GROUP_CHAT_ID, voice=message.voice.file_id, caption=caption)
    else:
        return

    # Запам'ятовуємо зв’язок
    message_links[sent.message_id] = user.id


# Обробка відповіді з групи
@dp.message(F.chat.id == GROUP_CHAT_ID, F.reply_to_message)
async def reply_from_group(message: Message):
    replied_id = message.reply_to_message.message_id

    if replied_id in message_links:
        user_id = message_links[replied_id]
        try:
            await bot.send_message(user_id, f"💬 Відповідь від команди:\n\n{message.text}")
        except Exception as e:
            await bot.send_message(GROUP_CHAT_ID, f"⚠️ Не вдалося надіслати повідомлення користувачу {user_id}\n{e}")
    else:
        await bot.send_message(GROUP_CHAT_ID, "⚠️ Не вдалося знайти користувача для цього повідомлення.")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
