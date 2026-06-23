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
    """Check Douyin login status — lightweight file check (no browser launch)
    Only verifies cookie file exists and is valid JSON with cookie data.
    Real cookie validity is verified at publish time.

    Returns:
        bool: True if cookie file exists and looks valid, False otherwise
    """
    try:
        if not DOUYIN_COOKIE_FILE.exists():
            return False
        if DOUYIN_COOKIE_FILE.stat().st_size < 50:
            return False
        with open(DOUYIN_COOKIE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            cookies = data.get('cookies', [])
            return isinstance(cookies, list) and len(cookies) > 0
        if isinstance(data, list):
            return len(data) > 0
        return False
    except Exception as e:
        print(f'[发布模块] 检查登录状态失败: {e}')
        return False


def login_douyin(qrcode_callback=None):
    """Execute Douyin login flow (sync wrapper)

    Bypasses cookie_auth() browser check for faster entry to login page.

    Args:
        qrcode_callback: QR code callback, receives dict: {'image_path': str, 'image_data_url': str}

    Returns:
        dict: {'success': bool, 'message': str, 'account_file': str}
    """
    try:
        from uploader.douyin_uploader.main import douyin_setup
        from uploader.douyin_uploader.main import douyin_cookie_gen

        async def _login():
            # If cookie doesn't exist, go straight to login (skip cookie_auth browser popup)
            if not DOUYIN_COOKIE_FILE.exists():
                result = await douyin_cookie_gen(
                    str(DOUYIN_COOKIE_FILE),
                    qrcode_callback=qrcode_callback,
                    headless=False,  # Login requires visible browser for QR scan
                )
                return result
            # Cookie exists, use normal flow (may briefly validate with browser)
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


if __name__ == '__main__':
    print(f'抖音登录状态: {check_douyin_login()}')
    print(f'Cookie 文件: {DOUYIN_COOKIE_FILE}')
