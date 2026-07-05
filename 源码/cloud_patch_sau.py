"""
云端适配补丁: 修改 social-auto-upload 的 douyin_uploader 以适配慢速云端环境
在 workflow 中克隆 social-auto-upload 后运行此脚本

v5: 自定义封面优先。点击封面“完成”后等待抖音页面自然接收封面状态；
    如果自定义封面仍未生效, 发布时再自动改用第一个推荐封面兜底。
    上传封面时优先走可见按钮触发 file chooser, 不再猜隐藏 input 下标。
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

    # ── 步骤 2b: 通过可见的“上传封面/替换”按钮触发 file chooser, 不猜隐藏 input 下标 ──
    cover_input_old_1 = 'cover_upload = cover_locator.locator("input.semi-upload-hidden-input").nth(1)'
    cover_input_old_2 = 'cover_upload = cover_locator.locator("input.semi-upload-hidden-input").nth(2)'
    upload_portrait_old = '            await cover_upload.set_input_files(self.thumbnail_portrait_path)'
    upload_landscape_old = '            await cover_upload.set_input_files(self.thumbnail_landscape_path)'
    upload_portrait_new = (
        '            async with page.expect_file_chooser(timeout=10000) as fc_info:\n'
        '                await cover_locator.get_by_text("上传封面", exact=True).last.click(force=True)\n'
        '            await fc_info.value.set_files(self.thumbnail_portrait_path)'
    )
    upload_landscape_new = (
        '            async with page.expect_file_chooser(timeout=10000) as fc_info:\n'
        '                await cover_locator.get_by_text("上传封面", exact=True).last.click(force=True)\n'
        '            await fc_info.value.set_files(self.thumbnail_landscape_path)'
    )
    if 'page.expect_file_chooser(timeout=10000)' in code:
        print('[OK] step2b: cover upload uses visible button file chooser already patched')
    elif (cover_input_old_1 in code or cover_input_old_2 in code) and upload_portrait_old in code and upload_landscape_old in code:
        code = code.replace(cover_input_old_1, '# cover upload is handled by visible button file chooser', 1)
        code = code.replace(cover_input_old_2, '# cover upload is handled by visible button file chooser', 1)
        code = code.replace(upload_portrait_old, upload_portrait_new, 1)
        code = code.replace(upload_landscape_old, upload_landscape_new, 1)
        print('[OK] step2b: cover upload input -> visible button file chooser')
    else:
        print('[FAIL] step2b: cover upload markers not found')
        ok = False

    # ── 步骤 3: 封面"完成"按钮 — 等页面自然接收封面状态, 不过早强制移除弹窗 ──
    lines = code.split('\n')
    click_line_idx = None
    detach_line_idx = None

    for i, line in enumerate(lines):
        if 'name="\u5b8c\u6210"' in line and '.click(' in line and 'cover_locator' in line:
            click_line_idx = i
        if click_line_idx is not None and i > click_line_idx and 'wait_for(state="detached"' in line:
            detach_line_idx = i
            break

    natural_cover_done = 'await cover_locator.wait_for(state="detached", timeout=60000)'
    old_force_remove_start = '        finish_btn = cover_locator.get_by_role("button", name="完成", exact=True).first\n'
    old_force_remove_end = '            douyin_logger.info(_msg("\\U0001f973", f"\\u5c01\\u9762\\u5f39\\u7a97\\u5904\\u7406\\u5b8c\\u6210: {_e}"))'
    if natural_cover_done in code:
        print('[OK] step3: cover finish waits for natural close already patched')
    elif old_force_remove_start in code and old_force_remove_end in code:
        start = code.index(old_force_remove_start)
        end = code.index(old_force_remove_end, start) + len(old_force_remove_end)
        new_block_text = (
            '        finish_btn = cover_locator.get_by_role("button", name="完成", exact=True).first\n'
            '        await asyncio.sleep(2)\n'
            '        await finish_btn.click(force=True)\n'
            '        douyin_logger.info(_msg("🥳", "已点击封面完成, 等待抖音应用封面..."))\n'
            '        try:\n'
            '            await cover_locator.wait_for(state="detached", timeout=60000)\n'
            '            douyin_logger.info(_msg("🥳", "自定义封面已应用"))\n'
            '        except Exception:\n'
            '            douyin_logger.warning(_msg("😵", "自定义封面等待超时, 关闭弹窗后发布时改用推荐封面兜底"))\n'
            '            await page.evaluate(\'() => { document.querySelectorAll(".dy-creator-content-modal-wrap, .dy-creator-content-modal-mask, .dy-creator-content-modal, .dy-creator-content-portal").forEach(e => e.remove()); }\')\n'
            '            await asyncio.sleep(2)'
        )
        code = code[:start] + new_block_text + code[end:]
        print('[OK] step3: removed early force-close, waits for Douyin to apply custom cover')
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
            f'{pad}douyin_logger.info(_msg("\\U0001f973", "\\u5df2\\u70b9\\u51fb\\u5c01\\u9762\\u5b8c\\u6210, \\u7b49\\u5f85\\u6296\\u97f3\\u5e94\\u7528\\u5c01\\u9762..."))',
            f'{pad}try:',
            f'{pad}    await cover_locator.wait_for(state="detached", timeout=60000)',
            f'{pad}    douyin_logger.info(_msg("\\U0001f973", "\\u81ea\\u5b9a\\u4e49\\u5c01\\u9762\\u5df2\\u5e94\\u7528"))',
            f'{pad}except Exception:',
            f'{pad}    douyin_logger.warning(_msg("\\U0001f635", "\\u81ea\\u5b9a\\u4e49\\u5c01\\u9762\\u7b49\\u5f85\\u8d85\\u65f6, \\u5173\\u95ed\\u5f39\\u7a97\\u540e\\u53d1\\u5e03\\u65f6\\u6539\\u7528\\u63a8\\u8350\\u5c01\\u9762\\u515c\\u5e95"))',
            f'{pad}    await page.evaluate(\'() => {{ document.querySelectorAll(".dy-creator-content-modal-wrap, .dy-creator-content-modal-mask, .dy-creator-content-modal, .dy-creator-content-portal").forEach(e => e.remove()); }}\')',
            f'{pad}    await asyncio.sleep(2)',
        ]
        lines[click_line_idx:detach_line_idx + 1] = new_block
        code = '\n'.join(lines)
        print('[OK] step3: waits for Douyin to apply custom cover')
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

    # ── 步骤 5b: 空描述保持为空, 不自动用标题填充描述区 ──
    desc_fallback_old = '        await self.fill_title_and_description(page, self.title, self.desc or self.title, self.tags)'
    desc_fallback_new = '        await self.fill_title_and_description(page, self.title, self.desc, self.tags)'
    if desc_fallback_new in code:
        print('[OK] step5b: empty description stays empty already patched')
    elif desc_fallback_old in code:
        code = code.replace(desc_fallback_old, desc_fallback_new, 1)
        print('[OK] step5b: empty description will not duplicate title')
    else:
        print('[FAIL] step5b: description fallback marker not found')
        ok = False

    # ── 步骤 6: 推荐封面确认框“确定”按钮会被 semi tooltip 浮层挡住 ──
    auto_cover_guard = (
        '    async def handle_auto_video_cover(self, page):\n'
        '        if self.thumbnail_portrait_path or self.thumbnail_landscape_path:\n'
        '            douyin_logger.warning(_msg("😵", "已上传自定义封面, 页面仍提示封面未设置, 不再改用推荐封面"))\n'
        '            return False\n'
    )
    auto_cover_marker = '    async def handle_auto_video_cover(self, page):\n'
    if auto_cover_guard in code:
        code = code.replace(auto_cover_guard, auto_cover_marker, 1)
        print('[OK] step6a: removed custom cover block, recommended cover fallback allowed')
    elif auto_cover_marker in code:
        print('[OK] step6a: recommended cover fallback allowed')
    else:
        print('[FAIL] step6a: handle_auto_video_cover marker not found')
        ok = False

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
