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

from bs4 import BeautifulSoup

# =======================
# CONFIG
# =======================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

# =======================
# REDIS
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
# HTTP CLIENT
# =======================
client = httpx.AsyncClient(timeout=10, headers={
    "User-Agent": "Mozilla/5.0"
})

# =======================
# АНТИФЛУД
# =======================
class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_limit=1):
        self.rate_limit = rate_limit
        self.users = {}

    async def __call__(self, handler, event: Message, data):
        uid = event.from_user.id
        now = time.time()

        if now - self.users.get(uid, 0) < self.rate_limit:
            await event.answer("Не спамь 😅")
            return

        self.users[uid] = now
        return await handler(event, data)

# =======================
# ДЕТЕКТОРЫ
# =======================
def is_phone(text):
    return re.match(r"^\+?\d{10,15}$", text)

def is_email(text):
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text)

def is_username(text):
    return text.startswith("@")

# =======================
# PHONE
# =======================
def get_phone_info(number_raw):
    try:
        number = phonenumbers.parse(number_raw)
        if not phonenumbers.is_valid_number(number):
            return "❌ Номер невалидный"

        return (
            f"📱 {number_raw}\n"
            f"🌍 {geocoder.description_for_number(number, 'ru')}\n"
            f"📡 {carrier.name_for_number(number, 'ru') or 'неизвестно'}"
        )
    except:
        return "❌ Ошибка номера"

# =======================
# TELEGRAM
# =======================
async def get_user_info(bot, username):
    try:
        chat = await bot.get_chat(username)
        return f"👤 {chat.username}\n🆔 {chat.id}\n📛 {chat.first_name or ''}"
    except:
        return "👤 Нет данных Telegram"

# =======================
# 🌐 ПАРСИНГ ПОИСКА
# =======================
async def parse_search(query):
    try:
        url = f"https://html.duckduckgo.com/html/?q={query}"
        r = await client.get(url)

        soup = BeautifulSoup(r.text, "html.parser")

        results = []
        for a in soup.select(".result__a")[:5]:
            title = a.get_text(strip=True)
            link = a.get("href")
            results.append(f"🔗 {title}\n{link}")

        if not results:
            return "🔎 Ничего не найдено"

        return "🔎 Найдено:\n\n" + "\n\n".join(results)

    except:
        return "❌ Ошибка парсинга"

# =======================
# СОЦСЕТИ
# =======================
async def find_socials(username):
    uname = username.replace("@", "")

    links = [
        f"https://instagram.com/{uname}",
        f"https://tiktok.com/@{uname}",
        f"https://github.com/{uname}",
        f"https://vk.com/{uname}",
    ]

    result = "🌐 Соцсети:\n" + "\n".join(links)

    deep = await parse_search(uname)

    return result + "\n\n" + deep

# =======================
# EMAIL
# =======================
async def get_email_info(email):
    return f"📧 {email}\n🌐 {email.split('@')[-1]}"

# =======================
# HANDLER
# =======================
router = Router()

@router.message(CommandStart())
async def start_handler(message: Message):
    await message.answer("🚀 Просто отправь номер / @username / email")

@router.message()
async def universal_handler(message: Message):
    text = message.text.strip()

    cached = await get_cache(text)
    if cached:
        await message.answer(f"(кэш)\n{cached}")
        return

    if is_phone(text):
        result = get_phone_info(text)

    elif is_username(text):
        tg = await get_user_info(message.bot, text)
        social = await find_socials(text)
        result = tg + "\n\n" + social

    elif is_email(text):
        result = await get_email_info(text)

    else:
        result = await parse_search(text)

    await set_cache(text, result)
    await message.answer(result)

# =======================
# MAIN
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
