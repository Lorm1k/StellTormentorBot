import asyncio
import time
import os

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message
from aiogram.filters import CommandStart, Command
from aiogram import BaseMiddleware

import httpx
import redis.asyncio as redis
from dotenv import load_dotenv

# =======================
# 🔐 CONFIG
# =======================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

# =======================
# ⚡ REDIS (кэш)
# =======================
redis_client = redis.from_url(REDIS_URL, decode_responses=True)


async def get_cache(key: str):
    return await redis_client.get(key)


async def set_cache(key: str, value: str, ttl: int = 300):
    await redis_client.set(key, value, ex=ttl)


# =======================
# 🌐 API CLIENT
# =======================
class APIClient:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10)

    async def get(self, url: str, params=None):
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()


api_client = APIClient()

# =======================
# 🧠 SEARCH SERVICE
# =======================
async def search_info(query: str) -> str:
    data = await api_client.get(
        "https://api.duckduckgo.com/",
        params={"q": query, "format": "json"}
    )

    if data.get("Abstract"):
        return f"🔎 {query}\n\n{data['Abstract']}"

    return "Ничего не найдено 🤷‍♂️"


# =======================
# 🛑 АНТИФЛУД
# =======================
class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_limit=1):
        self.rate_limit = rate_limit
        self.users = {}

    async def __call__(self, handler, event: Message, data):
        user_id = event.from_user.id
        now = time.time()

        last_time = self.users.get(user_id, 0)

        if now - last_time < self.rate_limit:
            await event.answer("Не спамь 😅")
            return

        self.users[user_id] = now
        return await handler(event, data)


# =======================
# 🤖 HANDLERS
# =======================
router = Router()


@router.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "🚀 Бот запущен\n\n"
        "Команды:\n"
        "/search запрос — поиск информации"
    )


@router.message(Command("search"))
async def search_handler(message: Message):
    query = message.text.replace("/search", "").strip()

    if not query:
        await message.answer("Напиши запрос после команды")
        return

    # проверка кэша
    cached = await get_cache(query)
    if cached:
        await message.answer(f"(из кэша)\n{cached}")
        return

    # запрос
    result = await search_info(query)

    # сохранить в кэш
    await set_cache(query, result)

    await message.answer(result)


# =======================
# 🚀 MAIN
# =======================
async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # middleware
    dp.message.middleware(ThrottlingMiddleware())

    # роутер
    dp.include_router(router)

    print("✅ Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
