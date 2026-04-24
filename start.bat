@echo off
setlocal EnableDelayedExpansion
SET PROJECT=%~dp0
:: Remove trailing backslash
IF "%PROJECT:~-1%"=="\" SET PROJECT=%PROJECT:~0,-1%
cd /d "%PROJECT%"

:: --- [1/3] Docker (пропустити, якщо контейнери вже підняті) ---
echo [1/3] Docker (Ollama + ChromaDB)...
set OLLAMA_UP=0
set CHROMA_UP=0
for /f %%i in ('docker ps -q --filter "name=ollama" 2^>nul ^| find /c /v ""') do set OLLAMA_UP=%%i
for /f %%i in ('docker ps -q --filter "name=chroma" 2^>nul ^| find /c /v ""') do set CHROMA_UP=%%i
if "!OLLAMA_UP!"=="0" goto :do_compose
if "!CHROMA_UP!"=="0" goto :do_compose
echo     Контейнери вже працюють - пропускаю docker-compose.
goto :after_compose
:do_compose
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -WindowStyle Hidden -FilePath cmd.exe -ArgumentList '/c','docker-compose up -d'"
:after_compose

:: --- [2/3] Python backend (прихована консоль через python.exe) ---
echo [2/3] Python backend...
set "PY_EXE=%PROJECT%\.venv\Scripts\python.exe"
powershell -NoProfile -WindowStyle Hidden -Command "$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'; Start-Process -WindowStyle Hidden -WorkingDirectory '%PROJECT%' -FilePath '%PY_EXE%' -ArgumentList 'server.py'"

:: --- [3/3] Vite UI ---
echo [3/3] Vite + Electron UI...
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -WindowStyle Hidden -WorkingDirectory '%PROJECT%\ui\web' -FilePath cmd.exe -ArgumentList '/c','npx vite'"

:: --- Розумне очікування Vite на :5173 (замість фіксованого timeout 6) ---
echo Очікування Vite (:5173)...
set /a TRIES=0
:wait_vite
set /a TRIES+=1
curl.exe -s --max-time 1 -o NUL http://localhost:5173 >nul 2>&1
if !ERRORLEVEL! EQU 0 goto :vite_ready
if !TRIES! GEQ 60 goto :vite_ready
ping -n 2 127.0.0.1 >nul
goto :wait_vite
:vite_ready

:: --- Electron ---
cd /d "%PROJECT%\ui\web"
set ELECTRON_RUN_AS_NODE=
node_modules\electron\dist\electron.exe electron.cjs

endlocal
