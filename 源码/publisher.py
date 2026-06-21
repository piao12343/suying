"""
Video publishing module (Douyin etc.)
"""

import os
import sys
import json
import asyncio
import io
from pathlib import Path
from datetime import datetime

# ============ Env Patch ============
# tkinter GUI may leave sys.stdout/stderr as None,
# causing loguru errors. Fix before importing social-auto-upload.
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

# social-auto-upload path: env var > repo sibling dir
SAU_DIR = Path(os.environ.get('SAU_DIR', str(Path(__file__).resolve().parent.parent / '配置' / 'social-auto-upload')))
if str(SAU_DIR) not in sys.path:
    sys.path.insert(0, str(SAU_DIR))

# Ensure log dir exists (needed by social-auto-upload loguru)
(SAU_DIR / 'logs').mkdir(parents=True, exist_ok=True)

# Cookie storage dir
COOKIE_DIR = Path(__file__).resolve().parent.parent / '配置' / 'cookies'
COOKIE_DIR.mkdir(parents=True, exist_ok=True)
DOUYIN_COOKIE_FILE = COOKIE_DIR / 'douyin_creator.json'


def check_douyin_login():
    """Check Douyin login status (sync wrapper)
    Returns:
        bool: True if logged in, False otherwise
    """
    try:
        from uploader.douyin_uploader.main import douyin_setup

        async def _check():
            result = await douyin_setup(str(DOUYIN_COOKIE_FILE), handle=False)
            return result

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_check())
        finally:
            loop.close()
    except Exception as e:
        print(f'[发布模块] 检查登录状态失败: {e}')
        return False


def login_douyin(qrcode_callback=None):
    """Execute Douyin login flow (sync wrapper)

    Args:
        qrcode_callback: QR code callback, receives dict: {'image_path': str, 'image_data_url': str}

    Returns:
        dict: {'success': bool, 'message': str, 'account_file': str}
    """
    try:
        from uploader.douyin_uploader.main import douyin_setup

        async def _login():
            result = await douyin_setup(
                str(DOUYIN_COOKIE_FILE),
                handle=True,
                return_detail=True,
                qrcode_callback=qrcode_callback,
                headless=False  # Login requires visible browser for QR scan
            )
            return result

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_login())
        finally:
            loop.close()
    except Exception as e:
        return {'success': False, 'message': f'登录失败: {e}', 'account_file': str(DOUYIN_COOKIE_FILE)}


def publish_to_douyin(video_path, title, tags=None, description=None,
                      publish_strategy='immediate', publish_date=None,
                      thumbnail_portrait_path=None, thumbnail_landscape_path=None,
                      headless=True, debug=False):
    """Publish video to Douyin (sync wrapper)

    Args:
        video_path: video file path (str or Path)
        title: video title (max 30 chars)
        tags: tag list, e.g. ['story', 'emotion']
        description: video description, defaults to title
        publish_strategy: 'immediate' or 'scheduled'
        publish_date: scheduled publish datetime, only for scheduled mode
        thumbnail_portrait_path: portrait cover image path
        thumbnail_landscape_path: landscape cover image path
        headless: run browser headless
        debug: enable debug mode

    Returns:
        dict: {'success': bool, 'message': str}
    """
    video_path = str(video_path)
    if not os.path.exists(video_path):
        return {'success': False, 'message': f'视频文件不存在: {video_path}'}

    if not DOUYIN_COOKIE_FILE.exists():
        return {'success': False, 'message': '未登录, 请先扫码登录抖音'}

    tags = tags or []
    description = description or title
    publish_date = publish_date or datetime.now()

    try:
        from uploader.douyin_uploader.main import DouYinVideo

        async def _upload():
            app = DouYinVideo(
                title=title,
                file_path=video_path,
                tags=tags,
                publish_date=publish_date,
                account_file=str(DOUYIN_COOKIE_FILE),
                thumbnail_portrait_path=thumbnail_portrait_path,
                thumbnail_landscape_path=thumbnail_landscape_path,
                desc=description,
                publish_strategy=publish_strategy,
                headless=headless,
                debug=debug,
            )
            await app.douyin_upload_video()
            return {'success': True, 'message': '发布成功'}

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_upload())
        finally:
            loop.close()
    except Exception as e:
        return {'success': False, 'message': f'发布失败: {e}'}


# ============ Extension Stubs ============

def publish_to_xiaohongshu(video_path, title, tags=None, **kwargs):
    """Publish to Xiaohongshu (stub)"""
    return {'success': False, 'message': '小红书发布功能尚未实现'}


def publish_to_kuaishou(video_path, title, tags=None, **kwargs):
    """Publish to Kuaishou (stub)"""
    return {'success': False, 'message': '快手发布功能尚未实现'}


def publish_to_bilibili(video_path, title, tags=None, **kwargs):
    """Publish to Bilibili (stub)"""
    return {'success': False, 'message': 'B站发布功能尚未实现'}


# ============ Unified Publish API ============

PLATFORMS = {
    'douyin': {'name': '抖音', 'func': publish_to_douyin},
    'xiaohongshu': {'name': '小红书', 'func': publish_to_xiaohongshu},
    'kuaishou': {'name': '快手', 'func': publish_to_kuaishou},
    'bilibili': {'name': 'B站', 'func': publish_to_bilibili},
}


def publish_video(platform, video_path, title, tags=None, **kwargs):
    """Unified publish API

    Args:
        platform: platform ID ('douyin', 'xiaohongshu', 'kuaishou', 'bilibili')
        video_path: video file path
        title: video title
        tags: tag list
        **kwargs: platform-specific params

    Returns:
        dict: {'success': bool, 'message': str, 'platform': str}
    """
    if platform not in PLATFORMS:
        return {'success': False, 'message': f'不支持的平台: {platform}', 'platform': platform}

    result = PLATFORMS[platform]['func'](video_path, title, tags, **kwargs)
    result['platform'] = platform
    return result


def get_supported_platforms():
    """Get list of supported platforms"""
    return [{'id': k, 'name': v['name']} for k, v in PLATFORMS.items()]


if __name__ == '__main__':
    # Test code
    print('支持的发布平台:')
    for p in get_supported_platforms():
        print(f"  - {p['name']} ({p['id']})")

    print(f'\n抖音登录状态: {check_douyin_login()}')
    print(f'Cookie 文件: {DOUYIN_COOKIE_FILE}')
