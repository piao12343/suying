"""
速影 - 视频发布模块 (抖音等平台)
模块化设计, 方便以后扩展其他平台
"""

import os
import sys
import json
import asyncio
import io
from pathlib import Path
from datetime import datetime

# ============ 环境修补 ============
# tkinter GUI 运行时 sys.stdout/stderr 可能为 None,
# 会导致 loguru 等库报错 "Cannot log to objects of type 'NoneType'"
# 需要在导入 social-auto-upload 之前修补
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

# social-auto-upload 路径: 优先环境变量, 回退到仓库根目录下
SAU_DIR = Path(os.environ.get('SAU_DIR', str(Path(__file__).resolve().parent.parent / 'social-auto-upload')))
if str(SAU_DIR) not in sys.path:
    sys.path.insert(0, str(SAU_DIR))

# 确保日志目录存在 (social-auto-upload 的 loguru 需要)
(SAU_DIR / 'logs').mkdir(parents=True, exist_ok=True)

# Cookie 存储目录
COOKIE_DIR = Path(__file__).resolve().parent.parent / 'config' / 'cookies'
COOKIE_DIR.mkdir(parents=True, exist_ok=True)
DOUYIN_COOKIE_FILE = COOKIE_DIR / 'douyin_creator.json'


def check_douyin_login():
    """检查抖音登录状态 (同步接口)
    Returns:
        bool: True 表示已登录, False 表示未登录或 cookie 失效
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
    """执行抖音登录流程 (同步接口)

    Args:
        qrcode_callback: 二维码回调函数, 接收 dict: {'image_path': str, 'image_data_url': str}

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
                headless=False  # 登录时需要显示浏览器让用户扫码
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
    """发布视频到抖音 (同步接口)

    Args:
        video_path: 视频文件路径 (str 或 Path)
        title: 视频标题 (最多30字)
        tags: 标签列表, 如 ['故事', '情感']
        description: 视频描述, 为空则使用 title
        publish_strategy: 'immediate' 立即发布, 'scheduled' 定时发布
        publish_date: 定时发布时间 (datetime), 仅 scheduled 模式有效
        thumbnail_portrait_path: 竖版封面图片路径
        thumbnail_landscape_path: 横版封面图片路径
        headless: 是否无头模式运行浏览器
        debug: 是否开启调试模式

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


# ============ 扩展接口 (预留) ============

def publish_to_xiaohongshu(video_path, title, tags=None, **kwargs):
    """发布到小红书 (预留接口)"""
    return {'success': False, 'message': '小红书发布功能尚未实现'}


def publish_to_kuaishou(video_path, title, tags=None, **kwargs):
    """发布到快手 (预留接口)"""
    return {'success': False, 'message': '快手发布功能尚未实现'}


def publish_to_bilibili(video_path, title, tags=None, **kwargs):
    """发布到B站 (预留接口)"""
    return {'success': False, 'message': 'B站发布功能尚未实现'}


# ============ 统一发布接口 ============

PLATFORMS = {
    'douyin': {'name': '抖音', 'func': publish_to_douyin},
    'xiaohongshu': {'name': '小红书', 'func': publish_to_xiaohongshu},
    'kuaishou': {'name': '快手', 'func': publish_to_kuaishou},
    'bilibili': {'name': 'B站', 'func': publish_to_bilibili},
}


def publish_video(platform, video_path, title, tags=None, **kwargs):
    """统一发布接口

    Args:
        platform: 平台标识 ('douyin', 'xiaohongshu', 'kuaishou', 'bilibili')
        video_path: 视频文件路径
        title: 视频标题
        tags: 标签列表
        **kwargs: 其他平台特定参数

    Returns:
        dict: {'success': bool, 'message': str, 'platform': str}
    """
    if platform not in PLATFORMS:
        return {'success': False, 'message': f'不支持的平台: {platform}', 'platform': platform}

    result = PLATFORMS[platform]['func'](video_path, title, tags, **kwargs)
    result['platform'] = platform
    return result


def get_supported_platforms():
    """获取支持的平台列表"""
    return [{'id': k, 'name': v['name']} for k, v in PLATFORMS.items()]


if __name__ == '__main__':
    # 测试代码
    print('支持的发布平台:')
    for p in get_supported_platforms():
        print(f"  - {p['name']} ({p['id']})")

    print(f'\n抖音登录状态: {check_douyin_login()}')
    print(f'Cookie 文件: {DOUYIN_COOKIE_FILE}')
