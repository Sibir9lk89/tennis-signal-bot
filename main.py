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
                if resp.status != 200: 
                    print(f"=== RESULTS: статус {resp.status} ===")
                    return
                data = await resp.json()
                events = data.get("data", [])
                
                # Получаем все строки таблицы
                all_rows = sheet.get_all_values()
                
                # Ищем только последние 200 строк, чтобы не тормозить
                start_row = max(2, len(all_rows) - 200)
                for row_idx, row in enumerate(all_rows[start_row-1:], start=start_row):
                    if len(row) < 8 or row[7] == "": continue # если нет счёта
                    if len(row) > 11 and row[11] != "": continue # если L уже заполнено
                    
                    fav_name = row[3].strip().lower()
                    und_name = row[5].strip().lower()
                    
                    for ev in events:
                        home = ev.get("home", {}).get("name", "").lower()
                        away = ev.get("away", {}).get("name", "").lower()
                        
                        # Проверяем совпадение имён (хотя бы одно вхождение)
                        if (fav_name in home or fav_name in away) and (und_name in home or und_name in away):
                            status = ev.get("status", {}).get("description", "")
                            # Спортскор использует "Finished" или "Ended" или "Retired"
                            if "Finished" in status or "Ended" in status or "Retired" in status:
                                # Определяем победителя по сетам
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
                                if is_fav_won:
                                    color = {"red": 0.8, "green": 1.0, "blue": 0.8} # Зеленый
                                else:
                                    color = {"red": 1.0, "green": 0.8, "blue": 0.8} # Красный
                                
                                try:
                                    sheet.format(f"A{row_idx}:L{row_idx}", {"backgroundColor": color})
                                except Exception as e:
                                    print(f"Ошибка покраски: {e}")
                                    
                                print(f"=== RESULTS: обновлён матч {fav_name} vs {und_name} ===")
                                break
    except Exception as e:
        print(f"=== RESULTS: ошибка: {e} ===")