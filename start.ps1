# J.A.R.V.I.S. Launch Script
$projectPath = "D:\Projects\Jv3\jarvis_project"

Set-Location $projectPath

# 1. Docker (Ollama + ChromaDB)
Write-Host "[1/3] Запуск Docker (Ollama + ChromaDB)..." -ForegroundColor Cyan
docker-compose up -d

# 2. Python backend
Write-Host "[2/3] Запуск Python бекенду..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", `
    "Set-Location '$projectPath'; .venv\Scripts\activate; python server.py"

# 3. Vite dev server
Write-Host "[3/3] Запуск Vite + Electron UI..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", `
    "Set-Location '$projectPath\ui\web'; npx vite"

# Чекаємо поки Vite та Python запустяться
Write-Host "Очікування запуску серверів (6 сек)..." -ForegroundColor Yellow
Start-Sleep -Seconds 6

# Запуск Electron через точний шлях до electron.exe
Set-Location "$projectPath\ui\web"
$env:ELECTRON_RUN_AS_NODE = $null
& ".\node_modules\electron\dist\electron.exe" "electron.cjs"
