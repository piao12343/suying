"""
Sync local Suying settings to GitHub Secrets for cloud runs.

Usage:
  python 源码/tools/sync_cloud_settings.py --all
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
CONFIG_TEMPLATE_PATH = CFG_DIR / 'config_template.json'
TEMPLATE_PATH = CFG_DIR / 'ai生故事模板.txt'
COOKIE_PATH = CFG_DIR / 'cookies' / 'douyin_creator.json'
GITHUB_REPO = 'piao12343/suying'
SECRET_LABELS = {
    'SUYING_OPENROUTER_API_KEY': 'OpenRouter API Key',
    'SUYING_PEXELS_API_KEY': 'Pexels API Key',
    'SUYING_LISTENER_SECRET': 'Worker 密钥',
    'SUYING_LISTENER_WORKER_URL': 'Worker 地址',
    'SUYING_PUSHPLUS_TOKEN': 'PushPlus Token',
    'SUYING_TTS_VOICE': 'TTS 语音',
    'SUYING_TTS_RATE': 'TTS 语速',
    'SUYING_OPENROUTER_MODEL': 'AI 模型',
    'SUYING_OPENROUTER_BASE_URL': 'AI 接口地址',
    'SUYING_OPENROUTER_FALLBACK_MODELS': 'AI 备用模型',
    'SUYING_PUB_DESC': '发布话题',
    'SUYING_AUTO_PUBLISH': '自动发布固定开启',
    'SUYING_PUBLISH_INTERVAL_MINUTES': '定时发布间隔',
    'SUYING_PUBLISH_TIMEOUT_SECONDS': '发布超时',
    'SUYING_REWRITE_TEMPLATE_TEXT': 'AI改写模板',
    'DOUYIN_COOKIES_JSON': '抖音 Cookie',
}


def load_config():
    config = {}
    if CONFIG_TEMPLATE_PATH.exists():
        config.update(json.loads(CONFIG_TEMPLATE_PATH.read_text(encoding='utf-8')))
    if CONFIG_PATH.exists():
        config.update(json.loads(CONFIG_PATH.read_text(encoding='utf-8')))
    if not config:
        raise FileNotFoundError(f'配置文件不存在: {CONFIG_PATH}')
    return config


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


def verify_secret_names(gh, expected_names):
    result = subprocess.run(
        [gh, 'secret', 'list', '-R', GITHUB_REPO, '--json', 'name,updatedAt'],
        capture_output=True,
        text=True,
        encoding='utf-8',
        **NW,
    )
    if result.returncode != 0:
        print('同步检查跳过：无法读取 GitHub Secret 列表。')
        return
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        print('同步检查跳过：GitHub Secret 列表解析失败。')
        return

    existing = {row.get('name') for row in rows}
    missing = [name for name in expected_names if name not in existing]
    if missing:
        labels = '、'.join(SECRET_LABELS.get(name, name) for name in missing)
        raise RuntimeError(f'同步检查失败，云端缺少: {labels}')

    print(f'同步检查通过：云端已存在 {len(expected_names)} 项配置。')


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


def text_value(config, key):
    value = config.get(key, '')
    if value is None:
        return ''
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def main():
    parser = argparse.ArgumentParser(description='同步本地配置到云端 GitHub Secrets')
    parser.add_argument('--all', action='store_true', help='同步云端运行需要的全部本地配置（不含 Cookie）')
    parser.add_argument('--openrouter-key', action='store_true', help='同步 OpenRouter API Key')
    parser.add_argument('--pexels-key', action='store_true', help='同步 Pexels API Key')
    parser.add_argument('--listener', action='store_true', help='同步 Worker 地址和密钥')
    parser.add_argument('--pushplus', action='store_true', help='同步 PushPlus Token')
    parser.add_argument('--tts', action='store_true', help='同步 TTS 语音和语速')
    parser.add_argument('--openrouter-model', action='store_true', help='同步 AI 模型、接口地址和备用模型')
    parser.add_argument('--pub-desc', action='store_true', help='同步发布话题')
    parser.add_argument('--auto-publish', action='store_true', help='同步自动发布开关')
    parser.add_argument('--publish-interval', action='store_true', help='同步发布间隔')
    parser.add_argument('--publish-timeout', action='store_true', help='同步发布超时时间')
    parser.add_argument('--rewrite-template', action='store_true', help='同步 AI 改写模板全文')
    parser.add_argument('--cookie', action='store_true', help='同步本地已保存的抖音 Cookie')
    args = parser.parse_args()

    if args.all:
        args.openrouter_key = True
        args.pexels_key = True
        args.listener = True
        args.pushplus = True
        args.tts = True
        args.openrouter_model = True
        args.pub_desc = True
        args.auto_publish = True
        args.publish_interval = True
        args.publish_timeout = True
        args.rewrite_template = True

    selected = [
        args.openrouter_key,
        args.pexels_key,
        args.listener,
        args.pushplus,
        args.tts,
        args.openrouter_model,
        args.pub_desc,
        args.auto_publish,
        args.publish_interval,
        args.publish_timeout,
        args.rewrite_template,
        args.cookie,
    ]
    if not any(selected):
        parser.error('请至少选择一项要同步的配置。')

    gh = require_gh()
    config = load_config()
    synced_names = []

    def sync_secret(name, value):
        set_secret(gh, name, value)
        synced_names.append(name)

    if args.openrouter_key:
        sync_secret('SUYING_OPENROUTER_API_KEY', text_value(config, 'openrouter_api_key'))

    if args.pexels_key:
        sync_secret('SUYING_PEXELS_API_KEY', text_value(config, 'pexels_api_key'))

    if args.listener:
        sync_secret('SUYING_LISTENER_SECRET', text_value(config, 'listener_secret'))
        sync_secret('SUYING_LISTENER_WORKER_URL', text_value(config, 'listener_worker_url'))

    if args.pushplus:
        sync_secret('SUYING_PUSHPLUS_TOKEN', text_value(config, 'pushplus_token'))

    if args.tts:
        sync_secret('SUYING_TTS_VOICE', text_value(config, 'tts_voice'))
        sync_secret('SUYING_TTS_RATE', text_value(config, 'tts_rate'))

    if args.openrouter_model:
        sync_secret('SUYING_OPENROUTER_MODEL', text_value(config, 'openrouter_model'))
        sync_secret('SUYING_OPENROUTER_BASE_URL', text_value(config, 'openrouter_base_url'))
        sync_secret('SUYING_OPENROUTER_FALLBACK_MODELS', text_value(config, 'openrouter_fallback_models'))

    if args.pub_desc:
        sync_secret('SUYING_PUB_DESC', text_value(config, 'pub_desc'))

    if args.auto_publish:
        value = 'true' if config.get('auto_publish_douyin', False) else 'false'
        sync_secret('SUYING_AUTO_PUBLISH', value)

    if args.publish_interval:
        value = str(int(config.get('publish_interval_minutes', 120)))
        sync_secret('SUYING_PUBLISH_INTERVAL_MINUTES', value)

    if args.publish_timeout:
        value = str(int(config.get('publish_timeout_seconds', 1200)))
        sync_secret('SUYING_PUBLISH_TIMEOUT_SECONDS', value)

    if args.rewrite_template:
        sync_secret('SUYING_REWRITE_TEMPLATE_TEXT', read_template_text())

    if args.cookie:
        sync_secret('DOUYIN_COOKIES_JSON', read_cookie_bytes())

    verify_secret_names(gh, synced_names)
    print('云端同步完成。')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'错误: {e}', file=sys.stderr)
        sys.exit(1)
