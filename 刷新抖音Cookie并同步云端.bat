@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo  速影 - 刷新抖音 Cookie 并同步云端
echo ================================================
echo.
python "源码\tools\refresh_cookies.py"
echo.
pause
