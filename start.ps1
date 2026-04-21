# J.A.R.V.I.S. Launch Script
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$projectPath = "D:\Projects\Jv3\jarvis_project"
Set-Location $projectPath

# 0. Перевірка та запуск Docker Desktop
$dockerRunning = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
if (-not $dockerRunning) {
    Write-Host "🐳 Запуск Docker Desktop..." -ForegroundColor Yellow
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

    Write-Host "Очікування Docker Engine..." -ForegroundColor Yellow
    while ($true) {
        docker info 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "✅ Docker Engine готовий!" -ForegroundColor Green
            break
        }
        Start-Sleep -Seconds 3
    }
} else {
    Write-Host "✅ Docker Desktop вже запущений." -ForegroundColor Green
}

# 1. Docker (Ollama + ChromaDB)
Write-Host "[1/4] Запуск Docker (Ollama + ChromaDB)..." -ForegroundColor Cyan
docker-compose up -d

# Очікування ChromaDB (:8000)
Write-Host "Очікування ChromaDB на :8000..." -ForegroundColor Yellow
while ($true) {
    try {
        Invoke-WebRequest -Uri "http://localhost:8000/api/v1/heartbeat" -TimeoutSec 1 -ErrorAction Stop | Out-Null
        Write-Host "✅ ChromaDB готовий!" -ForegroundColor Green
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}

# Очікування Ollama (:11434)
Write-Host "Очікування Ollama на :11434..." -ForegroundColor Yellow
while ($true) {
    try {
        Invoke-WebRequest -Uri "http://localhost:11434" -TimeoutSec 1 -ErrorAction Stop | Out-Null
        Write-Host "✅ Ollama готовий!" -ForegroundColor Green
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}

# 2. Python backend
Write-Host "[2/4] Запуск Python бекенду..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-Command", `
    "Set-Location '$projectPath'; .venv\Scripts\activate; python server.py"
    # -WindowStyle Hidden  # розкоментуйте коли все стабільно

# Очікування Python бекенду (:8001)
Write-Host "Очікування Python бекенду на :8001..." -ForegroundColor Yellow
while ($true) {
    try {
        Invoke-WebRequest -Uri "http://localhost:8001/status" -TimeoutSec 1 -ErrorAction Stop | Out-Null
        Write-Host "✅ Python бекенд готовий!" -ForegroundColor Green
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}

# 3. Vite dev server
Write-Host "[3/4] Запуск Vite..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-Command", `
    "Set-Location '$projectPath\ui\web'; npx vite"
    # -WindowStyle Hidden  # розкоментуйте коли все стабільно

# Очікування Vite (:5173)
Write-Host "Очікування Vite на :5173..." -ForegroundColor Yellow
while ($true) {
    try {
        Invoke-WebRequest -Uri "http://localhost:5173" -TimeoutSec 1 -ErrorAction Stop | Out-Null
        Write-Host "✅ Vite готовий!" -ForegroundColor Green
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}

# 4. Electron
Write-Host "[4/4] Запуск Electron..." -ForegroundColor Cyan
Set-Location "$projectPath\ui\web"
$env:ELECTRON_RUN_AS_NODE = $null
& ".\node_modules\electron\dist\electron.exe" "electron.cjs"
