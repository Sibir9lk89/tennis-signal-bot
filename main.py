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
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST", "sportscore1.p.rapidapi.com")

dp = Dispatcher()

api_blocked_until = 0
sent_signals = set()
last_summary_time = 0
last_results_check = 0

STALE_THRESHOLD = 120

# --- Google Sheets ---
gs_client = None
sheet = None


def init_google_sheets():
    global gs_client, sheet
    try:
        if not GOOGLE_CREDS_BASE64:
            return False
        cleaned = GOOGLE_CREDS_BASE64.strip().strip('"').strip("'").replace("\n", "").replace(" ", "")
        decoded = base64.b64decode(cleaned).decode("utf-8")
        creds_dict = json.loads(decoded)
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gs_client = gspread.authorize(creds)
        spreadsheet = gs_client.open_by_key(GOOGLE_SHEET_ID)
        sheet = spreadsheet.sheet1
        print(f"=== GOOGLE: подключено к '{spreadsheet.title}' ===")
        return True
    except Exception as e:
        print(f"=== GOOGLE: ошибка: {e} ===")
        return False


def write_signal_to_sheet(row_data):
    global sheet
    if sheet is None:
        return
    try:
        sheet.append_row(row_data)
        print(f"=== GOOGLE: записана строка ===")
    except Exception as e:
        print(f"=== GOOGLE: ошибка записи: {e} ===")


# --- The Odds API ---
async def get_live_odds(player1, player2):
    if not THE_ODDS_API_KEY:
        return None
    tennis_keys = ["tennis_atp_china_open", "tennis_atp_japan_open", "tennis_wta_china_open"]
    p1_low, p2_low = player1.lower(), player2.lower()
    for sport_key in tennis_keys:
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
        params = {"apiKey": THE_ODDS_API_KEY, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200: continue
                    data = await resp.json()
                    if not isinstance(data, list): continue
                    for match in data:
                        home, away = (match.get("home_team") or "").lower(), (match.get("away_team") or "").lower()
                        if (p1_low in home or p1_low in away) and (p2_low in home or p2_low in away):
                            bookmakers = match.get("bookmakers") or []
                            if bookmakers:
                                markets = bookmakers[0].get("markets") or []
                                if markets:
                                    for o in (markets[0].get("outcomes") or []):
                                        if p2_low in (o.get("name") or "").lower():
                                            return o.get("price")
        except Exception:
            continue
    return None


# --- Live Tennis API ---
async def get_live_matches():
    global api_blocked_until
    now = int(time.time())
    if now < api_blocked_until:
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
                    err = await resp.json()
                    retry = err.get("retry_at_epoch")
                    api_blocked_until = int(retry) if retry else now + 3600
                except: api_blocked_until = now + 3600
                return []
            return []


def format_games(games, p1_name, p2_name):
    if not games: return "—"
    lines = []
    for i, g in enumerate(games, start=1):
        if len(g) == 2:
            g1, g2 = g[0], g[1]
            winner = f"победил {p1_name}" if g1 > g2 else (f"победил {p2_name}" if g2 > g1 else "идёт")
            lines.append(f"   Сет {i}: {g1}–{g2} ({winner})")
    return "\n".join(lines)


def get_favorite(p1, p2):
    r1, r2 = p1.get('ranking'), p2.get('ranking')
    if r1 and r2:
        return (p1, p2, 0, r1, r2) if r1 < r2 else (p2, p1, 1, r2, r1)
    elif r1 and not r2: return p1, p2, 0, r1, "нет"
    elif r2 and not r1: return p2, p1, 1, r2, "нет"
    return None


# --- Проверка результатов (SportScore) ---
async def check_finished_matches():
    global sheet, last_results_check
    now = int(time.time())
    if now - last_results_check < 1800:  # раз в 30 минут
        return
    last_results_check = now
    
    if not sheet or not RAPIDAPI_KEY:
        return

    print("=== RESULTS: проверка завершённых матчей ===")
    url = f"https://{RAPIDAPI_HOST}/events/date/{datetime.now().strftime('%Y-%m-%d')}"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=15) as resp:
                if resp.status != 200: return
                data = await resp.json()
                events = data.get("data", [])
                
                # Получаем все строки таблицы
                all_rows = sheet.get_all_values()
                for row_idx, row in enumerate(all_rows[1:], start=2):
                    if len(row) < 8 or row[7] == "": continue # если нет счёта или уже обработан
                    if len(row) > 11 and row[11] != "": continue # если L уже заполнено
                    
                    fav_name = row[3].strip().lower()
                    und_name = row[5].strip().lower()
                    
                    for ev in events:
                        home = ev.get("home", {}).get("name", "").lower()
                        away = ev.get("away", {}).get("name", "").lower()
                        
                        if (fav_name in home or fav_name in away) and (und_name in home or und_name in away):
                            status = ev.get("status", {}).get("description", "")
                            if "Finished" in status or "Retired" in status:
                                # Определяем победителя
                                home_score = ev.get("scores", {}).get("home", 0)
                                away_score = ev.get("scores", {}).get("away", 0)
                                
                                if home_score > away_score:
                                    winner = ev.get("home", {}).get("name", "")
                                else:
                                    winner = ev.get("away", {}).get("name", "")
                                
                                final_score = f"{home_score}:{away_score}"
                                is_fav_won = fav_name in winner.lower()
                                result_text = "✅ Фаворит отыгрался" if is_fav_won else "❌ Фаворит проиграл"
                                
                                # Записываем в J, K, L
                                sheet.update(f"J{row_idx}:L{row_idx}", [[final_score, winner, result_text]])
                                
                                # Красим строку
                                color = {"red": 0.8, "green": 1.0, "blue": 0.8} if is_fav_won else {"red": 1.0, "green": 0.8, "blue": 0.8}
                                sheet.format(f"A{row_idx}:L{row_idx}", {"backgroundColor": color})
                                print(f"=== RESULTS: обновлён матч {fav_name} vs {und_name} ===")
                                break
    except Exception as e:
        print(f"=== RESULTS: ошибка: {e} ===")


async def check_signals(bot: Bot):
    global sent_signals, last_summary_time
    while True:
        try:
            # 1. Проверка результатов
            await check_finished_matches()
            
            # 2. Получение live-матчей
            matches = await get_live_matches()
            print(f"=== Матчей: {len(matches)} ===")

            summary_lines = []
            first_set_lost_count = 0
            fresh_count = 0

            for m in matches:
                mid = m.get('id')
                tournament = m.get('tournament', '?')
                round_name = m.get('round', '')
                p1 = (m.get('players') or {}).get('p1') or {}
                p2 = (m.get('players') or {}).get('p2') or {}
                p1_name, p2_name = p1.get('name', '?'), p2.get('name', '?')
                score = m.get('score') or {}
                sets, games, age = score.get('sets') or [], score.get('games') or [], score.get('age_seconds')

                if age is not None and age > STALE_THRESHOLD: continue
                fresh_count += 1

                fav_data = get_favorite(p1, p2)
                if not fav_data: continue
                favorite, underdog, fav_index, fav_rank, und_rank = fav_data

                if len(sets) == 2:
                    fav_sets, und_sets = sets[fav_index], sets[1 - fav_index]
                    if fav_sets == 0 and und_sets == 1:
                        first_set_lost_count += 1
                        summary_lines.append(f"• {favorite.get('name')} проиграл 1-й сет")
                        key = f"{mid}_firstset"
                        if key not in sent_signals:
                            sent_signals.add(key)
                            games_str = format_games(games, p1_name, p2_name)
                            round_str = f"🎾 Раунд: {round_name}\n" if round_name else ""
                            odds = await get_live_odds(p1_name, p2_name)
                            odds_str = f"💰 <b>Live-кэф на андердога:</b> {odds}\n" if odds else ""
                            
                            msg = (
                                f"🔴 <b>ФАВОРИТ ПРОИГРАЛ ПЕРВЫЙ СЕТ</b>\n\n"
                                f"🏆 <i>{tournament}</i>\n{round_str}\n"
                                f"<b>⭐ ФАВОРИТ:</b> {favorite.get('name')}\n   📊 Рейтинг: {fav_rank}\n\n"
                                f"<b>👤 АНДЕРДОГ:</b> {underdog.get('name')}\n   📊 Рейтинг: {und_rank}\n\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📊 <b>Счёт по сетам:</b> {sets[0]} : {sets[1]}\n"
                                f"🎯 <b>Геймы:</b>\n{games_str}\n{odds_str}\n"
                                f"💡 <i>СТАВКА: на победу фаворита в матче (отыгрыш)</i>"
                            )
                            try:
                                await bot.send_message(chat_id=CHANNEL_ID, text=msg)
                                print(f"СИГНАЛ: {mid}")
                            except Exception as e: print(f"Ошибка отправки: {e}")
                            
                            write_signal_to_sheet([
                                datetime.now().strftime("%Y-%m-%d %H:%M"), tournament, round_name,
                                favorite.get('name'), str(fav_rank), underdog.get('name'), str(und_rank),
                                f"{sets[0]}:{sets[1]}", odds if odds else "", "", "", ""
                            ])

            # 3. Часовая сводка
            now = int(time.time())
            if now - last_summary_time >= 3600:
                last_summary_time = now
                summary_msg = (
                    f"📊 <b>СВОДКА ЗА ЧАС</b>\n\n🎾 Live-матчей (свежих): {fresh_count}\n"
                    f"🔴 Фаворитов проиграли 1-й сет: {first_set_lost_count}\n"
                )
                if summary_lines: summary_msg += f"\n<b>Кого смотреть:</b>\n" + "\n".join(summary_lines[:15])
                else: summary_msg += f"\n✅ Все фавориты пока в порядке"
                try:
                    await bot.send_message(chat_id=CHANNEL_ID, text=summary_msg)
                except: pass

        except Exception as e:
            print(f"Ошибка: {e}")

        await asyncio.sleep(900) # 15 минут


async def main():
    print("=== СТАРТ ===")
    init_google_sheets()
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    asyncio.create_task(check_signals(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())