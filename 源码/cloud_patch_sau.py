"""
云端适配补丁: 修改 social-auto-upload 的 douyin_uploader 以适配慢速云端环境
在 workflow 中克隆 social-auto-upload 后运行此脚本
"""
import sys

def patch():
    path = 'uploader/douyin_uploader/main.py'
    with open(path, 'r', encoding='utf-8') as f:
        code = f.read()

    # 1) 封面弹窗 detach 超时从 20s 加到 120s
    code = code.replace(
        'cover_locator.wait_for(state="detached", timeout=20000)',
        'cover_locator.wait_for(state="detached", timeout=120000)')

    # 2) 视频上传检测后额外等 5 秒, 确保服务端处理完毕
    code = code.replace(
        'douyin_logger.success(_msg("\U0001f973", "\u89c6\u9891\u5df2\u7ecf\u4f20\u5b8c\u5566"))',
        'douyin_logger.success(_msg("\U0001f973", "\u89c6\u9891\u5df2\u7ecf\u4f20\u5b8c\u5566"))\n                    await asyncio.sleep(5)')

    # 3) 封面"完成"点击后如果弹窗不消失, 重试或强制关闭
    old = 'await cover_locator.get_by_role("button", name="\u5b8c\u6210", exact=True).first.click()\n        douyin_logger.info(_msg("\U0001f973", "\u89c6\u9891\u5c01\u9762\u8bbe\u7f6e\u5b8c\u6210"))'
    new = '''await cover_locator.get_by_role("button", name="\u5b8c\u6210", exact=True).first.click()
        try:
            await cover_locator.wait_for(state="detached", timeout=15000)
        except Exception:
            douyin_logger.info(_msg("\U0001f9f0", "\u5c01\u9762\u5f39\u7a97\u8fd8\u6ca1\u5173, \u5c0f\u4eba\u518d\u70b9\u4e00\u6b21"))
            await asyncio.sleep(2)
            try:
                await cover_locator.get_by_role("button", name="\u5b8c\u6210", exact=True).first.click(force=True)
                await asyncio.sleep(1)
            except Exception:
                await page.evaluate("() => document.querySelectorAll('.dy-creator-content-modal').forEach(e => e.remove())")
        douyin_logger.info(_msg("\U0001f973", "\u89c6\u9891\u5c01\u9762\u8bbe\u7f6e\u5b8c\u6210"))'''
    code = code.replace(old, new)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    print('Cloud patch applied successfully')

if __name__ == '__main__':
    patch()
