import asyncio
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import os

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
LIVETENNIS_KEY = os.getenv("LIVETENNIS_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")

dp = Dispatcher()


async def get_live_matches():
    url = "https://api.livetennisapi.com/api/public/v1/matches"
    headers = {"Authorization": f"Bearer {LIVETENNIS_KEY}"}
    params = {"status": "live"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("data", [])
            return []


async def check_signals(bot: Bot):
    while True:
        try:
            matches = await get_live_matches()
            print(f"=== Матчей: {len(matches)} ===")
            import json
            for m in matches[:3]:
                print("---")
                print(f"ID: {m.get('id')} | {m.get('tournament')} | {m.get('status')}")
                score = m.get('score') or {}
                print(f"  sets: {score.get('sets')}")
                print(f"  games: {score.get('games')}")
                print(f"  points: {score.get('points')}")
                print(f"  server: {score.get('server')}")
                print(f"  is_tiebreak: {score.get('is_tiebreak')}")
                print(f"  age_seconds: {score.get('age_seconds')}")
                p1 = (m.get('players') or {}).get('p1') or {}
                p2 = (m.get('players') or {}).get('p2') or {}
                print(f"  p1: {p1.get('name')} (ranking: {p1.get('ranking')})")
                print(f"  p2: {p2.get('name')} (ranking: {p2.get('ranking')})")
        except Exception as e:
            print(f"Ошибка: {e}")




async def main():
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    asyncio.create_task(check_signals(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())