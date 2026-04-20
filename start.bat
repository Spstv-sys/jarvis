@echo off
SET PROJECT=%~dp0
:: Remove trailing backslash
IF "%PROJECT:~-1%"=="\" SET PROJECT=%PROJECT:~0,-1%

echo [1/3] Запуск Docker (Ollama + ChromaDB)...
cd /d "%PROJECT%"
docker-compose up -d

echo [2/3] Запуск Python бекенду...
start "" /min cmd /c "cd /d "%PROJECT%" && .venv\Scripts\activate && python server.py"

echo [3/3] Запуск Vite + Electron UI...
start "" /min cmd /c "cd /d "%PROJECT%\ui\web" && npx vite"

echo Очікування запуску серверів (6 сек)...
timeout /t 6 /nobreak >nul

cd /d "%PROJECT%\ui\web"
set ELECTRON_RUN_AS_NODE=
node_modules\electron\dist\electron.exe electron.cjs
