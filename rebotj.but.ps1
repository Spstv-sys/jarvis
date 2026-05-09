# 1. Вбити всі процеси backend
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -match 'server\.py' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 2. Вбити Electron/Node (UI)
Get-Process "electron","node" -ErrorAction SilentlyContinue | Stop-Process -Force

# 3. Запустити заново
cd D:\Projects\Jv3\jarvis_project
.\start.ps1