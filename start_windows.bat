@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 bootstrap.py %*
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python was not found. Install Python 3.11+ and enable Add python.exe to PATH.
        pause
        exit /b 1
    )
    python bootstrap.py %*
)
if errorlevel 1 (
    echo.
    echo Startup failed. Please read the error above and README.md.
    pause
)
endlocal
