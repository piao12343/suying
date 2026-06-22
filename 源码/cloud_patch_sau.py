"""
云端适配补丁: 修改 social-auto-upload 的 douyin_uploader 以适配慢速云端环境
在 workflow 中克隆 social-auto-upload 后运行此脚本
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

    # 2) 封面上传后多等 15 秒, 让云端服务器处理完封面图
    code = code.replace(
        'douyin_logger.info(_msg("\U0001f5bc\ufe0f", "\u7ad6\u7248\u5c01\u9762\u5df2\u4e0a\u4f20\u5230\u9884\u89c8"))',
        'douyin_logger.info(_msg("\U0001f5bc\ufe0f", "\u7ad6\u7248\u5c01\u9762\u5df2\u4e0a\u4f20\u5230\u9884\u89c8"))\n            await asyncio.sleep(15)')

    # 3) 封面"完成"点击 + 日志 + wait_for(detached, 20s) 整块替换
    #    等弹窗自然关闭; 不关则 JS 删除外层 wrap (真正拦截点击的元凶)
    old = (
        'await cover_locator.get_by_role("button", name="\u5b8c\u6210", exact=True).first.click()\n'
        '        douyin_logger.info(_msg("\U0001f973", "\u89c6\u9891\u5c01\u9762\u8bbe\u7f6e\u5b8c\u6210"))\n'
        '        await cover_locator.wait_for(state="detached", timeout=20000)'
    )
    new = (
        'await cover_locator.get_by_role("button", name="\u5b8c\u6210", exact=True).first.click()\n'
        '        try:\n'
        '            await cover_locator.wait_for(state="detached", timeout=60000)\n'
        '        except Exception:\n'
        '            douyin_logger.info(_msg("\U0001f9f0", "\u5c01\u9762\u5f39\u7a97\u8fd8\u6ca1\u5173, \u518d\u70b9\u4e00\u6b21"))\n'
        '            try:\n'
        '                await cover_locator.get_by_role("button", name="\u5b8c\u6210", exact=True).first.click(force=True)\n'
        '                await cover_locator.wait_for(state="detached", timeout=60000)\n'
        '            except Exception:\n'
        '                douyin_logger.warning(_msg("\u26a0\ufe0f", "\u5c01\u9762\u5f39\u7a97\u5173\u4e0d\u4e86, JS\u79fb\u9664\u5916\u5c42wrap"))\n'
        '                await page.evaluate("() => document.querySelectorAll(\'.dy-creator-content-modal-wrap\').forEach(e => e.remove())")\n'
        '                await asyncio.sleep(1)\n'
        '        douyin_logger.info(_msg("\U0001f973", "\u89c6\u9891\u5c01\u9762\u8bbe\u7f6e\u5b8c\u6210"))'
    )
    code = code.replace(old, new)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    print('Cloud patch applied successfully')

if __name__ == '__main__':
    patch()
