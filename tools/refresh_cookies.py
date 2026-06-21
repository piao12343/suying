"""
Douyin Cookie refresh tool
Usage: python tools/refresh_cookies.py
"""

import sys
import json
from pathlib import Path

# social-auto-upload path
SAU_DIR = input('请输入 social-auto-upload 目录路径 (回车使用默认 D:\\Personal\\Desktop\\social-auto-upload): ').strip()
if not SAU_DIR:
    SAU_DIR = r'D:\Personal\Desktop\social-auto-upload'

sys.path.insert(0, SAU_DIR)

# Ensure log dir exists
(Path(SAU_DIR) / 'logs').mkdir(parents=True, exist_ok=True)


def main():
    print('=' * 50)
    print(' 速影 - 抖音 Cookie 刷新工具')
    print('=' * 50)
    print()
    print('即将打开浏览器, 请用抖音 APP 扫描二维码登录。')
    print('登录成功后, cookie 会自动保存到文件并输出到屏幕。')
    print()

    try:
        from uploader.douyin_uploader.main import douyin_setup
    except ImportError as e:
        print(f'导入失败: {e}')
        print(f'请确认 social-auto-upload 目录正确: {SAU_DIR}')
        sys.exit(1)

    import asyncio

    cookie_file = str(Path(__file__).resolve().parent.parent / 'config' / 'cookies' / 'douyin_creator.json')

    async def _login():
        result = await douyin_setup(
            cookie_file,
            handle=True,
            return_detail=True,
            headless=False,
        )
        return result

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_login())
    finally:
        loop.close()

    if isinstance(result, dict) and result.get('success'):
        print()
        print('登录成功! Cookie 已保存。')
        print()

        # Read cookie file for copying to GitHub Secrets
        cookie_path = Path(cookie_file)
        if cookie_path.exists():
            cookie_json = cookie_path.read_text(encoding='utf-8')
            print('=' * 50)
            print('以下是 Cookie JSON, 请复制到 GitHub Secrets:')
            print('Settings → Secrets → DOUYIN_COOKIES_JSON')
            print('=' * 50)
            print(cookie_json)
            print('=' * 50)
        else:
            print(f'Cookie 文件未找到: {cookie_file}')
    else:
        print(f'登录失败: {result}')
        sys.exit(1)


if __name__ == '__main__':
    main()
