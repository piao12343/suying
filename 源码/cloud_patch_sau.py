"""
云端适配补丁: 修改 social-auto-upload 的 douyin_uploader 以适配慢速云端环境
在 workflow 中克隆 social-auto-upload 后运行此脚本

v3: 按行匹配, 不依赖精确多行字符串; 每步验证替换是否成功
"""
import sys


def patch():
    path = 'uploader/douyin_uploader/main.py'
    with open(path, 'r', encoding='utf-8') as f:
        code = f.read()

    ok = True

    # ── 步骤 1: 视频上传成功后额外等 5 秒 ──
    marker1 = 'douyin_logger.success(_msg("\U0001f973", "\u89c6\u9891\u5df2\u7ecf\u4f20\u5b8c\u5566"))'
    if marker1 in code:
        code = code.replace(
            marker1,
            marker1 + '\n                    await asyncio.sleep(5)')
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

    # ── 步骤 3: 封面"完成"按钮 — 按行匹配, 诊断 disabled 状态 ──
    lines = code.split('\n')
    click_line_idx = None
    detach_line_idx = None

    for i, line in enumerate(lines):
        if 'name="\u5b8c\u6210"' in line and '.click(' in line and 'cover_locator' in line:
            click_line_idx = i
        if click_line_idx is not None and i > click_line_idx and 'wait_for(state="detached"' in line:
            detach_line_idx = i
            break

    if click_line_idx is not None and detach_line_idx is not None:
        # 检测缩进
        indent = len(lines[click_line_idx]) - len(lines[click_line_idx].lstrip())
        pad = ' ' * indent
        print(f'[INFO] step3: found click line at {click_line_idx}, detach at {detach_line_idx}, indent={indent}')
        print(f'[INFO] step3: replacing lines {click_line_idx}-{detach_line_idx}')
        for j in range(click_line_idx, detach_line_idx + 1):
            print(f'  | {lines[j]}')

        new_block = [
            f'{pad}finish_btn = cover_locator.get_by_role("button", name="\u5b8c\u6210", exact=True).first',
            f'{pad}# \u8bca\u65ad: \u68c0\u67e5\u6309\u94ae\u662f\u5426 disabled',
            f'{pad}for _wait_i in range(24):  # \u6700\u591a\u7b49 120 \u79d2 (24 * 5)',
            f'{pad}    is_disabled = await finish_btn.is_disabled()',
            f'{pad}    douyin_logger.info(_msg("\\U0001f50d", f"\\u5c01\\u9762\\u5b8c\\u6210\\u6309\\u94ae: disabled={{is_disabled}}, \\u5df2\\u7b49{{_wait_i*5}}\\u79d2"))',
            f'{pad}    if not is_disabled:',
            f'{pad}        break',
            f'{pad}    await asyncio.sleep(5)',
            f'{pad}await asyncio.sleep(2)',
            f'{pad}await finish_btn.click(force=True)',
            f'{pad}douyin_logger.info(_msg("\\U0001f973", "\\u5df2\\u70b9\\u51fb\\u5c01\\u9762\\u5b8c\\u6210, \\u7b49\\u5f85\\u5f39\\u7a97\\u5173\\u95ed..."))',
            f'{pad}await cover_locator.wait_for(state="detached", timeout=120000)',
            f'{pad}douyin_logger.info(_msg("\U0001f973", "\u89c6\u9891\u5c01\u9762\u8bbe\u7f6e\u5b8c\u6210"))',
        ]
        lines[click_line_idx:detach_line_idx + 1] = new_block
        code = '\n'.join(lines)
        print('[OK] step3: replaced with diagnostic code')
    else:
        print(f'[FAIL] step3: click_line={click_line_idx}, detach_line={detach_line_idx}')
        ok = False

    if not ok:
        print('\n=== PATCH FAILED: some steps did not match ===')
        sys.exit(1)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    print('\nCloud patch applied successfully (all steps verified)')


if __name__ == '__main__':
    patch()
