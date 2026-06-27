"""
Douyin Cookie refresh tool
Usage: python 源码/tools/refresh_cookies.py
"""

import sys
import json
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SAU_DIR = PROJECT_ROOT / '配置' / 'social-auto-upload'
FALLBACK_SAU_DIR = Path(r'D:\Personal\Desktop\social-auto-upload')
COOKIE_PATH = PROJECT_ROOT / '配置' / 'cookies' / 'douyin_creator.json'
GITHUB_REPO = 'piao12343/suying'
ACCOUNT_NAME = 'creator'


def pick_sau_dir():
    default_dir = DEFAULT_SAU_DIR if DEFAULT_SAU_DIR.exists() else FALLBACK_SAU_DIR
    raw = input(f'请输入 social-auto-upload 目录路径 (回车使用默认 {default_dir}): ').strip()
    sau_dir = Path(raw) if raw else default_dir
    if not sau_dir.exists():
        print(f'social-auto-upload 目录不存在: {sau_dir}')
        sys.exit(1)
    return sau_dir


SAU_DIR = pick_sau_dir()

sys.path.insert(0, str(SAU_DIR))

# Ensure log dir exists
(Path(SAU_DIR) / 'logs').mkdir(parents=True, exist_ok=True)


def sync_cookie_to_github(cookie_path):
    if not cookie_path.exists() or cookie_path.stat().st_size < 50:
        print(f'Cookie 文件不存在或内容过小: {cookie_path}')
        return False

    try:
        json.loads(cookie_path.read_text(encoding='utf-8-sig'))
    except Exception as e:
        print(f'Cookie JSON 格式异常: {e}')
        return False

    gh = shutil.which('gh')
    if not gh:
        print('未找到 gh 命令, 请先安装并登录 GitHub CLI。')
        return False

    print('正在同步 Cookie 到 GitHub Secret: DOUYIN_COOKIES_JSON ...')
    with cookie_path.open('rb') as f:
        result = subprocess.run(
            [gh, 'secret', 'set', 'DOUYIN_COOKIES_JSON', '-R', GITHUB_REPO],
            stdin=f,
        )

    if result.returncode != 0:
        print('GitHub Secret 同步失败。请确认 gh 已登录且有仓库权限。')
        return False

    print('GitHub Secret 同步完成。')
    return True


def sau_cookie_path():
    return SAU_DIR / 'cookies' / f'douyin_{ACCOUNT_NAME}.json'


def run_sau_command(args, timeout=None):
    sau_cli = SAU_DIR / 'sau_cli.py'
    if not sau_cli.exists():
        return None
    return subprocess.run(
        [sys.executable, str(sau_cli), *args],
        cwd=str(SAU_DIR),
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
    )


def refresh_with_sau_cli():
    sau_cli = SAU_DIR / 'sau_cli.py'
    if not sau_cli.exists():
        return False

    account_cookie = sau_cookie_path()
    account_cookie.parent.mkdir(parents=True, exist_ok=True)

    if COOKIE_PATH.exists() and not account_cookie.exists():
        shutil.copyfile(COOKIE_PATH, account_cookie)

    print(f'使用 social-auto-upload CLI 刷新账号: {ACCOUNT_NAME}')
    print('即将打开浏览器, 请完成抖音扫码登录。')
    login_result = run_sau_command(
        ['douyin', 'login', '--account', ACCOUNT_NAME, '--headed']
    )
    if login_result is None or login_result.returncode != 0:
        print('social-auto-upload CLI 登录失败。')
        return False

    check_result = run_sau_command(
        ['douyin', 'check', '--account', ACCOUNT_NAME],
        timeout=180,
    )
    if check_result is None or check_result.returncode != 0:
        print('登录后 Cookie 校验未通过。')
        return False

    if not account_cookie.exists() or account_cookie.stat().st_size < 50:
        print(f'账号 Cookie 文件不存在或内容过小: {account_cookie}')
        return False

    COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(account_cookie, COOKIE_PATH)
    return True


def main():
    print('=' * 50)
    print(' 速影 - 抖音 Cookie 刷新工具')
    print('=' * 50)
    print()
    print('即将打开浏览器, 请用抖音 APP 扫描二维码登录。')
    print('登录成功后, cookie 会自动保存到本地并同步到云端 GitHub Secret。')
    print()

    cookie_file = str(COOKIE_PATH)

    if refresh_with_sau_cli():
        print()
        print('登录成功! Cookie 已保存。')
        print()

        # Read cookie file for copying to GitHub Secrets
        if sync_cookie_to_github(Path(cookie_file)):
            print()
            print('全部完成: 本地 Cookie 已刷新, 云端 Secret 已同步。')
        else:
            print()
            print('本地 Cookie 已保存, 但云端同步失败。')
            sys.exit(1)
    else:
        print('登录失败: social-auto-upload CLI 未能生成有效 Cookie')
        sys.exit(1)


if __name__ == '__main__':
    main()
