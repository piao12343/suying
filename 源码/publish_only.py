"""
仅发布模式: 从已有视频文件发布到抖音
用法: python publish_only.py <视频目录> <标题> [话题描述]
"""
import sys, os, json

def main():
    if len(sys.argv) < 3:
        print('用法: python publish_only.py <视频目录> <标题> [话题描述]')
        sys.exit(1)

    video_dir = sys.argv[1]
    title = sys.argv[2]
    desc = sys.argv[3] if len(sys.argv) > 3 else ''

    # Find the video file
    video_file = None
    for f in os.listdir(video_dir):
        if f.endswith('--成品.mp4'):
            video_file = os.path.join(video_dir, f)
            break

    if not video_file:
        print(f'ERROR: 未找到成品视频 in {video_dir}')
        sys.exit(1)

    print(f'视频: {video_file}')
    print(f'标题: {title}')
    print(f'话题: {desc}')

    # Add social-auto-upload to path
    sau_dir = os.environ.get('SAU_DIR', '')
    if sau_dir:
        sys.path.insert(0, sau_dir)

    # Find cookies
    cookie_file = os.path.join('配置', 'cookies', 'douyin_creator.json')
    if not os.path.exists(cookie_file):
        print(f'ERROR: Cookie 文件不存在: {cookie_file}')
        sys.exit(1)

    try:
        from publisher import publish_to_douyin, check_douyin_login
    except ImportError as e:
        print(f'ERROR: 无法导入 publisher: {e}')
        sys.exit(1)

    if not check_douyin_login():
        print('ERROR: 抖音未登录或 cookie 已失效')
        sys.exit(1)

    # Find cover images
    cover_p = os.path.join(video_dir, 'cover_portrait.jpg')
    cover_l = os.path.join(video_dir, 'cover_landscape.jpg')

    result = publish_to_douyin(
        video_path=video_file,
        title=title[:30],
        tags=[],
        description=desc,
        headless=True,
        debug=False,
        thumbnail_portrait_path=cover_p if os.path.exists(cover_p) else None,
        thumbnail_landscape_path=cover_l if os.path.exists(cover_l) else None,
    )

    if result['success']:
        print('发布成功!')
    else:
        print(f'发布失败: {result["message"]}')
        sys.exit(1)

if __name__ == '__main__':
    main()
