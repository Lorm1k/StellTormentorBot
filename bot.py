import asyncio
import time
import os
import re
import logging

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
# LOGGING
# =======================
logging.basicConfig(level=logging.INFO)

# =======================
# CONFIG
# =======================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

# =======================
# REDIS
# =======================
redis_client = redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

async def get_cache(key):
    try:
        return await redis_client.get(key) if redis_client else None
    except Exception as e:
        logging.error(f"Redis get error: {e}")
        return None

async def set_cache(key, value, ttl=300):
    try:
        if redis_client:
            await redis_client.set(key, value, ex=ttl)
    except Exception as e:
        logging.error(f"Redis set error: {e}")

# =======================
# HTTP CLIENT
# =======================
client = httpx.AsyncClient(
    timeout=httpx.Timeout(10.0),
    headers={"User-Agent": "Mozilla/5.0"}
)

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
# DETECTORS (компилируем regex)
# =======================
PHONE_RE = re.compile(r"^\+?\d{10,15}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def is_phone(text):
    return PHONE_RE.match(text)

def is_email(text):
    return EMAIL_RE.match(text)

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
    except Exception as e:
        logging.error(f"Phone error: {e}")
        return "❌ Ошибка номера"

# =======================
# TELEGRAM
# =======================
async def get_user_info(bot, username):
    try:
        chat = await bot.get_chat(username)
        return f"👤 {chat.username}\n🆔 {chat.id}\n📛 {chat.first_name or ''}"
    except Exception as e:
        logging.warning(f"TG error: {e}")
        return "👤 Нет данных Telegram"

# =======================
# ПРОВЕРКА САЙТА
# =======================
async def check_profile(url):
    try:
        r = await client.get(url)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.title.string.strip() if soup.title else "нет"
            return f"✅ {url}\n📌 {title[:60]}"
        return f"❌ {url}"
    except Exception as e:
        logging.warning(f"Profile check error: {e}")
        return f"❌ {url}"

# =======================
# СОЦСЕТИ (параллельно 🚀)
# =======================
async def find_socials(username):
    uname = username.replace("@", "")

    sites = [
        f"https://instagram.com/{uname}",
        f"https://tiktok.com/@{uname}",
        f"https://github.com/{uname}",
        f"https://vk.com/{uname}",
    ]

    tasks = [check_profile(url) for url in sites]
    results = await asyncio.gather(*tasks)

    return "🌐 Проверка соцсетей:\n\n" + "\n\n".join(results)

# =======================
# ПОИСК
# =======================
async def parse_search(query):
    try:
        url = f"https://html.duckduckgo.com/html/?q={query}"
        r = await client.get(url)

        soup = BeautifulSoup(r.text, "html.parser")

        results = []
        for a in soup.select(".result__a")[:5]:
            results.append(a.get_text(strip=True))

        return "🔎 Найдено:\n" + "\n".join(results)
    except Exception as e:
        logging.error(f"Search error: {e}")
        return "❌ Ошибка поиска"

# =======================
# AI (простая логика)
# =======================
def analyze_text(text):
    words = text.lower()

    if any(x in words for x in ["dev", "github", "code"]):
        return "🧠 Похоже на IT / разработку"
    if "shop" in words:
        return "🧠 Возможно коммерция"
    if "blog" in words:
        return "🧠 Возможно блогер"

    return "🧠 Недостаточно данных"

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
    await message.answer("🚀 Отправь номер / @username / email")

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
        search = await parse_search(text)
        ai = analyze_text(search)

        result = f"{tg}\n\n{social}\n\n{search}\n\n{ai}"

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
    try:
        await dp.start_polling(bot)
    finally:
        await client.aclose()

if __name__ == "__main__":
    asyncio.run(main())
