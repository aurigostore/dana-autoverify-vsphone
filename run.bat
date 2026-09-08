@echo off
REM Auto-restart bot tanpa menu (buat ditinggal jalan). Tutup jendela = berhenti.
cd /d "%~dp0"
:loop
python src\watch.py --no-menu
echo [run.bat] watch keluar (rc=%errorlevel%). Restart 10 dtk lagi... Ctrl+C untuk stop.
timeout /t 10 /nobreak >nul
goto loop
