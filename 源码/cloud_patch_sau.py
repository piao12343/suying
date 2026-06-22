"""
云端适配补丁: 修改 social-auto-upload 的 douyin_uploader 以适配慢速云端环境
在 workflow 中克隆 social-auto-upload 后运行此脚本

诊断版: 检查"完成"按钮是否 disabled, 等到可用再点击
"""
import sys

def patch():
    path = 'uploader/douyin_uploader/main.py'
    with open(path, 'r', encoding='utf-8') as f:
        code = f.read()

    # 1) 视频上传检测后额外等 5 秒
    code = code.replace(
        'douyin_logger.success(_msg("\U0001f973", "\u89c6\u9891\u5df2\u7ecf\u4f20\u5b8c\u5566"))',
        'douyin_logger.success(_msg("\U0001f973", "\u89c6\u9891\u5df2\u7ecf\u4f20\u5b8c\u5566"))\n                    await asyncio.sleep(5)')

    # 2) 封面图上传后等待从 3 秒加到 30 秒
    code = code.replace(
        'await page.wait_for_timeout(3000)\n            douyin_logger.info(_msg("\U0001f5bc\ufe0f", "\u7ad6\u7248\u5c01\u9762\u5df2\u4e0a\u4f20\u5230\u9884\u89c8"))',
        'await page.wait_for_timeout(30000)\n            douyin_logger.info(_msg("\U0001f5bc\ufe0f", "\u7ad6\u7248\u5c01\u9762\u5df2\u4e0a\u4f20\u5230\u9884\u89c8"))')
    code = code.replace(
        'await page.wait_for_timeout(3000)\n            douyin_logger.info(_msg("\U0001f5bc\ufe0f", "\u6a2a\u7248\u5c01\u9762\u5df2\u4e0a\u4f20\u5230\u9884\u89c8"))',
        'await page.wait_for_timeout(30000)\n            douyin_logger.info(_msg("\U0001f5bc\ufe0f", "\u6a2a\u7248\u5c01\u9762\u5df2\u4e0a\u4f20\u5230\u9884\u89c8"))')

    # 3) 封面"完成": 诊断按钮状态, 等到可用再点击, 然后等弹窗关闭
    old = (
        'await cover_locator.get_by_role("button", name="\u5b8c\u6210", exact=True).first.click()\n'
        '        douyin_logger.info(_msg("\U0001f973", "\u89c6\u9891\u5c01\u9762\u8bbe\u7f6e\u5b8c\u6210"))\n'
        '        await cover_locator.wait_for(state="detached", timeout=20000)'
    )
    new = (
        'finish_btn = cover_locator.get_by_role("button", name="\u5b8c\u6210", exact=True).first\n'
        '        # 诊断: 检查按钮是否 disabled\n'
        '        for _wait_i in range(24):  # 最多等 120 秒 (24 * 5)\n'
        '            is_disabled = await finish_btn.is_disabled()\n'
        '            douyin_logger.info(_msg("\\U0001f50d", f"\\u5c01\\u9762\\u5b8c\\u6210\\u6309\\u94ae: disabled={is_disabled}, \\u5df2\\u7b49{_wait_i*5}\\u79d2"))\n'
        '            if not is_disabled:\n'
        '                break\n'
        '            await asyncio.sleep(5)\n'
        '        await asyncio.sleep(2)\n'
        '        await finish_btn.click(force=True)\n'
        '        douyin_logger.info(_msg("\\U0001f973", "\\u5df2\\u70b9\\u51fb\\u5c01\\u9762\\u5b8c\\u6210, \\u7b49\\u5f85\\u5f39\\u7a97\\u5173\\u95ed..."))\n'
        '        await cover_locator.wait_for(state="detached", timeout=120000)\n'
        '        douyin_logger.info(_msg("\U0001f973", "\u89c6\u9891\u5c01\u9762\u8bbe\u7f6e\u5b8c\u6210"))'
    )
    code = code.replace(old, new)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    print('Cloud patch applied successfully')

if __name__ == '__main__':
    patch()
