@echo off
REM ============================================================
REM  JARVIS — чистка git-репозиторію
REM  Прибирає з відстеження випадкову venv, логи та дампи.
REM  Робочі файли НЕ видаляються — лише untrack (git rm --cached).
REM  Запускати з кореня проєкту:  cleanup_repo.bat
REM ============================================================
chcp 65001 >nul
echo.
echo === 1. Untrack випадкової venv (.venvс) ===
git rm -r --cached --quiet ".venvс"

echo === 2. Untrack логів та дампів ===
git rm --cached --quiet jarvis_server.log.1 2>nul
git rm --cached --quiet jarvis_server.log.2 2>nul
git rm --cached --quiet jarvis_server.log.3 2>nul
git rm --cached --quiet profile_load.txt    2>nul
git rm --cached --quiet profile_raw.txt      2>nul
git rm --cached --quiet train.log            2>nul
git rm --cached --quiet reclean.log          2>nul

echo === 3. Залишок відстежуваних файлів ===
for /f %%i in ('git ls-files ^| find /c /v ""') do echo Tracked files: %%i

echo.
echo Готово. Перевірте зміни:  git status
echo Потім закомітьте:        git commit -m "chore: untrack accidental venv, logs and profile dumps"
pause
