# J.A.R.V.I.S. Launch Script
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$projectPath = "D:\Projects\Jv3\jarvis_project"
$venvPython = "$projectPath\.venv\Scripts\python.exe"
Set-Location $projectPath

function Test-ReadableFile {
    param([string]$Path)
    try {
        if (-not (Test-Path $Path)) { return $false }
        Get-Content -Path $Path -TotalCount 1 -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Get-HttpStatusCode {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSec = 2
    )
    try {
        $code = curl.exe -s --max-time $TimeoutSec -o NUL -w "%{http_code}" $Url 2>$null
        if (-not $code) { return "000" }
        return $code.Trim()
    } catch {
        return "000"
    }
}

function Stop-JarvisBackendProcesses {
    param(
        [Parameter(Mandatory = $true)][string]$VenvPython,
        [int]$Port = 8001
    )

    $processIds = New-Object System.Collections.Generic.HashSet[int]

    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($listenerPid in @($listener)) {
        [void]$processIds.Add([int]$listenerPid)
    }

    $backendProcesses = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and $_.CommandLine -match 'server\.py'
        }
    foreach ($process in @($backendProcesses)) {
        [void]$processIds.Add([int]$process.ProcessId)
    }

    foreach ($processId in $processIds) {
        if ($processId -eq $PID) { continue }

        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
        if (-not $process -or -not $process.CommandLine -or $process.CommandLine -notmatch 'server\.py') {
            continue
        }

        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
            Write-Host "Stopped existing backend process PID $processId" -ForegroundColor Yellow
        } catch {
            Write-Host "Could not stop backend process PID ${processId}: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

# 0. Check and start Docker Desktop
$dockerRunning = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
if (-not $dockerRunning) {
    Write-Host "Starting Docker Desktop..." -ForegroundColor Yellow
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

    Write-Host "Waiting for Docker Engine..." -ForegroundColor Yellow
    while ($true) {
        docker info 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "✅ Docker Engine ready!" -ForegroundColor Green
            break
        }
        Start-Sleep -Seconds 3
    }
} else {
    Write-Host "✅ Docker Desktop already running." -ForegroundColor Green
}

# 1. Docker (Ollama + ChromaDB)
Write-Host "[1/4] Starting Docker (Ollama + ChromaDB)..." -ForegroundColor Cyan
docker-compose up -d

# Wait for ChromaDB (:8000)
Write-Host "Waiting for ChromaDB on :8000..." -ForegroundColor Yellow
$chromaMaxRetries = 15
$chromaRetries = 0
$chromaEndpointPrimary = "http://localhost:8000/api/v2/heartbeat"
$chromaEndpointFallback = "http://localhost:8000/api/v1/heartbeat"
while ($chromaRetries -lt $chromaMaxRetries) {
    $statusPrimary = Get-HttpStatusCode -Url $chromaEndpointPrimary
    $statusFallback = Get-HttpStatusCode -Url $chromaEndpointFallback
    if ($statusPrimary -eq "200" -or $statusFallback -eq "200") {
        Write-Host "✅ ChromaDB ready!" -ForegroundColor Green
        break
    } else {
        $chromaRetries++
        if ($chromaRetries -lt $chromaMaxRetries) {
            Write-Host "  Attempt $chromaRetries/$chromaMaxRetries (v2=$statusPrimary, v1=$statusFallback)..." -ForegroundColor Gray
            Start-Sleep -Seconds 1
        }
    }
}
if ($chromaRetries -eq $chromaMaxRetries) {
    Write-Host "ChromaDB not responding. Continuing..." -ForegroundColor Yellow
}

# Wait for Ollama (configurable host port)
${ollamaPort} = if ($env:OLLAMA_PORT) { $env:OLLAMA_PORT } else { "11534" }
$ollamaHealthUrl = "http://localhost:$ollamaPort/api/tags"
Write-Host "Waiting for Ollama on :$ollamaPort..." -ForegroundColor Yellow
$ollamaMaxRetries = 15
$ollamaRetries = 0
while ($ollamaRetries -lt $ollamaMaxRetries) {
    $status = Get-HttpStatusCode -Url $ollamaHealthUrl
    if ($status -eq "200") {
        Write-Host "✅ Ollama ready!" -ForegroundColor Green
        break
    } else {
        $ollamaRetries++
        if ($ollamaRetries -lt $ollamaMaxRetries) {
            Write-Host "  Attempt $ollamaRetries/$ollamaMaxRetries (status=$status)..." -ForegroundColor Gray
            Start-Sleep -Seconds 1
        }
    }
}
if ($ollamaRetries -eq $ollamaMaxRetries) {
    Write-Host "Ollama not responding. Continuing..." -ForegroundColor Yellow
}

# 2. Python backend
Write-Host "[2/4] Starting Python backend..." -ForegroundColor Cyan
if (-not (Test-Path $venvPython)) {
    Write-Host "Python venv not found: $venvPython" -ForegroundColor Red
    exit 1
}
Stop-JarvisBackendProcesses -VenvPython $venvPython -Port 8001
Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", `
    "Set-Location '$projectPath'; `$env:PYTHONIOENCODING='utf-8'; `$env:PYTHONUTF8='1'; & '$venvPython' server.py" `
    -WindowStyle Hidden

# 3. Vite dev server
Write-Host "[3/4] Starting Vite..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-Command", `
    "Set-Location '$projectPath\ui\web'; npx vite" `
    #-WindowStyle Hidden

# Wait for servers to start
Write-Host "Waiting for servers to start (6 sec)..." -ForegroundColor Yellow
Start-Sleep -Seconds 6

# 4. Electron
Write-Host "[4/4] Starting Electron..." -ForegroundColor Cyan
$electronCJS = "$projectPath\ui\web\electron.cjs"
$packageJson = "$projectPath\ui\web\package.json"
Set-Location "$projectPath\ui\web"

if ((Test-ReadableFile $electronCJS) -and (Test-ReadableFile $packageJson)) {
    # If this variable is set, Electron runs in Node mode and app.whenReady is unavailable.
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    npx electron .
} else {
    Write-Host "Electron files are not readable (disk I/O issue). Opening web UI in browser..." -ForegroundColor Yellow
    Start-Process "http://localhost:5173"
}
