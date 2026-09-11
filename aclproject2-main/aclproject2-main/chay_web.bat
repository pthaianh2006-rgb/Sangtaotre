@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."
echo ============================================
echo   Dang khoi dong web phuc hoi chuc nang...
echo   Cho den khi thay dong "Running on http://127.0.0.1:5000"
echo   roi mo trinh duyet vao: http://127.0.0.1:5000
echo ============================================
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" web.py %*
) else (
    python web.py %*
)
pause
