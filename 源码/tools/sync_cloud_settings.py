"""
Sync local Suying settings to GitHub Secrets for cloud runs.

Usage:
  python 源码/tools/sync_cloud_settings.py --pub-desc --publish-interval
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


NW = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CFG_DIR = PROJECT_ROOT / '配置'
CONFIG_PATH = CFG_DIR / 'config.json'
TEMPLATE_PATH = CFG_DIR / 'ai生故事模板.txt'
COOKIE_PATH = CFG_DIR / 'cookies' / 'douyin_creator.json'
GITHUB_REPO = 'piao12343/suying'
SECRET_LABELS = {
    'SUYING_PUB_DESC': '发布话题',
    'SUYING_AUTO_PUBLISH': '自动发布固定开启',
    'SUYING_PUBLISH_INTERVAL_MINUTES': '定时发布间隔',
    'SUYING_REWRITE_TEMPLATE_TEXT': 'AI改写模板',
    'DOUYIN_COOKIES_JSON': '抖音 Cookie',
}


def load_config():
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f'配置文件不存在: {CONFIG_PATH}')
    return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))


def require_gh():
    gh = shutil.which('gh')
    if not gh:
        raise RuntimeError('未找到 gh 命令, 请先安装并登录 GitHub CLI。')
    return gh


def set_secret(gh, name, value):
    if isinstance(value, str):
        data = value.encode('utf-8')
    else:
        data = value
    result = subprocess.run(
        [gh, 'secret', 'set', name, '-R', GITHUB_REPO],
        input=data,
        **NW,
    )
    if result.returncode != 0:
        raise RuntimeError(f'同步失败: {SECRET_LABELS.get(name, name)}')
    print(f'已同步：{SECRET_LABELS.get(name, name)}')


def read_cookie_bytes():
    if not COOKIE_PATH.exists() or COOKIE_PATH.stat().st_size < 50:
        raise RuntimeError(f'Cookie 文件不存在或内容过小: {COOKIE_PATH}')
    try:
        json.loads(COOKIE_PATH.read_text(encoding='utf-8-sig'))
    except Exception as e:
        raise RuntimeError(f'Cookie JSON 格式异常: {e}')
    return COOKIE_PATH.read_bytes()


def read_template_text():
    if not TEMPLATE_PATH.exists():
        raise RuntimeError(f'AI 改写模板不存在: {TEMPLATE_PATH}')
    text = TEMPLATE_PATH.read_text(encoding='utf-8-sig').strip()
    if not text:
        raise RuntimeError('AI 改写模板为空, 已取消同步。')
    return text


def main():
    parser = argparse.ArgumentParser(description='同步本地配置到云端 GitHub Secrets')
    parser.add_argument('--pub-desc', action='store_true', help='同步发布话题')
    parser.add_argument('--auto-publish', action='store_true', help='同步自动发布开关')
    parser.add_argument('--publish-interval', action='store_true', help='同步发布间隔')
    parser.add_argument('--rewrite-template', action='store_true', help='同步 AI 改写模板全文')
    parser.add_argument('--cookie', action='store_true', help='同步本地已保存的抖音 Cookie')
    args = parser.parse_args()

    selected = [
        args.pub_desc,
        args.auto_publish,
        args.publish_interval,
        args.rewrite_template,
        args.cookie,
    ]
    if not any(selected):
        parser.error('请至少选择一项要同步的配置。')

    gh = require_gh()
    config = load_config()

    if args.pub_desc:
        set_secret(gh, 'SUYING_PUB_DESC', config.get('pub_desc', ''))

    if args.auto_publish:
        value = 'true' if config.get('auto_publish_douyin', False) else 'false'
        set_secret(gh, 'SUYING_AUTO_PUBLISH', value)

    if args.publish_interval:
        value = str(int(config.get('publish_interval_minutes', 120)))
        set_secret(gh, 'SUYING_PUBLISH_INTERVAL_MINUTES', value)

    if args.rewrite_template:
        set_secret(gh, 'SUYING_REWRITE_TEMPLATE_TEXT', read_template_text())

    if args.cookie:
        set_secret(gh, 'DOUYIN_COOKIES_JSON', read_cookie_bytes())

    print('云端同步完成。')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'错误: {e}', file=sys.stderr)
        sys.exit(1)
