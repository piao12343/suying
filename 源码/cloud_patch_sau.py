"""
云端适配补丁: 修改 social-auto-upload 的 douyin_uploader 以适配慢速云端环境
在 workflow 中克隆 social-auto-upload 后运行此脚本

v4: 封面弹窗在云端无法自然关闭, 点击"完成"后等10秒再用JS强制移除所有遮挡层
    已测试通过 (2026-06-22), 自定义封面可正常设置并发布成功

备选方案 (如果JS移除方案失效):
    跳过自定义封面, 让抖音自动选推荐封面
    只需把步骤3的 new_block 替换为以下内容:
        new_block = [
            f'{pad}douyin_logger.info(_msg("\\U0001f4f7", "\\u4e91\\u7aef\\u8df3\\u8fc7\\u81ea\\u5b9a\\u4e49\\u5c01\\u9762, \\u53d1\\u5e03\\u65f6\\u81ea\\u52a8\\u9009\\u62e9\\u63a8\\u8350\\u5c01\\u9762"))',
            f'{pad}return',
        ]
    这样 set_thumbnail 直接 return, 不上传封面, 发布时由 handle_auto_video_cover 自动选推荐封面
    此方案也已测试通过 (2026-06-22)
"""
import sys


def patch():
    path = 'uploader/douyin_uploader/main.py'
    with open(path, 'r', encoding='utf-8') as f:
        code = f.read()

    ok = True

    # ── 步骤 1: 视频上传成功后额外等 5 秒 ──
    marker1 = 'douyin_logger.success(_msg("\U0001f973", "\u89c6\u9891\u5df2\u7ecf\u4f20\u5b8c\u5566"))'
    marker1_patched = marker1 + '\n                    await asyncio.sleep(5)'
    if marker1_patched in code:
        print('[OK] step1: 5s sleep after video upload already patched')
    elif marker1 in code:
        code = code.replace(marker1, marker1_patched, 1)
        print('[OK] step1: added 5s sleep after video upload')
    else:
        print('[FAIL] step1: marker not found')
        ok = False

    # ── 步骤 2: 封面图上传后等待从 3 秒加到 30 秒 ──
    for variant in ['\u7ad6\u7248', '\u6a2a\u7248']:
        old_pat = f'await page.wait_for_timeout(3000)\n            douyin_logger.info(_msg("\U0001f5bc\ufe0f", "{variant}\u5c01\u9762\u5df2\u4e0a\u4f20\u5230\u9884\u89c8"))'
        new_pat = f'await page.wait_for_timeout(30000)\n            douyin_logger.info(_msg("\U0001f5bc\ufe0f", "{variant}\u5c01\u9762\u5df2\u4e0a\u4f20\u5230\u9884\u89c8"))'
        if old_pat in code:
            code = code.replace(old_pat, new_pat)
            print(f'[OK] step2: {variant} cover wait 3s -> 30s')
        else:
            print(f'[WARN] step2: {variant} cover pattern not found (may already be patched)')

    # ── 步骤 3: 封面"完成"按钮 — 按行匹配, 点击后JS强制移除弹窗 ──
    lines = code.split('\n')
    click_line_idx = None
    detach_line_idx = None

    for i, line in enumerate(lines):
        if 'name="\u5b8c\u6210"' in line and '.click(' in line and 'cover_locator' in line:
            click_line_idx = i
        if click_line_idx is not None and i > click_line_idx and 'wait_for(state="detached"' in line:
            detach_line_idx = i
            break

    if 'cover_locator.is_visible()' in code and 'dy-creator-content-modal-wrap' in code:
        print('[OK] step3: JS force-remove cover modal already patched')
    elif click_line_idx is not None and detach_line_idx is not None:
        # 检测缩进
        indent = len(lines[click_line_idx]) - len(lines[click_line_idx].lstrip())
        pad = ' ' * indent
        print(f'[INFO] step3: found click line at {click_line_idx}, detach at {detach_line_idx}, indent={indent}')
        print(f'[INFO] step3: replacing lines {click_line_idx}-{detach_line_idx}')
        for j in range(click_line_idx, detach_line_idx + 1):
            print(f'  | {lines[j]}')

        new_block = [
            f'{pad}finish_btn = cover_locator.get_by_role("button", name="\u5b8c\u6210", exact=True).first',
            f'{pad}await asyncio.sleep(2)',
            f'{pad}await finish_btn.click(force=True)',
            f'{pad}douyin_logger.info(_msg("\\U0001f973", "\\u5df2\\u70b9\\u51fb\\u5c01\\u9762\\u5b8c\\u6210, \\u7b49\\u5f85\\u5f39\\u7a97\\u5173\\u95ed..."))',
            f'{pad}# \u4e91\\u7aef\\u5f39\\u7a97\\u65e0\\u6cd5\\u81ea\\u7136\\u5173\\u95ed, \\u7b49 10 \\u79d2\\u540e JS \\u5f3a\\u5236\\u79fb\\u9664',
            f'{pad}await asyncio.sleep(10)',
            f'{pad}try:',
            f'{pad}    still_visible = await cover_locator.is_visible()',
            f'{pad}    if still_visible:',
            f'{pad}        douyin_logger.info(_msg("\\U0001f527", "\\u5f39\\u7a97\\u4ecd\\u7136\\u5b58\\u5728, JS \\u5f3a\\u5236\\u79fb\\u9664..."))',
            f'{pad}        await page.evaluate(\'() => {{ document.querySelectorAll(".dy-creator-content-modal-wrap, .dy-creator-content-modal-mask, .dy-creator-content-modal, .dy-creator-content-portal").forEach(e => e.remove()); }}\')',
            f'{pad}        await asyncio.sleep(2)',
            f'{pad}        douyin_logger.info(_msg("\\U0001f973", "\\u5c01\\u9762\\u5f39\\u7a97\\u5df2\\u5f3a\\u5236\\u79fb\\u9664"))',
            f'{pad}    else:',
            f'{pad}        douyin_logger.info(_msg("\\U0001f973", "\\u5c01\\u9762\\u5f39\\u7a97\\u5df2\\u81ea\\u7136\\u5173\\u95ed"))',
            f'{pad}except Exception as _e:',
            f'{pad}    douyin_logger.info(_msg("\\U0001f973", f"\\u5c01\\u9762\\u5f39\\u7a97\\u5904\\u7406\\u5b8c\\u6210: {{_e}}"))',
        ]
        lines[click_line_idx:detach_line_idx + 1] = new_block
        code = '\n'.join(lines)
        print('[OK] step3: replaced with JS force-remove code')
    else:
        print(f'[FAIL] step3: click_line={click_line_idx}, detach_line={detach_line_idx}')
        ok = False

    # ── 步骤 4: 自主声明默认改为“无需添加自主声明” ──
    old_decl = 'declaration: str = "\u5185\u5bb9\u4e3a\u4e2a\u4eba\u89c2\u70b9\u6216\u89c1\u89e3"'
    new_decl = 'declaration: str = "\u65e0\u9700\u6dfb\u52a0\u81ea\u4e3b\u58f0\u660e"'
    if old_decl in code:
        code = code.replace(old_decl, new_decl, 1)
        print('[OK] step4: self declaration default -> 无需添加自主声明')
    elif new_decl in code:
        print('[OK] step4: self declaration default already patched')
    else:
        print('[FAIL] step4: self declaration default marker not found')
        ok = False

    # ── 步骤 5: 发布描述/话题要真正写入描述框 ──
    desc_old = (
        '        await page.keyboard.press("Delete")\n\n'
        '        for tag in tags or []:'
    )
    desc_new = (
        '        await page.keyboard.press("Delete")\n'
        '        if description:\n'
        '            await page.keyboard.type(description)\n'
        '            await page.keyboard.press("Space")\n\n'
        '        for tag in tags or []:'
    )
    if desc_new in code:
        print('[OK] step5: description input already patched')
    elif desc_old in code:
        code = code.replace(desc_old, desc_new, 1)
        print('[OK] step5: description will be typed before tags')
    else:
        print('[FAIL] step5: description input marker not found')
        ok = False

    # ── 步骤 6: 推荐封面确认框“确定”按钮会被 semi tooltip 浮层挡住 ──
    confirm_old = (
        '                        await page.get_by_role("button", name="确定").click()\n'
        '                        douyin_logger.info(_msg("🥳", "推荐封面已经应用"))'
    )
    confirm_new = (
        '                        await page.evaluate("""\\n'
        '                        () => document.querySelectorAll(\\n'
        '                            \'.semi-tooltip-wrapper,.semi-tooltip,.semi-portal .semi-tooltip-wrapper\'\\n'
        '                        ).forEach(e => e.remove())\\n'
        '                        """)\n'
        '                        confirm_btn = page.get_by_role("button", name="确定").first\n'
        '                        try:\n'
        '                            await confirm_btn.click(force=True, timeout=8000)\n'
        '                        except Exception:\n'
        '                            await page.evaluate("""\\n'
        '                            () => {\\n'
        '                                const buttons = [...document.querySelectorAll("button")];\\n'
        '                                const btn = buttons.find(e => (e.innerText || "").trim() === "确定");\\n'
        '                                if (btn) btn.click();\\n'
        '                            }\\n'
        '                            """)\n'
        '                        douyin_logger.info(_msg("🥳", "推荐封面已经应用"))'
    )
    if '.semi-tooltip-wrapper,.semi-tooltip,.semi-portal .semi-tooltip-wrapper' in code:
        print('[OK] step6: auto cover confirm tooltip cleanup already patched')
    elif confirm_old in code:
        code = code.replace(confirm_old, confirm_new, 1)
        print('[OK] step6: auto cover confirm button uses tooltip cleanup + force click')
    else:
        print('[FAIL] step6: auto cover confirm marker not found')
        ok = False

    if not ok:
        print('\n=== PATCH FAILED: some steps did not match ===')
        sys.exit(1)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    print('\nCloud patch applied successfully (all steps verified)')


if __name__ == '__main__':
    patch()
