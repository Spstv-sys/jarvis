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

# 3. Electron
Write-Host "[3/3] Запуск Electron UI..." -ForegroundColor Cyan
Set-Location "$projectPath\ui\web"
npm run electron:dev
