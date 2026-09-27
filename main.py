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
            print(f"=== Получено матчей: {len(matches)} ===")
            if matches:
                print("=== ПОЛНЫЕ ДАННЫЕ ПЕРВОГО МАТЧА ===")
                import json
                print(json.dumps(matches[0], indent=2, ensure_ascii=False))
                print("=== КОНЕЦ ДАННЫХ ===")
        except Exception as e:
            print(f"Ошибка: {e}")

        await asyncio.sleep(900)


async def main():
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    asyncio.create_task(check_signals(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())