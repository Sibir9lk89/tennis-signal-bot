import asyncio
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import os
import time
import json
import base64
from datetime import datetime

# Google Sheets
import gspread
from google.oauth2.service_account import Credentials

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
LIVETENNIS_KEY = os.getenv("LIVETENNIS_API_KEY")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS_BASE64 = os.getenv("GOOGLE_CREDS_BASE64")

dp = Dispatcher()

api_blocked_until = 0
sent_signals = set()
last_summary_time = 0

STALE_THRESHOLD = 120

# --- Google Sheets ---
gs_client = None
sheet = None


def init_google_sheets():
    """Подключается к Google-таблице. Возвращает True/False."""
    global gs_client, sheet
    try:
        if not GOOGLE_CREDS_BASE64:
            print("=== GOOGLE: нет переменной GOOGLE_CREDS_BASE64 ===")
            return False

        cleaned = GOOGLE_CREDS_BASE64.strip().strip('"').strip("'").replace("\n", "").replace(" ", "")
        decoded = base64.b64decode(cleaned).decode("utf-8")
        creds_dict = json.loads(decoded)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gs_client = gspread.authorize(creds)

        spreadsheet = gs_client.open_by_key(GOOGLE_SHEET_ID)
        sheet = spreadsheet.sheet1
        print(f"=== GOOGLE: подключено к таблице '{spreadsheet.title}' ===")
        return True
    except json.JSONDecodeError as e:
        print(f"=== GOOGLE: ошибка парсинга JSON: {e} ===")
        return False
    except Exception as e:
        print(f"=== GOOGLE: ошибка подключения: {e} ===")
        return False


def write_signal_to_sheet(row_data):
    """Записывает строку в таблицу."""
    global sheet
    if sheet is None:
        print("=== GOOGLE: таблица не подключена, пропускаем запись ===")
        return
    try:
        sheet.append_row(row_data)
        print(f"=== GOOGLE: записана строка: {row_data} ===")
    except Exception as e:
        print(f"=== GOOGLE: ошибка записи: {e} ===")


# --- The Odds API ---
async def get_live_odds(player1, player2):
    """Запрашивает live-коэффициенты у The Odds API по всем теннисным турнирам."""
    if not THE_ODDS_API_KEY:
        return None

    tennis_keys = [
        "tennis_atp_china_open",
        "tennis_atp_japan_open",
        "tennis_wta_china_open",
    ]

    p1_low = player1.lower()
    p2_low = player2.lower()

    for sport_key in tennis_keys:
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
        params = {
            "apiKey": THE_ODDS_API_KEY,
            "regions": "eu",
            "markets": "h2h",
            "oddsFormat": "decimal",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    if not isinstance(data, list):
                        continue
                    for match in data:
                        home = (match.get("home_team") or "").lower()
                        away = (match.get("away_team") or "").lower()
                        if (p1_low in home or p1_low in away) and (p2_low in home or p2_low in away):
                            bookmakers = match.get("bookmakers") or []
                            if bookmakers:
                                markets = bookmakers[0].get("markets") or []
                                if markets:
                                    outcomes = markets[0].get("outcomes") or []
                                    for o in outcomes:
                                        name = (o.get("name") or "").lower()
                                        if p2_low in name:
                                            return o.get("price")
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            print(f"=== ODDS API ({sport_key}): ошибка: {e} ===")
            continue

    return None


# --- Live Tennis API ---
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
    if not games:
        return "—"
    lines = []
    for i, g in enumerate(games, start=1):
        if len(g) == 2:
            g1 = g[0]
            g2 = g[1]
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

                if age is not None and age > STALE_THRESHOLD:
                    print(f"  матч {mid}: пропуск — устарели данные (age {age})")
                    continue

                fresh_count += 1

                fav_data = get_favorite(p1, p2)
                if not fav_data:
                    continue
                favorite, underdog, fav_index, fav_rank, und_rank = fav_data

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

                            odds = await get_live_odds(p1_name, p2_name)
                            odds_str = f"💰 <b>Live-кэф на андердога:</b> {odds}\n" if odds else ""

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
                                f"🎯 <b>Геймы (P1 – P2):</b>\n{games_str}\n"
                                f"{odds_str}\n"
                                f"⚠️ <i>Возможен заход на андердога</i>"
                            )
                            try:
                                await bot.send_message(chat_id=CHANNEL_ID, text=msg)
                                print(f"СИГНАЛ 1 (первый сет): {mid}")
                            except Exception as e:
                                print(f"Ошибка отправки: {e}")

                            write_signal_to_sheet([
                                datetime.now().strftime("%Y-%m-%d %H:%M"),
                                tournament,
                                round_name,
                                favorite.get('name'),
                                str(fav_rank),
                                underdog.get('name'),
                                str(und_rank),
                                f"{sets[0]}:{sets[1]}",
                                odds if odds else "",
                            ])

            # --- Часовая сводка ---
            now = int(time.time())
            if now - last_summary_time >= 3600:
                last_summary_time = now
                if summary_lines:
                    summary_text = "\n".join(summary_lines[:15])
                    summary_msg = (
                        f"📊 <b>СВОДКА ЗА ЧАС</b>\n\n"
                        f"🎾 Live-матчей (свежих): {fresh_count}\n"
                        f"🔴 Фаворитов проиграли 1-й сет: {first_set_lost_count}\n\n"
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
    print("=== СТАРТ: подключение к Google Sheets ===")
    init_google_sheets()

    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    asyncio.create_task(check_signals(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())