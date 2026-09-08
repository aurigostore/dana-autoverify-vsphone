@echo off
REM Jalankan bot auto-verifikasi DANA. Tutup jendela ini = bot berhenti.
REM QuickEdit dimatikan lewat 'cmd /q' bukan; pakai: klik-kanan judul > Properties > hilangkan QuickEdit Mode.
cd /d "%~dp0"
:loop
echo [run.bat] start watch.py  %date% %time%
python src\watch.py
echo [run.bat] watch.py keluar (rc=%errorlevel%). Restart 10 dtk lagi... Ctrl+C untuk stop.
timeout /t 10 /nobreak >nul
goto loop
