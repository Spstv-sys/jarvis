@echo off
SET PROJECT=%~dp0
IF "%PROJECT:~-1%"=="\" SET PROJECT=%PROJECT:~0,-1%

echo [1/3] Запуск Docker (Ollama + ChromaDB)...
cd /d "%PROJECT%"
docker-compose up -d

echo [2/3] Запуск Python бекенду...
start "" /min cmd /c "cd /d "%PROJECT%" && .venv\Scripts\activate && python server.py"

echo [3/3] Запуск Vite...
start "" /min cmd /c "cd /d "%PROJECT%\ui\web" && npx vite"

echo Очікування Vite на http://localhost:5173...
:waitvite
timeout /t 2 /nobreak >nul
curl -s http://localhost:5173 >nul 2>&1
if errorlevel 1 goto waitvite

echo Vite готовий, запускаємо Electron...
cd /d "%PROJECT%\ui\web"
set ELECTRON_RUN_AS_NODE=
node_modules\electron\dist\electron.exe electron.cjs
