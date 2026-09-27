import asyncio
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import os

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
LIVETENNIS_KEY = os.getenv("LIVETENNIS_API_KEY")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

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


# Хранилище отправленных сигналов
sent_signals = set()


def format_games(games, fav_index):
    """Красиво форматирует счёт по сетам с галочками/крестиками для фаворита"""
    if not games:
        return "—"
    lines = []
    for i, g in enumerate(games, start=1):
        if len(g) == 2:
            fav_g = g[fav_index]
            und_g = g[1 - fav_index]
            if fav_g > und_g:
                mark = "✅"
            elif fav_g < und_g:
                mark = "❌"
            else:
                mark = "➖"
            lines.append(f"   Сет {i}: {fav_g}–{und_g} {mark}")
    return "\n".join(lines)


async def check_signals(bot: Bot):
    global sent_signals
    while True:
        try:
            matches = await get_live_matches()
            print(f"=== Матчей: {len(matches)} ===")
            for m in matches:
                mid = m.get('id')
                tournament = m.get('tournament', '?')
                round_name = m.get('round', '')
                p1 = (m.get('players') or {}).get('p1') or {}
                p2 = (m.get('players') or {}).get('p2') or {}
                score = m.get('score') or {}
                sets = score.get('sets') or []
                games = score.get('games') or []

                r1 = p1.get('ranking')
                r2 = p2.get('ranking')

                favorite = None
                underdog = None
                fav_index = None
                fav_rank = None
                und_rank = None

                if r1 and r2:
                    if r1 < r2:
                        favorite, underdog, fav_index = p1, p2, 0
                        fav_rank, und_rank = r1, r2
                    else:
                        favorite, underdog, fav_index = p2, p1, 1
                        fav_rank, und_rank = r2, r1
                elif r1 and not r2:
                    favorite, underdog, fav_index = p1, p2, 0
                    fav_rank, und_rank = r1, "нет"
                elif r2 and not r1:
                    favorite, underdog, fav_index = p2, p1, 1
                    fav_rank, und_rank = r2, "нет"
                else:
                    continue

                # ФАВОРИТ ПРОИГРЫВАЕТ ПО СЕТАМ
                if len(sets) == 2:
                    fav_sets = sets[fav_index]
                    und_sets = sets[1 - fav_index]
                    if fav_sets < und_sets:
                        key = f"{mid}_setloss_{fav_sets}_{und_sets}"
                        if key not in sent_signals:
                            sent_signals.add(key)

                            games_str = format_games(games, fav_index)
                            round_str = f"🎾 Раунд: {round_name}\n" if round_name else ""

                            msg = (
                                f"🔴 <b>ФАВОРИТ ПРОИГРЫВАЕТ ПО СЕТАМ</b>\n\n"
                                f"🏆 <i>{tournament}</i>\n"
                                f"{round_str}\n"
                                f"<b>⭐ ФАВОРИТ:</b> {favorite.get('name')}\n"
                                f"   📊 Рейтинг: {fav_rank}\n\n"
                                f"<b>👤 АНДЕРДОГ:</b> {underdog.get('name')}\n"
                                f"   📊 Рейтинг: {und_rank}\n\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 <b>Счёт по сетам:</b> {sets[0]} : {sets[1]}\n"
                                f"🎯 <b>Геймы:</b>\n{games_str}\n\n"
                                f"⚠️ <i>Возможен заход на андердога</i>"
                            )
                            try:
                                await bot.send_message(chat_id=CHANNEL_ID, text=msg)
                                print(f"СИГНАЛ ОТПРАВЛЕН (сеты): {mid}")
                            except Exception as send_err:
                                print(f"Ошибка отправки: {send_err}")

        except Exception as e:
            print(f"Ошибка: {e}")

        await asyncio.sleep(120)


async def main():
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    asyncio.create_task(check_signals(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())