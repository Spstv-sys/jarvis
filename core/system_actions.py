import os
import re
import sys
import time
import threading
import logging
import subprocess
import psutil

# ОС-залежні бібліотеки автоматизації — опційні: на headless-сервері
# (без дисплея/прав) їх імпорт може падати, тож не валимо весь модуль.
try:
    import keyboard
except Exception:
    keyboard = None
try:
    import screen_brightness_control as sbc
except Exception:
    sbc = None

import config

logger = logging.getLogger(__name__)


class SystemActions:
    def __init__(self, audio_manager, db):
        self.audio = audio_manager
        self.db    = db

    # -------------------------------------------------------------------------
    # ТАЙМЕР
    # -------------------------------------------------------------------------

    def cmd_timer(self, cmd: str) -> str:
        match = re.search(r'(\d+)\s*(секунд|хвилин|годин)', cmd)
        if not match:
            self.audio.speak("Не зрозумів, на скільки часу поставити таймер.")
            return "CONTINUE"

        amt, unit = int(match.group(1)), match.group(2)

        MULTIPLIERS = {"секунд": 1, "хвилин": 60, "годин": 3600}
        secs = amt * MULTIPLIERS.get(unit, 1)

        act = re.search(r'(що|про)\s+(.+)', cmd)
        msg = act.group(2) if act else "Час вийшов!"

        self.audio.speak(f"Таймер на {amt} {unit} запущено.")

        def timer_thread(wait_time: int, message: str) -> None:
            time.sleep(wait_time)
            # ✅ import sys на рівні модуля + winsound як резерв для Windows
            try:
                import winsound
                for _ in range(3):
                    winsound.Beep(1000, 400)
                    time.sleep(0.3)
            except Exception:
                # Fallback для не-Windows
                for _ in range(3):
                    sys.stdout.write('\a')
                    sys.stdout.flush()
                    time.sleep(0.5)
            self.audio.speak(f"Нагадую: {message}")

        threading.Thread(
            target=timer_thread,
            args=(secs, msg),
            daemon=True
        ).start()
        return "CONTINUE"

    # -------------------------------------------------------------------------
    # АПАРАТНЕ УПРАВЛІННЯ
    # -------------------------------------------------------------------------

    def cmd_hardware_control(self, cmd: str) -> str:
        if "яскравість" in cmd and sbc is None:
            self.audio.speak("Керування яскравістю недоступне на цій платформі.")
            return "CONTINUE"
        if "яскравість" not in cmd and keyboard is None:
            self.audio.speak("Керування гучністю недоступне на цій платформі.")
            return "CONTINUE"
        try:
            if "гучніше" in cmd:
                # ✅ Визначаємо крок з команди або використовуємо дефолт
                step = int(re.search(r'(\d+)', cmd).group(1)) if re.search(r'(\d+)', cmd) else 10
                for _ in range(step):
                    keyboard.send("volume up")
                self.audio.speak(f"Гучність збільшено.")

            elif "тихіше" in cmd:
                step = int(re.search(r'(\d+)', cmd).group(1)) if re.search(r'(\d+)', cmd) else 10
                for _ in range(step):
                    keyboard.send("volume down")
                self.audio.speak("Гучність зменшено.")

            elif "вимкни звук" in cmd:
                keyboard.send("volume mute")
                self.audio.speak("Звук вимкнено.")

            elif "яскравість" in cmd:
                level = re.search(r'(\d+)', cmd)
                if level:
                    val = max(0, min(100, int(level.group(1))))  # ✅ Клампінг 0-100
                    sbc.set_brightness(val)
                    self.audio.speak(f"Яскравість {val} відсотків.")
                else:
                    self.audio.speak("Вкажіть рівень яскравості від нуля до ста.")

        except Exception as e:
            logger.error(f"Помилка управління системою: {e}")
            self.audio.speak("Не вдалося змінити налаштування системи.")
        return "CONTINUE"

    # -------------------------------------------------------------------------
    # ДОДАТКИ ТА ОС
    # -------------------------------------------------------------------------

    def cmd_apps_and_os(self, cmd: str) -> str:
        try:
            if "час кодити" in cmd:
                subprocess.Popen(["code"], shell=False)
                self.audio.speak("Відкриваю VS Code.")

            elif "відкрий танки" in cmd:
                # ✅ Шлях береться з config або env
                path = getattr(config, "WOT_PATH", None) or os.getenv(
                    "WOT_PATH", r"E:\Games\World_of_Tanks_EU\WorldOfTanks.exe"
                )
                if os.path.exists(path):
                    os.startfile(path)
                    self.audio.speak("Запускаю гру.")
                else:
                    self.audio.speak("Шлях до гри не знайдено. Перевірте конфігурацію.")

            elif "диск" in cmd:
                # ✅ Виправлено: шукаємо літеру диску в cmd через regex
                match = re.search(r'\b([a-hA-Hасе])\b', cmd)
                if match:
                    letter = match.group(1).upper()
                    drive_path = f"{letter}:\\"
                    if os.path.exists(drive_path):
                        os.startfile(drive_path)
                        self.audio.speak(f"Диск {letter} відкрито.")
                    else:
                        self.audio.speak(f"Диск {letter} не знайдено.")
                else:
                    self.audio.speak("Не зрозумів, який диск відкрити.")

            elif "закрий" in cmd:
                app_raw = cmd.replace("закрий", "").strip()
                # ✅ apps_map береться з config якщо є
                apps_map = getattr(config, "APPS_MAP", {
                    "хром":    "chrome.exe",
                    "телеграм":"Telegram.exe",
                    "код":     "Code.exe",
                    "танки":   "WorldOfTanks.exe",
                })
                p_name = apps_map.get(app_raw, app_raw + ".exe")
                killed = False
                for proc in psutil.process_iter(['name']):
                    try:
                        if proc.info['name'] and proc.info['name'].lower() == p_name.lower():
                            proc.kill()
                            killed = True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue  # ✅ Процес вже завершився або немає прав
                self.audio.speak(
                    f"Закрито {app_raw}." if killed
                    else f"Не знайшов процес {app_raw}."
                )

        except Exception as e:
            logger.error(f"Помилка роботи з додатком: {e}")
            self.audio.speak("Виникла помилка під час роботи з додатком.")
        return "CONTINUE"

    # -------------------------------------------------------------------------
    # ВИМКНЕННЯ
    # -------------------------------------------------------------------------

    def cmd_system_exit(self, cmd: str) -> str:
        if "вимкни комп" in cmd or "завершити роботу" in cmd:
            self.audio.speak("Ви підтверджуєте зупинку системи?")

            # ✅ timeout щоб не зависало вічно
            confirm = self.audio.listen_for_command()

            if confirm and any(w in confirm for w in ["так", "підтверджую"]):
                self.audio.speak("Вимикаю живлення.")
                subprocess.Popen(["shutdown", "/s", "/t", "0"], shell=False)
                return "STOP"

            self.audio.speak("Скасовано.")
            return "CONTINUE"

        self.audio.speak("Припиняю роботу. До зустрічі.")
        return "STOP"