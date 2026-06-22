"""
云端适配补丁: 修改 social-auto-upload 的 douyin_uploader 以适配慢速云端环境
在 workflow 中克隆 social-auto-upload 后运行此脚本

核心策略: 云端封面弹窗关闭极慢, 跳过自定义封面上传, 改用抖音推荐封面
"""
import sys

def patch():
    path = 'uploader/douyin_uploader/main.py'
    with open(path, 'r', encoding='utf-8') as f:
        code = f.read()

    # 1) 视频上传检测后额外等 5 秒, 确保服务端处理完毕
    code = code.replace(
        'douyin_logger.success(_msg("\U0001f973", "\u89c6\u9891\u5df2\u7ecf\u4f20\u5b8c\u5566"))',
        'douyin_logger.success(_msg("\U0001f973", "\u89c6\u9891\u5df2\u7ecf\u4f20\u5b8c\u5566"))\n                    await asyncio.sleep(5)')

    # 2) 跳过自定义封面上传 (云端弹窗关闭极慢, 会导致后续发布按钮被遮挡)
    #    改为在发布时由 handle_auto_video_cover 自动选择推荐封面
    old = (
        '    async def set_thumbnail(self, page: Page):\n'
        '        if not self.thumbnail_landscape_path and not self.thumbnail_portrait_path:\n'
        '            return'
    )
    new = (
        '    async def set_thumbnail(self, page: Page):\n'
        '        douyin_logger.info(_msg("\\U0001f4f7", "\\u4e91\\u7aef\\u8df3\\u8fc7\\u81ea\\u5b9a\\u4e49\\u5c01\\u9762, \\u53d1\\u5e03\\u65f6\\u81ea\\u52a8\\u9009\\u62e9\\u63a8\\u8350\\u5c01\\u9762"))\n'
        '        return'
    )
    code = code.replace(old, new)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    print('Cloud patch applied successfully')

if __name__ == '__main__':
    patch()
