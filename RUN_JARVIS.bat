@echo off
TITLE J.A.R.V.I.S. SYSTEM BOOT
COLOR 0A
cls

echo ======================================================
echo           INITIALIZING J.A.R.V.I.S. CORE
echo ======================================================
echo.
echo [SYSTEM]: Environment check...

:: Перевірка чи існує віртуальне середовище
if not exist .venv (
    echo [ERROR]: Віртуальне середовище .venv не знайдене!
    echo [SYSTEM]: Спробуйте запустити 'python -m venv .venv'
    pause
    exit
)

echo [SYSTEM]: Activating communication protocols...
call .venv\Scripts\activate

echo [SYSTEM]: Loading the graphical interface HUD...
echo.
echo ------------------------------------------------------
echo      SYSTEM IS READY. STARTING MAIN INTERFACE...
echo ------------------------------------------------------

:: Запуск через файл інтерфейсу
python ui/app_window.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [CRITICAL ERROR]: Системний збій під час виконання.
    pause
)

echo [SYSTEM]: The protocol is complete. Shutdown.
pause