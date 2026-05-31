@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM ============================================================
REM  JARVIS — повна git-чистка + коміт + push (один запуск).
REM  Безпечно: НЕ комітить, якщо індекс у небезпечному стані.
REM ============================================================

echo === 1. Прибираю застряглий lock (якщо є) ===
del /f /q ".git\index.lock" 2>nul

echo === 2. Перевіряю/відновлюю індекс ===
git ls-files --error-unmatch main.py >nul 2>&1
if errorlevel 1 (
  echo     індекс пошкоджено — перебудовую з HEAD...
  del /f /q ".git\index.lock" 2>nul
  del /f /q ".git\index"      2>nul
  git reset -q
)

echo === 3. САНІТАРНИЙ ГЕЙТ ===
git ls-files --error-unmatch main.py >nul 2>&1
if errorlevel 1 (
  echo [X] Індекс у небезпечному стані: main.py не відстежено.
  echo     Коміт СКАСОВАНО, щоб не записати видалення всього репо.
  echo     Закрий запущені Jarvis / git-GUI / редактори і запусти скрипт ще раз.
  pause
  exit /b 1
)
echo     OK: індекс осудний, продовжую.

echo === 4. Untrack сміття (--ignore-unmatch: не падає на відсутніх) ===
git rm -r --cached --quiet --ignore-unmatch ".venvс"
git rm --cached --quiet --ignore-unmatch jarvis_server.log jarvis_server.log.1 jarvis_server.log.2 jarvis_server.log.3
git rm --cached --quiet --ignore-unmatch jarvis_system.log train.log reclean.log electron_output.log server_stdout.log
git rm --cached --quiet --ignore-unmatch profile_load.txt profile_raw.txt
git rm --cached --quiet --ignore-unmatch jarvis_core.db jarvis_episodic.sqlite jarvis_thoughts.sqlite jarvis_intent_cache.sqlite

echo === 5. .gitignore у коміт ===
git add -A

echo === 6. Коміт ===
git commit -m "refactor+chore: decompose god-files, DI-container, Protocol facades, 330+ tests, hard sandbox, CI, EOL normalize; untrack .venvс/logs/dumps"
if errorlevel 1 echo (нічого комітити або коміт не потрібен — продовжую до push)

echo === 7. Push на origin/main ===
git push origin main

echo.
echo Готово. Перевір: git status  та  git log -1 --stat
pause
