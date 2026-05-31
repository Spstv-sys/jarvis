@echo off
REM ============================================================
REM  JARVIS - чистка git-репозиторію (запускати з кореня проєкту)
REM  Прибирає з ВІДСТЕЖЕННЯ випадкову venv, ротовані логи та дампи.
REM  Робочі файли НЕ видаляються - лише untrack (git rm --cached).
REM
REM  Станом на аудит у git відстежено 758 файлів, з них 567 - .venvс
REM  (випадкова копія venv, ~75%% репозиторію). Бази (*.db/*.sqlite)
REM  та більшість логів НЕ відстежуються - чіпати не треба.
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

REM --- 0. Якщо git лається на "index file corrupt" - розкоментуй 3 рядки:
REM del /f /q ".git\index.lock" 2>nul
REM del /f /q ".git\index"      2>nul
REM git reset -q

echo.
echo === 1. Untrack випадкової venv (.venvс - кирилична "с") ===
git rm -r --cached --quiet ".venvс" 2>nul

echo === 2. Untrack ротованих логів і дампів профілю ===
git rm --cached --quiet jarvis_server.log.1 2>nul
git rm --cached --quiet jarvis_server.log.2 2>nul
git rm --cached --quiet jarvis_server.log.3 2>nul
git rm --cached --quiet profile_load.txt    2>nul
git rm --cached --quiet profile_raw.txt     2>nul

REM На випадок, якщо щось із цього додадуть пізніше (no-op якщо не відстежено):
git rm --cached --quiet jarvis_server.log jarvis_system.log 2>nul
git rm --cached --quiet train.log reclean.log electron_output.log server_stdout.log 2>nul
git rm --cached --quiet jarvis_core.db jarvis_episodic.sqlite jarvis_thoughts.sqlite jarvis_intent_cache.sqlite 2>nul

echo === 3. Оновлений .gitignore у коміт ===
git add .gitignore

echo === 4. Залишок відстежуваних файлів ===
for /f %%i in ('git ls-files ^| find /c /v ""') do echo Tracked files: %%i

echo === 5. Коміт ===
git commit -m "chore: untrack accidental .venvс, rotated logs, profile dumps; tighten .gitignore"

echo.
echo Готово. Перевірте:  git status
echo.
echo (Опційно) .venvс лишається в ІСТОРІЇ комітів і роздуває репозиторій.
echo Щоб прибрати її з усієї історії (узгодьте з усіма, хто має клони):
echo     pip install git-filter-repo
echo     git filter-repo --invert-paths --path ".venvс/"
pause
