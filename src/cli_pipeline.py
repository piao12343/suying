"""
速影 - CLI Pipeline (GitHub Actions / headless server)
用法: python cli_pipeline.py <抖音链接或文案文件>
      python cli_pipeline.py --poll   (轮询 Cloudflare Worker 获取待处理链接)
"""

import os, sys, json, re, time, shutil, subprocess
from pathlib import Path
from datetime import datetime

# 隐藏子进程控制台窗口
NW = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}

# ============ 路径配置 ============
BASE = Path(__file__).resolve().parent.parent
SRC_DIR = BASE / 'src'
CFG_DIR = BASE / 'config'
OUT_DIR = BASE / 'outputs'
CACHE   = BASE / 'cache'
for e in [OUT_DIR, CACHE]:
    e.mkdir(parents=True, exist_ok=True)

# 确保 src/ 在 Python 路径中
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 缓存目录 (whisper 模型等)
_CACHE_BASE = Path(os.environ.get('SUYING_CACHE_DIR', str(CACHE)))
if not os.environ.get('HF_HOME'):
    os.environ['HF_HOME'] = str(_CACHE_BASE / 'hf_models')
    os.environ['HF_HUB_CACHE'] = str(_CACHE_BASE / 'hf_models' / 'hub')


# ============ 配置加载 ============
def build_config():
    """
    构建配置: 从 config_template.json 读默认值, 用 SUYING_* 环境变量覆盖密钥。
    GitHub Secrets 注入的环境变量会覆盖模板中的空字符串。
    """
    template_path = CFG_DIR / 'config_template.json'
    if template_path.exists():
        config = json.loads(template_path.read_text(encoding='utf-8'))
    else:
        config = {}

    # 环境变量 → 配置项映射
    env_overrides = {
        'SUYING_OPENROUTER_API_KEY': 'openrouter_api_key',
        'SUYING_PEXELS_API_KEY': 'pexels_api_key',
        'SUYING_LISTENER_SECRET': 'listener_secret',
        'SUYING_LISTENER_WORKER_URL': 'listener_worker_url',
        'SUYING_PUSHPLUS_TOKEN': 'pushplus_token',
        'SUYING_TTS_VOICE': 'tts_voice',
        'SUYING_TTS_RATE': 'tts_rate',
        'SUYING_OPENROUTER_MODEL': 'openrouter_model',
        'SUYING_PUB_DESC': 'pub_desc',
        'SUYING_AUTO_PUBLISH': 'auto_publish_douyin',
    }
    for env_key, config_key in env_overrides.items():
        val = os.environ.get(env_key)
        if val is not None:
            if config_key == 'auto_publish_douyin':
                config[config_key] = val.lower() == 'true'
            else:
                config[config_key] = val

    # ffmpeg 路径: 环境变量 > 系统 PATH
    config['ffmpeg_path'] = os.environ.get('FFMPEG_PATH', config.get('ffmpeg_path', 'ffmpeg'))

    return config


# ============ 日志 ============
def log(msg):
    """带时间戳的日志, 适配 GitHub Actions 输出"""
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


# ============ 学习记录 ============
LEARN_PATH = CFG_DIR / 'learning_context.json'


def load_learning():
    """加载学习记录"""
    try:
        if LEARN_PATH.exists():
            return json.loads(LEARN_PATH.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {'rewrite_corrections': [], 'keyword_corrections': []}


def get_learning_prompt():
    """生成注入到AI提示词中的学习示例 (移植自 gui.py _get_learning_prompt)"""
    data = load_learning()
    parts = []

    rewrites = data.get('rewrite_corrections', [])
    if rewrites:
        parts.append('【用户过往改写偏好】')
        parts.append('请先分析下面这些修改案例中体现的用户风格偏好(如语气、用词习惯、段落结构等), 然后应用到本次改写中。')
        for c in rewrites[-10:]:
            parts.append(f'原文片段: {c["original"][:200]}...')
            parts.append(f'用户改为: {c["corrected"][:200]}...')

    keywords = data.get('keyword_corrections', [])
    if keywords:
        parts.append('【用户偏好的配图关键词修正, 请参考风格选择类似调性的关键词】')
        for c in keywords[-15:]:
            parts.append(f'"{c["original"]}" → "{c["corrected"]}"')

    return '\n'.join(parts) if parts else ''


# ============ 平台字体适配 ============
def get_font_config():
    """根据操作系统返回字体配置"""
    import platform
    if platform.system() == 'Windows':
        return {
            'title_font': "C\\:/Windows/Fonts/msyhbd.ttc",
            'subtitle_font': 'Microsoft YaHei',
            'fonts_dir': 'C:\\Windows\\Fonts',
            'fonts_cachedir': 'C:\\Windows\\Temp\\fontconfig_cache',
        }
    else:
        # Linux (GitHub Actions ubuntu-latest)
        return {
            'title_font': "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            'subtitle_font': 'Noto Sans CJK SC',
            'fonts_dir': '/usr/share/fonts',
            'fonts_cachedir': '/tmp/fontconfig_cache',
        }


# ============ 流水线 ============
class Pipeline:
    def __init__(self, config):
        self.config = config
        self.raw_narration = ''
        self.title = ''
        self.narration = ''
        self.segments = []
        self.images = []
        self.audio_path = None
        self.word_boundaries = []
        self.run_dir = None
        self.proc_dir = None
        self.cover_portrait_path = None
        self.cover_landscape_path = None

    def run(self, douyin_input):
        """执行完整7步流水线, 返回是否成功"""
        success = False
        try:
            self.step1_extract(douyin_input)
            self.step2_rewrite()
            self.step3_split()
            self.step4_search()
            self.step5_tts()
            self.step6_render()
            self.step7_publish()
            success = True
        except Exception as e:
            log(f'流水线错误: {e}')
            import traceback
            log(traceback.format_exc())
        finally:
            if success:
                self._notify('速影 - 视频生成完成', f'视频《{self.title}》已成功生成并发布')
            else:
                self._notify('速影 - 视频生成失败', f'视频《{self.title}》生成过程中出现错误')
        return success

    # -------- 步骤1: 提取文案 --------
    def step1_extract(self, douyin_input):
        from extract_narration import (extract_share_url, resolve_video_id,
            get_video_info, download_and_extract_audio, transcribe_audio)

        log('=' * 50)
        log('[步骤1/7] 提取抖音文案...')

        is_url = douyin_input.startswith('http') or 'douyin' in douyin_input
        if is_url:
            share_url = extract_share_url(douyin_input)
            log(f'  链接: {share_url}')
            vid = resolve_video_id(share_url)
            if not vid:
                raise RuntimeError('无法解析视频ID')
            info = get_video_info(vid)
            log(f'  作者: {info["author"]}')
            if not info['video_url']:
                raise RuntimeError('无法获取视频地址')
            wav = CACHE / f'_audio_{vid}.wav'
            try:
                download_and_extract_audio(info['video_url'], wav)
                raw = transcribe_audio(wav)
            finally:
                if wav.exists():
                    os.remove(wav)
        else:
            # 直接读取文案文件
            raw = Path(douyin_input).read_text(encoding='utf-8').strip()
            log(f'  已加载文案: {len(raw)}字')

        log(f'  文案长度: {len(raw)} 字')
        self.raw_narration = raw

    # -------- 步骤2: AI改写 --------
    def step2_rewrite(self):
        import requests as req

        log('=' * 50)
        log('[步骤2/7] AI改写文案...')

        raw = self.raw_narration
        tm = re.search(r'【标题】\s*\n?(.+)', raw)
        bm = re.search(r'【优化口播文案】\s*\n?([\s\S]+)', raw)
        if tm and bm:
            title, narration = tm.group(1).strip(), bm.group(1).strip()
            log('  已有格式标记, 跳过改写')
        else:
            tpl = (CFG_DIR / 'ai_rewrite_prompt.txt').read_text(encoding='utf-8-sig')
            learn_ctx = get_learning_prompt()
            custom_instr = self.config.get('rewrite_custom_instruction', '').strip()
            prompt = tpl.rstrip()
            if custom_instr:
                prompt += '\n\n## 用户额外要求:\n' + custom_instr
                log('  已加载自定义指令')
            if learn_ctx:
                prompt += '\n\n' + learn_ctx
                log('  已注入学习偏好')
            prompt += '\n\n' + raw

            log(f'  模型: {self.config["openrouter_model"]}')
            for att in range(5):
                try:
                    r = req.post('https://openrouter.ai/api/v1/chat/completions',
                        headers={'Content-Type': 'application/json',
                                 'Authorization': f'Bearer {self.config["openrouter_api_key"]}'},
                        json={'model': self.config['openrouter_model'],
                              'messages': [{'role': 'user', 'content': prompt}],
                              'max_tokens': self.config.get('openrouter_max_tokens', 4000)},
                        timeout=180)
                    d = r.json()
                    if 'error' in d:
                        code = d['error'].get('code', 0)
                        msg = d['error'].get('message', '')[:80]
                        log(f'  尝试{att+1}/5 失败: {msg}')
                        if code == 429 and att < 4:
                            wait = 30
                            log(f'  限速, 等待{wait}秒后重试...')
                            time.sleep(wait)
                            continue
                        elif att < 4:
                            time.sleep(10)
                            continue
                        raise RuntimeError(f'OpenRouter错误: {d["error"]}')
                    break
                except req.exceptions.Timeout:
                    log(f'  尝试{att+1}/5 超时')
                    if att < 4:
                        time.sleep(10)
                        continue
                    raise RuntimeError('OpenRouter超时')

            txt = d['choices'][0]['message']['content'].strip()
            log(f'  tokens: {d["usage"]["total_tokens"]}, cost: ${d["usage"]["cost"]}')
            t2 = re.search(r'【标题】\s*\n?(.+)', txt)
            b2 = re.search(r'【优化口播文案】\s*\n?([\s\S]+)', txt)
            title = t2.group(1).strip() if t2 else raw[:6]
            narration = b2.group(1).strip() if b2 else txt

        self.title = title
        self.narration = narration
        log(f'  标题: {title}')
        log(f'  改写文案: {len(narration)} 字')

        safe = re.sub(r'[\\/:*?"<>|]', '_', title)
        self.run_dir = Path(OUT_DIR) / safe
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.proc_dir = self.run_dir / '过程'
        self.proc_dir.mkdir(exist_ok=True)
        (self.proc_dir / '01_rewritten_narration.txt').write_text(
            f'【标题】\n{title}\n\n【优化口播文案】\n{narration}', encoding='utf-8')

    # -------- 步骤3: 分镜切分 --------
    def step3_split(self):
        from video_pipeline import split_narration

        log('=' * 50)
        log('[步骤3/7] 分镜切分...')

        segs = split_narration(self.narration, self.config.get('num_shots', 10))
        self.segments = segs
        log(f'  共 {len(segs)} 个分镜, 预估 {sum(s["duration"] for s in segs):.0f}秒')
        for s in segs:
            log(f'  镜{s["id"]:2d} ({s["duration"]:5.1f}s): {s["text"][:30]}...')

        if self.proc_dir:
            (self.proc_dir / '02_storyboard.json').write_text(
                json.dumps(segs, ensure_ascii=False, indent=2), encoding='utf-8')

    # -------- 步骤4: 搜索配图 --------
    def step4_search(self):
        from video_pipeline import search_and_download_images

        log('=' * 50)
        log('[步骤4/7] 搜索实拍配图...')

        # 构建关键词学习上下文
        learn_data = load_learning()
        kw_corrections = learn_data.get('keyword_corrections', [])
        kw_ctx = ''
        if kw_corrections:
            kw_ctx = '【用户偏好的配图关键词修正, 请分析用户的选词风格并应用到本次提取中】'
            for c in kw_corrections[-15:]:
                kw_ctx += f'\n"{c["original"]}" → "{c["corrected"]}"'
            log('  已注入关键词偏好')

        imgs = search_and_download_images(self.segments, self.config, self.proc_dir,
                                          learning_context=kw_ctx)
        self.images = imgs
        log(f'  成功: {len(imgs)}/{len(self.segments)} 张')

    # -------- 步骤5: TTS合成 --------
    def step5_tts(self):
        from video_pipeline import generate_tts

        log('=' * 50)
        log(f'[步骤5/7] TTS语音合成 ({self.config["tts_voice"]})...')

        audio = self.proc_dir / 'narration.mp3'
        tts_rate = self.config.get('tts_rate', '-5%')
        audio_path, word_boundaries = generate_tts(self.narration, audio, self.config['tts_voice'], tts_rate)
        self.audio_path = audio_path
        self.word_boundaries = word_boundaries
        log(f'  音频: {audio.name}')
        log(f'  词边界: {len(word_boundaries)} 个')

    # -------- 步骤6: 视频渲染 --------
    def step6_render(self):
        from video_pipeline import create_kenburns_clip

        ffmpeg = self.config['ffmpeg_path']
        fonts = get_font_config()

        log('=' * 50)
        log('[步骤6/7] 视频渲染...')

        W, H, fps = self.config['video_width'], self.config['video_height'], self.config['fps']
        dirs = ['zoom_in', 'zoom_out', 'pan_left', 'pan_right']
        tc = sum(len(s['text']) for s in self.segments)

        # 字体配置 (适配 Linux/Windows)
        fonts_dir = CACHE / 'ffmpeg_fonts'
        fonts_conf = fonts_dir / 'fonts.conf'
        if not fonts_conf.exists():
            fonts_dir.mkdir(parents=True, exist_ok=True)
            fonts_conf.write_text(
                '<?xml version="1.0"?>\n<fontconfig>\n'
                f'  <dir>{fonts["fonts_dir"]}</dir>\n'
                f'  <cachedir>{fonts["fonts_cachedir"]}</cachedir>\n'
                '</fontconfig>', encoding='utf-8')
        ff_env = os.environ.copy()
        ff_env['FONTCONFIG_PATH'] = str(fonts_dir)
        ff_env['FONTCONFIG_FILE'] = 'fonts.conf'

        render_dir = CACHE / 'render'
        render_dir.mkdir(parents=True, exist_ok=True)

        # 6a: Ken Burns 动画片段
        log('  [6a] Ken Burns动画片段...')
        cdir = self.proc_dir / 'clips'
        cdir.mkdir(exist_ok=True)
        cfs = []
        trans_dur = 0.5 if self.config.get('transition_enabled', True) else 0
        if trans_dur > 0:
            log(f'    转场: 淡入淡出 {trans_dur}s')
        for s in self.segments:
            im = next((r for r in self.images if r['id'] == s['id']), None)
            if not im:
                continue
            cp = cdir / f"clip_{s['id']:02d}.mp4"
            dr = dirs[(s['id']-1) % 4]
            sd = max((len(s['text'])/tc) * sum(x['duration'] for x in self.segments), 3.0)
            if create_kenburns_clip(im['image_path'], cp, sd, W, H, fps, ffmpeg, dr, transition_duration=trans_dur):
                cfs.append(str(cp))
                log(f'    + 片段{s["id"]}: {dr} {sd:.1f}s')
            else:
                log(f'    x 片段{s["id"]}失败')
        if not cfs:
            raise RuntimeError('没有成功创建任何视频片段!')

        # 6b: 拼接片段
        log('  [6b] 拼接片段...')
        cl = self.proc_dir / 'concat_list.txt'
        cl.write_text(''.join(f"file '{c}'\n" for c in cfs), encoding='utf-8')
        cat = self.proc_dir / 'concat_video.mp4'
        subprocess.run([ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', str(cl),
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-pix_fmt', 'yuv420p', str(cat)],
            capture_output=True, timeout=300, **NW)

        # 6c: 叠加TTS音频
        log('  [6c] 叠加TTS音频...')
        wa = self.proc_dir / 'video_with_audio.mp4'
        subprocess.run([ffmpeg, '-y', '-i', str(cat), '-i', str(self.audio_path),
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k', '-shortest',
            '-map', '0:v:0', '-map', '1:a:0', str(wa)],
            capture_output=True, text=True, timeout=300, **NW)

        pr = subprocess.run([ffmpeg, '-i', str(wa), '-f', 'null', '-'],
            capture_output=True, text=True, timeout=30, **NW)
        dm = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.?\d*)', pr.stderr)
        tdur = int(dm.group(1))*3600 + int(dm.group(2))*60 + float(dm.group(3)) if dm else 319

        # 6d: 渲染标题封面
        log('  [6d] 渲染标题封面...')
        ss = render_dir / 'source.mp4'
        shutil.copy2(str(wa), str(ss))
        to = render_dir / 'title.mp4'
        te = self.title.replace("'", "'\\''").replace(":", "\\:")
        title_font_escaped = fonts['title_font'].replace('\\', '\\\\')
        vf1 = (f"drawbox=x=0:y=890:w=1080:h=140:color=white@0.6:t=fill:enable='between(t,0,1)',"
               f"drawtext=fontfile='{title_font_escaped}':text='{te}'"
               f":fontsize=88:fontcolor=0xFFD700:borderw=6:bordercolor=black"
               f":x=(w-text_w)/2:y=916:enable='between(t,0,1)'")
        r1 = subprocess.run([ffmpeg, '-y', '-i', str(ss), '-vf', vf1,
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23', '-c:a', 'copy', str(to)],
            capture_output=True, text=True, env=ff_env, timeout=600, **NW)
        if r1.returncode != 0:
            log('    标题渲染失败, 跳过')
            to = ss

        # 6e: 生成智能字幕
        log('  [6e] 生成智能字幕...')
        af = render_dir / 'final_sub.ass'

        def ft(t):
            return f"{int(t//3600)}:{int((t%3600)//60):02d}:{int(t%60):02d}.{int((t%1)*100):02d}"

        def ssp(text, ml=18):
            ps = re.split(r'(?<=[，,。！？；、])', text)
            sg = [s.strip() for s in ps if s.strip()]
            rs = []
            for g in sg:
                if len(g) <= ml:
                    rs.append(g)
                else:
                    sb = re.split(r'(?<=[，,、；：""\'\'（）—])', g)
                    sb = [s for s in sb if s.strip()]
                    b = ''
                    for s in sb:
                        if b and len(b+s) > ml:
                            rs.append(b); b = s
                        else:
                            b += s
                    if b:
                        if len(b) > ml:
                            for j in range(0, len(b), ml):
                                rs.append(b[j:j+ml])
                        else:
                            rs.append(b)
            return rs

        evts, alls = [], []
        for s in self.segments:
            alls.extend(ssp(s['text']))

        # 优先使用 Edge TTS 词边界做精准字幕对齐
        wb = self.word_boundaries
        if wb and alls:
            log('    使用词边界精准对齐字幕')
            sub_idx = 0
            acc_text = ''
            cur_start = wb[0]['start'] if wb else 0.0
            for b in wb:
                acc_text += b['text']
                if sub_idx < len(alls):
                    target = alls[sub_idx]
                    norm_acc = re.sub(r'[_\W]+', '', acc_text)
                    norm_tgt = re.sub(r'[_\W]+', '', target)
                    if norm_acc and norm_tgt and (norm_acc == norm_tgt or norm_acc.startswith(norm_tgt)):
                        evts.append(f"Dialogue: 0,{ft(cur_start)},{ft(b['end'])},Default,,0,0,0,,{target}")
                        sub_idx += 1
                        acc_text = ''
                        cur_start = b['end']
            # 兜底: 未匹配的行用等间距填充剩余时间
            if sub_idx < len(alls):
                remaining = alls[sub_idx:]
                last_end = wb[-1]['end'] if wb else tdur
                gap = max(tdur - last_end, 0.5)
                per = gap / len(remaining)
                for i, l in enumerate(remaining):
                    s_t = last_end + i * per
                    e_t = min(s_t + per, tdur)
                    evts.append(f"Dialogue: 0,{ft(s_t)},{ft(e_t)},Default,,0,0,0,,{l}")
        else:
            # 兜底: 字符比例估算
            log('    无词边界数据, 使用字符比例估算')
            tsc = sum(len(l) for l in alls) if alls else 1
            cur = 1.0
            for l in alls:
                d = (len(l)/tsc)*(tdur-1.0) if tsc else (tdur-1.0)/len(alls)
                e = min(cur+d, tdur)
                evts.append(f"Dialogue: 0,{ft(cur)},{ft(e)},Default,,0,0,0,,{l}")
                cur = e

        ae = str(af).replace('\\', '/').replace(':', '\\:')
        af.write_text(r"""[Script Info]
Title: Final
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: Default,""" + fonts['subtitle_font'] + r""",52,&H0030D0FF,&H000000FF,&H00FF00C8,&H00FFFFFF,-1,0,0,0,100,100,2,0,4,4,1,2,80,80,200

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + '\n'.join(evts), encoding='utf-8-sig')
        log(f'    字幕: {len(evts)}条')

        # 6f: 渲染字幕
        log('  [6f] 渲染字幕...')
        fo = render_dir / 'final.mp4'
        r2 = subprocess.run([ffmpeg, '-y', '-i', str(to), '-vf', f"ass='{ae}'",
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23', '-c:a', 'copy', str(fo)],
            capture_output=True, text=True, env=ff_env, timeout=900, **NW)
        if r2.returncode != 0 or not fo.exists():
            log('    字幕渲染失败, 使用标题版本')
            fo = to

        # 6g: 混入背景音乐
        bgm_enabled = self.config.get('bgm_enabled', False)
        bgm_dir = CFG_DIR / 'bgm'
        if bgm_enabled and bgm_dir.is_dir():
            bgm_files = list(bgm_dir.glob('*.mp3')) + list(bgm_dir.glob('*.wav')) + list(bgm_dir.glob('*.ogg'))
            if bgm_files:
                import random
                bgm_file = random.choice(bgm_files)
                bgm_vol = self.config.get('bgm_volume', 15) / 100.0
                bgm_out = render_dir / 'final_bgm.mp4'
                log(f'  [6g] 混入BGM: {bgm_file.name} (音量{int(bgm_vol*100)}%)')
                try:
                    r3 = subprocess.run([
                        ffmpeg, '-y',
                        '-i', str(fo),
                        '-stream_loop', '-1', '-i', str(bgm_file),
                        '-filter_complex',
                        f'[1:a]volume={bgm_vol},afade=t=out:st={max(tdur-3, 0)}:d=3[bgm];'
                        f'[0:a][bgm]amix=inputs=2:duration=shortest:dropout_transition=0[aout]',
                        '-map', '0:v', '-map', '[aout]',
                        '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                        '-t', str(tdur),
                        str(bgm_out),
                    ], capture_output=True, text=True, timeout=300, **NW)
                    if r3.returncode == 0 and bgm_out.exists():
                        fo = bgm_out
                        log('    BGM混入成功')
                    else:
                        log(f'    BGM混入失败: {r3.stderr[:100]}')
                except Exception as e:
                    log(f'    BGM混入异常: {e}')
            else:
                log('  [6g] BGM已启用但 config/bgm/ 目录无音频文件, 跳过')

        fd = self.run_dir / f'{self.title}--成品.mp4'
        if fd.exists():
            os.remove(fd)
        shutil.copy2(str(fo), str(fd))
        for t in [ss, to, fo, af]:
            if t.exists():
                try: os.remove(t)
                except: pass

        mb = os.path.getsize(str(fd)) / 1024 / 1024
        log('')
        log('#' * 50)
        log(f' 完成! {fd}')
        log(f' 大小: {mb:.1f} MB')
        log('#' * 50)

        # 截取封面
        self.cover_portrait_path = None
        self.cover_landscape_path = None
        try:
            cover_p = self.run_dir / 'cover_portrait.jpg'
            subprocess.run([ffmpeg, '-y', '-i', str(fd),
                '-vf', 'select=eq(n\\,0),crop=iw:ih*3/4:0:(oh-ih*3/4)/2',
                '-frames:v', '1', '-q:v', '2', str(cover_p)],
                capture_output=True, timeout=30, **NW)
            if cover_p.exists():
                self.cover_portrait_path = str(cover_p)
                log(f'  竖封面(3:4): {cover_p.name}')

            cover_l = self.run_dir / 'cover_landscape.jpg'
            subprocess.run([ffmpeg, '-y', '-i', str(fd),
                '-vf', 'select=eq(n\\,0),crop=iw*3/4:ih:(ow-iw*3/4)/2:0,scale=1440:1080',
                '-frames:v', '1', '-q:v', '2', str(cover_l)],
                capture_output=True, timeout=30, **NW)
            if cover_l.exists():
                self.cover_landscape_path = str(cover_l)
                log(f'  横封面(4:3): {cover_l.name}')
        except Exception as e:
            log(f'  封面截取失败(不影响发布): {e}')

    # -------- 步骤7: 发布抖音 --------
    def step7_publish(self):
        if not self.config.get('auto_publish_douyin', False):
            log('自动发布未启用, 跳过')
            return

        log('')
        log('=' * 50)
        log('[步骤7/7] 自动发布到抖音...')

        try:
            from publisher import publish_to_douyin, check_douyin_login
        except ImportError as e:
            log(f'发布模块导入失败 (social-auto-upload 可能未安装): {e}')
            return

        if not check_douyin_login():
            log('抖音未登录或 cookie 已失效, 跳过自动发布')
            log('请更新 GitHub Secrets 中的 DOUYIN_COOKIES_JSON')
            self._notify('速影 - Cookie 失效', '抖音 cookie 已过期, 请在本地重新扫码后更新 GitHub Secrets')
            return

        video_path = self.run_dir / f'{self.title}--成品.mp4'
        if not video_path.exists():
            log(f'视频文件不存在: {video_path}')
            return

        title = self.config.get('pub_title', '').strip() or self.title
        desc = self.config.get('pub_desc', '')
        strategy = self.config.get('pub_strategy', 'immediate')
        tags = [t.strip() for t in self.config.get('douyin_tags', '').split(',') if t.strip()]

        kwargs = dict(
            video_path=str(video_path),
            title=title[:30],
            tags=tags,
            description=desc,
            headless=True,
            debug=False,
            thumbnail_portrait_path=self.cover_portrait_path,
            thumbnail_landscape_path=self.cover_landscape_path,
        )

        log(f'  标题: {title[:30]}')
        log(f'  话题: {desc if desc else "(无)"}')
        log(f'  方式: {strategy}')

        result = publish_to_douyin(**kwargs)

        if result['success']:
            log('  发布成功!')
        else:
            log(f'  发布失败: {result["message"]}')

    # -------- 通知 --------
    def _notify(self, title, content):
        """通过 PushPlus 发送微信通知"""
        try:
            import requests as req
            token = self.config.get('pushplus_token', '').strip()
            if not token:
                return
            req.post('http://www.pushplus.plus/send',
                json={'token': token, 'title': title, 'content': content},
                timeout=10)
            log(f'已发送微信通知: {title}')
        except Exception:
            pass


# ============ Cloudflare 轮询 ============
def poll_cloudflare(config):
    """
    轮询 Cloudflare Worker 获取待处理链接。
    修复 bug: 使用 /api/poll (原 gui.py 用的是 /api)
    """
    import requests as req
    url = config.get('listener_worker_url', '').rstrip('/')
    secret = config.get('listener_secret', '')
    if not url or not secret:
        log('ERROR: listener_worker_url 或 listener_secret 未配置')
        return []
    try:
        resp = req.get(
            f'{url}/api/poll',
            params={'secret': secret},
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            links = [item['link'] for item in data.get('links', []) if item.get('link')]
            return links
        else:
            log(f'轮询返回状态码: {resp.status_code}')
    except Exception as e:
        log(f'轮询失败: {e}')
    return []


# ============ 主入口 ============
def main():
    log('=' * 60)
    log(' 速影 - CLI Pipeline')
    log('=' * 60)

    config = build_config()

    if '--poll' in sys.argv:
        # 轮询模式: 从 Cloudflare Worker 获取待处理链接
        links = poll_cloudflare(config)
        if not links:
            log('无待处理链接, 退出')
            sys.exit(0)
        log(f'获取到 {len(links)} 条待处理链接')

        # 发布间隔控制
        interval_min = config.get('publish_interval_minutes', 120)

        pipeline = Pipeline(config)
        for i, link in enumerate(links):
            if i > 0 and interval_min > 0:
                wait_sec = interval_min * 60
                log(f'发布间隔: 等待 {interval_min} 分钟...')
                time.sleep(wait_sec)
            log(f'\n{"="*60}')
            log(f'处理第 {i+1}/{len(links)} 条: {link}')
            pipeline.run(link)
            # 每条链接处理完后重置 pipeline 状态
            if i < len(links) - 1:
                pipeline = Pipeline(config)

    elif len(sys.argv) >= 2:
        # 单条处理模式
        pipeline = Pipeline(config)
        success = pipeline.run(sys.argv[1])
        sys.exit(0 if success else 1)
    else:
        print('用法: python cli_pipeline.py <抖音链接或文案文件>')
        print('      python cli_pipeline.py --poll   (轮询 Cloudflare Worker)')
        sys.exit(1)


if __name__ == '__main__':
    main()
