import asyncio
import time
import os
import re

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiogram import BaseMiddleware

import httpx
import redis.asyncio as redis
from dotenv import load_dotenv

import phonenumbers
from phonenumbers import geocoder, carrier

# =======================
# 🔐 CONFIG
# =======================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

# =======================
# ⚡ REDIS
# =======================
redis_client = None
if REDIS_URL:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

async def get_cache(key):
    if not redis_client:
        return None
    return await redis_client.get(key)

async def set_cache(key, value, ttl=300):
    if redis_client:
        await redis_client.set(key, value, ex=ttl)

# =======================
# 🌐 API CLIENT
# =======================
class APIClient:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10)

    async def get(self, url, params=None):
        r = await self.client.get(url, params=params)
        r.raise_for_status()
        return r.json()

api_client = APIClient()

# =======================
# 🛑 АНТИФЛУД
# =======================
class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_limit=1):
        self.rate_limit = rate_limit
        self.users = {}

    async def __call__(self, handler, event: Message, data):
        uid = event.from_user.id
        now = time.time()
        last = self.users.get(uid, 0)

        if now - last < self.rate_limit:
            await event.answer("Не спамь 😅")
            return

        self.users[uid] = now
        return await handler(event, data)

# =======================
# 🔍 ДЕТЕКТОРЫ
# =======================
def is_phone(text: str):
    return re.match(r"^\+?\d{10,15}$", text)

def is_email(text: str):
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text)

def is_username(text: str):
    return text.startswith("@")

# =======================
# 📱 PHONE INFO
# =======================
def get_phone_info(number_raw: str):
    try:
        number = phonenumbers.parse(number_raw)
        if not phonenumbers.is_valid_number(number):
            return "❌ Номер невалидный"

        country = geocoder.description_for_number(number, "ru")
        operator = carrier.name_for_number(number, "ru")

        return (
            f"📱 Номер: {number_raw}\n"
            f"🌍 Регион: {country}\n"
            f"📡 Оператор: {operator or 'неизвестно'}"
        )
    except:
        return "❌ Ошибка обработки номера"

# =======================
# 👤 TELEGRAM USER
# =======================
async def get_user_info(bot: Bot, username: str):
    try:
        chat = await bot.get_chat(username)

        return (
            f"👤 Username: {chat.username}\n"
            f"🆔 ID: {chat.id}\n"
            f"📛 Имя: {chat.first_name or ''} {chat.last_name or ''}\n"
            f"📝 Bio: {chat.bio or 'нет'}\n"
        )
    except:
        return "👤 Telegram: данных нет (не писал боту)\n"

# =======================
# 🌐 СОЦСЕТИ ПО ЮЗУ
# =======================
async def find_socials(username: str):
    uname = username.replace("@", "")

    links = [
        f"https://instagram.com/{uname}",
        f"https://tiktok.com/@{uname}",
        f"https://twitter.com/{uname}",
        f"https://github.com/{uname}",
        f"https://vk.com/{uname}",
        f"https://facebook.com/{uname}",
    ]

    result = "🌐 Возможные соцсети:\n"
    for link in links:
        result += f"{link}\n"

    # доп поиск
    search = await api_client.get(
        "https://api.duckduckgo.com/",
        params={"q": uname, "format": "json"}
    )

    if search.get("Abstract"):
        result += f"\n🔎 Инфо:\n{search['Abstract']}"

    return result

# =======================
# 📧 EMAIL CHECK
# =======================
async def get_email_info(email: str):
    domain = email.split("@")[-1]

    return (
        f"📧 Email: {email}\n"
        f"🌐 Домен: {domain}\n"
    )

# =======================
# 🤖 HANDLER
# =======================
router = Router()

@router.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "🚀 Бот запущен\n\n"
        "Просто отправь:\n"
        "📱 номер\n"
        "👤 @username\n"
        "📧 email\n\n"
        "Я найду открытую информацию 🔎"
    )

@router.message()
async def universal_handler(message: Message):
    text = message.text.strip()

    cached = await get_cache(text)
    if cached:
        await message.answer(f"(из кэша)\n{cached}")
        return

    if is_phone(text):
        result = get_phone_info(text)

    elif is_username(text):
        tg_info = await get_user_info(message.bot, text)
        socials = await find_socials(text)
        result = tg_info + "\n" + socials

    elif is_email(text):
        result = await get_email_info(text)

    else:
        result = "❌ Не удалось определить тип данных"

    await set_cache(text, result)
    await message.answer(result)

# =======================
# 🚀 MAIN
# =======================
async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.middleware(ThrottlingMiddleware())
    dp.include_router(router)

    print("✅ Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
