import asyncio
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import os
import time

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
LIVETENNIS_KEY = os.getenv("LIVETENNIS_API_KEY")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

dp = Dispatcher()

api_blocked_until = 0
sent_signals = set()
last_summary_time = 0

# Порог устаревания данных (в секундах)
STALE_THRESHOLD = 120


async def get_live_matches():
    global api_blocked_until
    now = int(time.time())

    if now < api_blocked_until:
        remain = api_blocked_until - now
        print(f"=== API ЗАБЛОКИРОВАН, ждём {remain // 60} мин ===")
        return []

    url = "https://api.livetennisapi.com/api/public/v1/matches"
    headers = {"Authorization": f"Bearer {LIVETENNIS_KEY}"}
    params = {"status": "live"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("data", [])
            elif resp.status == 429:
                try:
                    err_data = await resp.json()
                    retry_at = err_data.get("retry_at_epoch")
                    if retry_at:
                        api_blocked_until = int(retry_at)
                        print(f"=== API 429, блокировка до {api_blocked_until} ===")
                    else:
                        api_blocked_until = now + 3600
                        print(f"=== API 429, блокировка на 1 час ===")
                except Exception:
                    api_blocked_until = now + 3600
                return []
            else:
                print(f"=== API STATUS: {resp.status} ===")
                return []


def format_games(games, p1_name, p2_name):
    """
    Форматирует геймы так:
    Сет 1: 1–6 (победил: p2_name)
    Сет 2: 6–3 (победил: p1_name)
    где первое число — геймы p1, второе — геймы p2.
    """
    if not games:
        return "—"
    lines = []
    for i, g in enumerate(games, start=1):
        if len(g) == 2:
            g1 = g[0]  # геймы p1
            g2 = g[1]  # геймы p2
            if g1 > g2:
                winner = f"победил {p1_name}"
            elif g2 > g1:
                winner = f"победил {p2_name}"
            else:
                winner = "идёт"
            lines.append(f"   Сет {i}: {g1}–{g2} ({winner})")
    return "\n".join(lines)


def get_favorite(p1, p2):
    r1 = p1.get('ranking')
    r2 = p2.get('ranking')
    if r1 and r2:
        if r1 < r2:
            return p1, p2, 0, r1, r2
        else:
            return p2, p1, 1, r2, r1
    elif r1 and not r2:
        return p1, p2, 0, r1, "нет"
    elif r2 and not r1:
        return p2, p1, 1, r2, "нет"
    return None


async def check_signals(bot: Bot):
    global sent_signals, last_summary_time
    while True:
        try:
            matches = await get_live_matches()
            print(f"=== Матчей: {len(matches)} ===")

            summary_lines = []
            favorites_losing = 0
            first_set_lost_count = 0
            fresh_count = 0

            for m in matches:
                mid = m.get('id')
                tournament = m.get('tournament', '?')
                round_name = m.get('round', '')
                p1 = (m.get('players') or {}).get('p1') or {}
                p2 = (m.get('players') or {}).get('p2') or {}
                p1_name = p1.get('name', '?')
                p2_name = p2.get('name', '?')
                score = m.get('score') or {}
                sets = score.get('sets') or []
                games = score.get('games') or []
                age = score.get('age_seconds')

                # Пропускаем устаревшие матчи
                if age is not None and age > STALE_THRESHOLD:
                    print(f"  матч {mid}: пропуск — данные устарели (age {age} сек)")
                    continue

                fresh_count += 1

                fav_data = get_favorite(p1, p2)
                if not fav_data:
                    print(f"  матч {mid}: пропуск — нет рейтинга у обоих")
                    continue
                favorite, underdog, fav_index, fav_rank, und_rank = fav_data

                # Отладка
                print(f"=== DEBUG матч {mid} ===")
                print(f"  p1: {p1_name} (rank {p1.get('ranking')})")
                print(f"  p2: {p2_name} (rank {p2.get('ranking')})")
                print(f"  favorite: {favorite.get('name')} (index {fav_index})")
                print(f"  sets: {sets} | games: {games} | age: {age}")
                print(f"=== КОНЕЦ DEBUG ===")

                # --- СИГНАЛ 1: Фаворит проиграл первый сет ---
                if len(sets) == 2:
                    fav_sets = sets[fav_index]
                    und_sets = sets[1 - fav_index]
                    if fav_sets == 0 and und_sets == 1:
                        favorites_losing += 1
                        first_set_lost_count += 1
                        summary_lines.append(
                            f"• {favorite.get('name')} проиграл 1-й сет vs {underdog.get('name')}"
                        )
                        key = f"{mid}_firstset"
                        if key not in sent_signals:
                            sent_signals.add(key)
                            games_str = format_games(games, p1_name, p2_name)
                            round_str = f"🎾 Раунд: {round_name}\n" if round_name else ""
                            msg = (
                                f"🔴 <b>ФАВОРИТ ПРОИГРАЛ ПЕРВЫЙ СЕТ</b>\n\n"
                                f"🏆 <i>{tournament}</i>\n"
                                f"{round_str}\n"
                                f"<b>⭐ ФАВОРИТ:</b> {favorite.get('name')}\n"
                                f"   📊 Рейтинг: {fav_rank}\n\n"
                                f"<b>👤 АНДЕРДОГ:</b> {underdog.get('name')}\n"
                                f"   📊 Рейтинг: {und_rank}\n\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 <b>Счёт по сетам:</b> {sets[0]} : {sets[1]}\n"
                                f"🎯 <b>Геймы (P1 – P2):</b>\n{games_str}\n\n"
                                f"⚠️ <i>Возможен заход на андердога</i>"
                            )
                            try:
                                await bot.send_message(chat_id=CHANNEL_ID, text=msg)
                                print(f"СИГНАЛ 1 (первый сет): {mid}")
                            except Exception as e:
                                print(f"Ошибка отправки: {e}")

                # --- СИГНАЛ 2: Фаворит проигрывает по сетам (0:2, 1:2) ---
                if len(sets) == 2:
                    fav_sets = sets[fav_index]
                    und_sets = sets[1 - fav_index]
                    if fav_sets < und_sets and und_sets > 1:
                        key = f"{mid}_setloss_{fav_sets}_{und_sets}"
                        if key not in sent_signals:
                            sent_signals.add(key)
                            games_str = format_games(games, p1_name, p2_name)
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
                                f"🎯 <b>Геймы (P1 – P2):</b>\n{games_str}\n\n"
                                f"⚠️ <i>Возможен заход на андердога</i>"
                            )
                            try:
                                await bot.send_message(chat_id=CHANNEL_ID, text=msg)
                                print(f"СИГНАЛ 2 (сеты): {mid}")
                            except Exception as e:
                                print(f"Ошибка отправки: {e}")

            # --- Часовая сводка ---
            now = int(time.time())
            if now - last_summary_time >= 3600:
                last_summary_time = now
                if summary_lines:
                    summary_text = "\n".join(summary_lines[:15])
                    summary_msg = (
                        f"📊 <b>СВОДКА ЗА ЧАС</b>\n\n"
                        f"🎾 Live-матчей (свежих): {fresh_count}\n"
                        f"🔴 Фаворитов проиграли 1-й сет: {first_set_lost_count}\n"
                        f"⚠️ Всего фаворитов в невыгодном положении: {favorites_losing}\n\n"
                        f"<b>Кого смотреть:</b>\n{summary_text}"
                    )
                else:
                    summary_msg = (
                        f"📊 <b>СВОДКА ЗА ЧАС</b>\n\n"
                        f"🎾 Live-матчей (свежих): {fresh_count}\n"
                        f"✅ Все фавориты пока в порядке"
                    )
                try:
                    await bot.send_message(chat_id=CHANNEL_ID, text=summary_msg)
                    print(f"СВОДКА ОТПРАВЛЕНА")
                except Exception as e:
                    print(f"Ошибка отправки сводки: {e}")

        except Exception as e:
            print(f"Ошибка: {e}")

        await asyncio.sleep(900)


async def main():
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    asyncio.create_task(check_signals(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())