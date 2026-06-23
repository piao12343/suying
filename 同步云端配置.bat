@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  速影 - 同步云端配置
echo ========================================
echo.
echo 将同步: 发布话题、自动发布开关、发布间隔、文案改写自定义指令
echo 如需同步 AI 模板或 Cookie, 请在本地客户端的“同步云端配置”窗口勾选。
echo.

python "源码\tools\sync_cloud_settings.py" --pub-desc --auto-publish --publish-interval --rewrite-instruction

echo.
pause
