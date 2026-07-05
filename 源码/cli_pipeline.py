"""
CLI Pipeline (GitHub Actions / headless server)
Usage: python cli_pipeline.py <douyin_url_or_narration_file>
       python cli_pipeline.py --poll   (poll Cloudflare Worker for pending links)
"""

import os, sys, json, re, time, shutil, subprocess, threading, queue
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

# Hide subprocess console window
NW = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}

# ============ Path Config ============
BASE = Path(__file__).resolve().parent.parent
SRC_DIR = BASE / '源码'
CFG_DIR = BASE / '配置'
OUT_DIR = BASE / '作品'
CACHE   = BASE / '缓存'
for e in [OUT_DIR, CACHE]:
    e.mkdir(parents=True, exist_ok=True)

# Ensure src/ in Python path
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ai_client import call_openrouter_chat

# Cache dir (whisper models etc)
_CACHE_BASE = Path(os.environ.get('SUYING_CACHE_DIR', str(CACHE)))
if not os.environ.get('HF_HOME'):
    os.environ['HF_HOME'] = str(_CACHE_BASE / 'hf_models')
    os.environ['HF_HUB_CACHE'] = str(_CACHE_BASE / 'hf_models' / 'hub')


# ============ Config Loading ============
def build_config():
    """
    Build config from config_template.json, override with SUYING_* env vars.
    GitHub Secrets env vars override empty-string template defaults.
    """
    template_path = CFG_DIR / 'config_template.json'
    if template_path.exists():
        config = json.loads(template_path.read_text(encoding='utf-8'))
    else:
        config = {}

    # Cloud-specific defaults (from cloud_settings.json, only applies in GitHub Actions)
    cloud_settings_path = CFG_DIR / 'cloud_settings.json'
    if cloud_settings_path.exists():
        try:
            cloud_cfg = json.loads(cloud_settings_path.read_text(encoding='utf-8'))
            for key in ('auto_publish_douyin', 'publish_interval_minutes', 'poll_interval_minutes'):
                if key in cloud_cfg:
                    config[key] = cloud_cfg[key]
            log('已加载云端配置: cloud_settings.json')
        except Exception as e:
            log(f'云端配置加载失败: {e}')

    # Env var -> config mapping. GitHub Secrets take final precedence.
    env_overrides = {
        'SUYING_OPENROUTER_API_KEY': 'openrouter_api_key',
        'SUYING_PEXELS_API_KEY': 'pexels_api_key',
        'SUYING_LISTENER_SECRET': 'listener_secret',
        'SUYING_LISTENER_WORKER_URL': 'listener_worker_url',
        'SUYING_PUSHPLUS_TOKEN': 'pushplus_token',
        'SUYING_TTS_VOICE': 'tts_voice',
        'SUYING_TTS_RATE': 'tts_rate',
        'SUYING_OPENROUTER_MODEL': 'openrouter_model',
        'SUYING_OPENROUTER_BASE_URL': 'openrouter_base_url',
        'SUYING_PUB_DESC': 'pub_desc',
        'SUYING_AUTO_PUBLISH': 'auto_publish_douyin',
        'SUYING_PUBLISH_INTERVAL_MINUTES': 'publish_interval_minutes',
        'SUYING_PUBLISH_TIMEOUT_SECONDS': 'publish_timeout_seconds',
        'SUYING_REWRITE_TEMPLATE_TEXT': 'rewrite_template_text',
    }
    for env_key, config_key in env_overrides.items():
        val = os.environ.get(env_key)
        if val is not None:
            if config_key == 'auto_publish_douyin':
                config[config_key] = val.lower() == 'true'
            elif config_key in ('publish_interval_minutes', 'publish_timeout_seconds'):
                try:
                    config[config_key] = int(val)
                except ValueError:
                    log(f'{env_key} 不是有效数字, 已忽略')
            else:
                config[config_key] = val

    # ffmpeg path: env var > system PATH
    config['ffmpeg_path'] = os.environ.get('FFMPEG_PATH', config.get('ffmpeg_path', 'ffmpeg'))

    return config


# ============ Logging ============
BEIJING_TZ = ZoneInfo('Asia/Shanghai')


def log(msg):
    """Timestamped logging"""
    ts = datetime.now(BEIJING_TZ).strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


# ============ Learning Records ============
LEARN_PATH = CFG_DIR / 'learning_context.json'


def load_learning():
    """Load learning records"""
    try:
        if LEARN_PATH.exists():
            return json.loads(LEARN_PATH.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {'rewrite_corrections': [], 'keyword_corrections': []}


def get_learning_prompt():
    """Build learning examples for AI prompt injection (from gui.py)"""
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


# ============ Platform Font Config ============
def get_font_config():
    """Return font config by OS"""
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
            'title_font': "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            'subtitle_font': 'Noto Sans CJK SC',
            'fonts_dir': '/usr/share/fonts',
            'fonts_cachedir': '/tmp/fontconfig_cache',
        }


def probe_media_duration(ffmpeg_path, media_path, fallback=0.0):
    """Read media duration using ffmpeg stderr output."""
    try:
        result = subprocess.run(
            [ffmpeg_path, '-i', str(media_path), '-f', 'null', '-'],
            capture_output=True,
            text=True,
            timeout=30,
            **NW,
        )
        match = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.?\d*)', result.stderr)
        if match:
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        pass
    return fallback


# ============ Pipeline ============
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

    def run(self, douyin_input, publish_strategy=None, publish_date=None):
        """Run full 7-step pipeline"""
        success = False
        try:
            self.step1_extract(douyin_input)
            self.step2_rewrite()
            self.step3_split()
            self.step4_search()
            self.step5_tts()
            self.step6_render()
            self.step7_publish(publish_strategy=publish_strategy, publish_date=publish_date)
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

    # -------- Step 1: Extract narration --------
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
            video_urls = info.get('video_urls') or [info.get('video_url')]
            if not any(video_urls):
                raise RuntimeError('无法获取视频地址')
            wav = CACHE / f'_audio_{vid}.wav'
            try:
                t0 = time.perf_counter()
                log('  开始下载视频并提取音频...')
                download_and_extract_audio(video_urls, wav)
                log(f'  音频提取完成, 用时 {time.perf_counter() - t0:.1f} 秒')
                log('  开始语音识别...')
                t0 = time.perf_counter()
                raw = transcribe_audio(wav)
                log(f'  语音识别完成, 用时 {time.perf_counter() - t0:.1f} 秒')
            finally:
                if wav.exists():
                    os.remove(wav)
        else:
            # Read narration file directly
            raw = Path(douyin_input).read_text(encoding='utf-8').strip()
            log(f'  已加载文案: {len(raw)}字')

        log(f'  文案长度: {len(raw)} 字')
        self.raw_narration = raw

    # -------- Step 2: AI rewrite --------
    def step2_rewrite(self):
        log('=' * 50)
        log('[步骤2/7] AI改写文案...')

        raw = self.raw_narration
        tm = re.search(r'【标题】\s*\n?(.+)', raw)
        bm = re.search(r'【优化口播文案】\s*\n?([\s\S]+)', raw)
        if tm and bm:
            title, narration = tm.group(1).strip(), bm.group(1).strip()
            log('  已有格式标记, 跳过改写')
        else:
            tpl = self.config.get('rewrite_template_text')
            if tpl:
                log('  已加载云端 AI 改写模板')
            else:
                tpl = (CFG_DIR / 'ai生故事模板.txt').read_text(encoding='utf-8-sig')
            learn_ctx = get_learning_prompt()
            prompt = tpl.rstrip()
            if learn_ctx:
                prompt += '\n\n' + learn_ctx
                log('  已注入学习偏好')
            prompt += '\n\n' + raw

            log(f'  模型: {self.config["openrouter_model"]}')
            d = call_openrouter_chat(
                self.config,
                prompt,
                max_tokens=self.config.get('openrouter_max_tokens', 4000),
                timeout=180,
                log_func=log,
            )
            txt = d['choices'][0]['message']['content'].strip()
            usage = d.get('usage', {})
            log(f'  tokens: {usage.get("total_tokens", 0)}, cost: ${usage.get("cost", 0)}')
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

    # -------- Step 3: Storyboard split --------
    def step3_split(self):
        from video_pipeline import ai_split_narration

        log('=' * 50)
        log('[步骤3/7] 分镜切分...')

        num_shots = self.config.get('num_shots', 5)
        log(f'  尝试AI按故事情节分镜, 最多 {num_shots} 段...')
        segs = ai_split_narration(self.narration, self.config, num_shots, log_func=log)
        log('  AI分镜成功')
        self.segments = segs
        log(f'  共 {len(segs)} 个分镜, 预估 {sum(s["duration"] for s in segs):.0f}秒')
        for s in segs:
            log(f'  镜{s["id"]:2d} ({s["duration"]:5.1f}s): {s["text"][:30]}...')

        if self.proc_dir:
            (self.proc_dir / '02_storyboard.json').write_text(
                json.dumps(segs, ensure_ascii=False, indent=2), encoding='utf-8')

    # -------- Step 4: Search images --------
    def step4_search(self):
        from video_pipeline import search_and_download_images

        log('=' * 50)
        log('[步骤4/7] 搜索实拍配图...')

        # Build keyword learning context
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

    # -------- Step 5: TTS synthesis --------
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

    # -------- Step 6: Video render --------
    def step6_render(self):
        from video_pipeline import create_kenburns_clip

        ffmpeg = self.config['ffmpeg_path']
        fonts = get_font_config()

        log('=' * 50)
        log('[步骤6/7] 视频渲染...')

        W, H, fps = self.config['video_width'], self.config['video_height'], self.config['fps']
        dirs = ['zoom_in', 'zoom_out', 'pan_left', 'pan_right']
        tc = sum(len(s['text']) for s in self.segments)
        estimated_duration = sum(x['duration'] for x in self.segments)
        audio_duration = probe_media_duration(ffmpeg, self.audio_path, estimated_duration)
        target_duration = max(audio_duration, 1.0)
        cover_duration = 1.0
        output_duration = target_duration + cover_duration
        audio_delay_ms = int(round(cover_duration * 1000))
        log(f'  音频基准时长: {target_duration:.1f}s, 封面静音: {cover_duration:.3f}s')

        # Font config (Linux/Windows compatible)
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

        # 6a: Ken Burns clips
        log('  [6a] Ken Burns动画片段...')
        cdir = self.proc_dir / 'clips'
        cdir.mkdir(exist_ok=True)
        cfs = []
        trans_dur = 0.5 if self.config.get('transition_enabled', True) else 0
        if trans_dur > 0:
            log(f'    转场: 淡入淡出 {trans_dur}s')
        render_items = []
        for s in self.segments:
            im = next((r for r in self.images if r['id'] == s['id']), None)
            if not im:
                continue
            sd = max((len(s['text']) / tc) * target_duration, 3.0)
            if render_items and render_items[-1]['image_path'] == im['image_path']:
                render_items[-1]['duration'] += sd
                render_items[-1]['ids'].append(s['id'])
            else:
                render_items.append({
                    'ids': [s['id']],
                    'image_path': im['image_path'],
                    'duration': sd,
                })
        for idx, item in enumerate(render_items):
            first_id = item['ids'][0]
            cp = cdir / f"clip_{first_id:02d}.mp4"
            dr = dirs[idx % 4]
            fade_in = 0 if not cfs else trans_dur
            fade_out = 0 if idx == len(render_items) - 1 else trans_dur
            if create_kenburns_clip(
                item['image_path'], cp, item['duration'], W, H, fps, ffmpeg, dr,
                transition_duration=trans_dur,
                fade_in_duration=fade_in,
                fade_out_duration=fade_out,
            ):
                cfs.append(str(cp))
                id_text = f'{item["ids"][0]}-{item["ids"][-1]}' if len(item['ids']) > 1 else str(first_id)
                log(f'    + 片段{id_text}: {dr} {item["duration"]:.1f}s')
            else:
                log(f'    x 片段{first_id}失败')
        if not cfs:
            raise RuntimeError('没有成功创建任何视频片段!')

        # 6b: Concat clips
        log('  [6b] 拼接片段...')
        cl = self.proc_dir / 'concat_list.txt'
        cl.write_text(''.join(f"file '{c}'\n" for c in cfs), encoding='utf-8')
        cat = self.proc_dir / 'concat_video.mp4'
        concat_result = subprocess.run([ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', str(cl),
            '-c', 'copy', str(cat)],
            capture_output=True, text=True, timeout=300, **NW)
        if concat_result.returncode != 0 or not cat.exists() or cat.stat().st_size <= 0:
            raise RuntimeError('视频片段快速拼接失败: 中间片段参数不一致或输出无效')

        # 6c: Overlay TTS audio
        log('  [6c] 叠加TTS音频...')
        wa = self.proc_dir / 'video_with_audio.mp4'
        audio_result = subprocess.run([ffmpeg, '-y', '-i', str(cat), '-i', str(self.audio_path),
            '-t', f'{output_duration:.3f}', '-filter:a', f'adelay={audio_delay_ms}:all=1',
            '-c:v', 'copy',
            '-c:a', 'aac', '-b:a', '128k',
            '-map', '0:v:0', '-map', '1:a:0', str(wa)],
            capture_output=True, text=True, timeout=300, **NW)
        if audio_result.returncode != 0 or not wa.exists() or wa.stat().st_size <= 0:
            raise RuntimeError('视频叠加音频失败: 输出无效')

        tdur = probe_media_duration(ffmpeg, wa, output_duration)
        if abs(tdur - output_duration) > 1.0:
            raise RuntimeError(f'视频叠加音频后时长异常: {tdur:.1f}s, 预期 {output_duration:.1f}s')

        # 6d: Render title cover
        log('  [6d] 渲染标题封面...')
        ss = render_dir / 'source.mp4'
        shutil.copy2(str(wa), str(ss))
        to = render_dir / 'title.mp4'
        te = self.title.replace("'", "'\\''").replace(":", "\\:")
        title_font_escaped = fonts['title_font'].replace('\\', '\\\\')
        cover_expr = f"lt(t\\,{cover_duration:.4f})"
        content_expr = f"gte(t\\,{cover_duration:.4f})"
        vf1 = (f"drawbox=x=0:y=870:w=1080:h=220:color=white@0.6:t=fill:enable='{cover_expr}',"
               f"drawtext=fontfile='{title_font_escaped}':text='{te}'"
               f":fontsize=128:fontcolor=0xFFE600:borderw=9:bordercolor=black"
               f":x=(w-text_w)/2:y=905:enable='{cover_expr}',"
               f"drawtext=fontfile='{title_font_escaped}':text='全'"
               f":fontsize=82:fontcolor=0xFFE600:borderw=7:bordercolor=black"
               f":x=(w-text_w)/2:y=1210:enable='{cover_expr}',"
               f"drawtext=fontfile='{title_font_escaped}':text='{te}'"
               f":fontsize=64:fontcolor=0xFFE600:borderw=5:bordercolor=black"
               f":x=(w-text_w)/2:y=110:enable='{content_expr}'")
        r1 = subprocess.run([ffmpeg, '-y', '-i', str(ss), '-vf', vf1,
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23', '-c:a', 'copy', str(to)],
            capture_output=True, text=True, env=ff_env, timeout=600, **NW)
        if r1.returncode != 0:
            log('    标题渲染失败, 跳过')
            to = ss

        # 6e: Generate subtitles
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

        def subtitle_text(text):
            text = re.sub(r'[\r\n]+', ' ', text).strip()
            return re.sub(r'[，,。.!！？?；;、：:]+$', '', text).strip()

        def ass_text(text):
            return text.replace('\\', '\\\\').replace('{', '').replace('}', '')

        def add_evt(start, end, text):
            text = subtitle_text(text)
            start = max(start, cover_duration)
            if text and end > start:
                evts.append(f"Dialogue: 1,{ft(start)},{ft(end)},Default,,0,0,0,,{ass_text(text)}")
                evt_times.append((start, end))

        def build_ratio_events(reason):
            nonlocal evts, evt_times
            log(f'    {reason}, 改用稳定时间轴')
            evts, evt_times = [], []
            tsc = sum(len(l) for l in alls) if alls else 1
            cur = 0.0
            for l in alls:
                d = (len(l) / tsc) * tdur if tsc else tdur / len(alls)
                e = min(cur + d, tdur)
                add_evt(cur, e, l)
                cur = e

        evts, evt_times, alls = [], [], []
        for s in self.segments:
            alls.extend(ssp(s['text']))

        # Prefer Edge TTS word boundaries for precise subtitle alignment
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
                        add_evt(cur_start, b['end'], target)
                        sub_idx += 1
                        acc_text = ''
                        cur_start = b['end']
            if sub_idx < len(alls):
                build_ratio_events('词边界未匹配完整字幕')
            elif evt_times:
                gaps = [evt_times[i][0] - evt_times[i - 1][1] for i in range(1, len(evt_times))]
                max_gap = max(gaps) if gaps else 0
                gap_limit = max(2.5, min(tdur * 0.08, 5.0))
                last_start, last_end = evt_times[-1]
                if max_gap > gap_limit:
                    build_ratio_events(f'词边界字幕中间空档过大({max_gap:.1f}s)')
                elif last_end > tdur + 0.05 or last_start >= max(tdur - 0.5, tdur * 0.98):
                    build_ratio_events('词边界字幕结束时间异常')
        else:
            build_ratio_events('无词边界数据')

        ae = str(af).replace('\\', '/').replace(':', '\\:')
        af.write_text(r"""[Script Info]
Title: Final
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: Default,""" + fonts['subtitle_font'] + r""",86,&H0000E6FF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,8,0,2,120,120,500

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + '\n'.join(evts), encoding='utf-8-sig')
        log(f'    字幕: {len(evts)}条')

        # 6f: Burn subtitles
        log('  [6f] 渲染字幕...')
        fo = render_dir / 'final.mp4'
        r2 = subprocess.run([ffmpeg, '-y', '-i', str(to), '-vf', f"ass='{ae}'",
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23', '-c:a', 'copy', str(fo)],
            capture_output=True, text=True, env=ff_env, timeout=900, **NW)
        if r2.returncode != 0 or not fo.exists():
            log('    字幕渲染失败, 使用标题版本')
            fo = to

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

        # Extract covers
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

    # -------- Step 7: Publish to Douyin --------
    def step7_publish(self, publish_strategy=None, publish_date=None):
        if not self.config.get('auto_publish_douyin', False):
            log('自动发布未启用, 跳过')
            return

        log('')
        log('=' * 50)
        log('[步骤7/7] 自动发布到抖音...')

        try:
            from publisher import publish_to_douyin, check_douyin_login, split_douyin_tags
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

        title = self.title
        desc = self.config.get('pub_desc', '')
        publish_strategy = publish_strategy or self.config.get('pub_strategy', 'immediate')
        if publish_strategy not in ('immediate', 'scheduled'):
            publish_strategy = 'immediate'

        parsed_publish_date = None
        if publish_strategy == 'scheduled' and publish_date:
            try:
                parsed_publish_date = datetime.fromisoformat(
                    str(publish_date).replace('Z', '+00:00')
                ).astimezone(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
            except Exception as e:
                log(f'  定时发布时间解析失败, 改为立即发布: {e}')
                publish_strategy = 'immediate'

        thumbnail_portrait_path = self.cover_portrait_path
        thumbnail_landscape_path = self.cover_landscape_path
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            log('  云端发布: 不上传自定义封面, 使用抖音第一个推荐封面')
            thumbnail_portrait_path = None
            thumbnail_landscape_path = None

        kwargs = dict(
            video_path=str(video_path),
            title=title[:30],
            tags=split_douyin_tags(desc),
            description='',
            publish_strategy=publish_strategy,
            publish_date=parsed_publish_date,
            headless=True,
            debug=False,
            thumbnail_portrait_path=thumbnail_portrait_path,
            thumbnail_landscape_path=thumbnail_landscape_path,
        )

        log(f'  标题: {title[:30]}')
        log(f'  话题: {desc if desc else "(无)"}')
        if publish_strategy == 'scheduled' and parsed_publish_date:
            log(f'  方式: 定时发布 {parsed_publish_date.strftime("%Y-%m-%d %H:%M:%S")}')
        else:
            log('  方式: 立即发布')

        timeout_seconds = int(self.config.get('publish_timeout_seconds', 1200))
        log(f'  发布超时限制: {timeout_seconds // 60} 分钟')

        publish_payload = dict(kwargs)
        if parsed_publish_date:
            publish_payload['publish_date'] = parsed_publish_date.isoformat()
        publish_payload_path = self.proc_dir / 'publish_payload.json'
        publish_payload_path.write_text(
            json.dumps(publish_payload, ensure_ascii=False), encoding='utf-8')

        publish_code = r'''
import json
import sys
from pathlib import Path
from datetime import datetime

payload_path = Path(sys.argv[1])
src_dir = Path(sys.argv[2])
sys.path.insert(0, str(src_dir))

from publisher import publish_to_douyin

payload = json.loads(payload_path.read_text(encoding='utf-8'))
if payload.get('publish_date'):
    payload['publish_date'] = datetime.fromisoformat(payload['publish_date'])
result = publish_to_douyin(**payload)
print('SUYING_PUBLISH_RESULT=' + json.dumps(result, ensure_ascii=False), flush=True)
sys.exit(0 if result.get('success') else 2)
'''

        try:
            publish_env = os.environ.copy()
            publish_env['PYTHONUNBUFFERED'] = '1'
            publish_env['PYTHONIOENCODING'] = 'utf-8'
            proc = subprocess.Popen(
                [sys.executable, '-c', publish_code, str(publish_payload_path), str(SRC_DIR)],
                cwd=str(BASE),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding='utf-8',
                errors='replace',
                env=publish_env,
            )
            publish_lines = []
            publish_queue = queue.Queue()

            def read_publish_output():
                if proc.stdout:
                    for output_line in proc.stdout:
                        publish_queue.put(output_line)

            reader = threading.Thread(target=read_publish_output, daemon=True)
            reader.start()
            start_time = time.time()
            while True:
                try:
                    line = publish_queue.get(timeout=0.5)
                    publish_lines.append(line)
                    if not line.startswith('SUYING_PUBLISH_RESULT=') and line.strip():
                        log(line.rstrip())
                    continue
                except queue.Empty:
                    pass
                if proc.poll() is not None:
                    while True:
                        try:
                            line = publish_queue.get_nowait()
                        except queue.Empty:
                            break
                        publish_lines.append(line)
                        if not line.startswith('SUYING_PUBLISH_RESULT=') and line.strip():
                            log(line.rstrip())
                    break
                if time.time() - start_time > timeout_seconds:
                    proc.kill()
                    proc.wait()
                    log(f'  发布失败: 抖音发布超过 {timeout_seconds // 60} 分钟, 已停止等待')
                    return
                time.sleep(0.5)
        except Exception as exc:
            log(f'  发布失败: 发布进程异常: {exc}')
            return

        publish_output = ''.join(publish_lines).strip()

        result = None
        for line in publish_output.splitlines():
            if line.startswith('SUYING_PUBLISH_RESULT='):
                try:
                    result = json.loads(line.split('=', 1)[1])
                except Exception:
                    pass
        if result is None:
            result = {
                'success': False,
                'message': f'发布进程异常退出, 退出码: {proc.returncode}',
            }

        if result['success']:
            log('  发布成功!')
        else:
            log(f'  发布失败: {result["message"]}')
            raise RuntimeError(result["message"])

    # -------- Notify --------
    def _notify(self, title, content):
        """Send WeChat notification via PushPlus"""
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


# ============ Cloudflare Polling ============
def poll_cloudflare(config):
    """Poll Cloudflare Worker for pending links.
    Passes poll_interval to worker so it enforces minimum interval via KV timestamp.
    """
    import requests as req
    url = config.get('listener_worker_url', '').rstrip('/')
    secret = config.get('listener_secret', '')
    if not url or not secret:
        log('ERROR: listener_worker_url 或 listener_secret 未配置')
        return []
    try:
        interval = config.get('poll_interval_minutes', 1)
        resp = req.get(
            f'{url}/api/poll',
            params={'secret': secret, 'poll_interval': interval},
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get('skip'):
                log(f'轮询间隔未到, 跳过 (每{interval}分钟轮询一次)')
                return []
            links = [item['link'] for item in data.get('links', []) if item.get('link')]
            return links
        else:
            log(f'轮询返回状态码: {resp.status_code}')
    except Exception as e:
        log(f'轮询失败: {e}')
    return []


# ============ Main Entry ============
def main():
    log('=' * 60)
    log(' 速影 - CLI Pipeline')
    log('=' * 60)

    config = build_config()

    if '--poll' in sys.argv:
        # Poll mode: get pending links
        # Priority 1: env var from GitHub Actions workflow (already polled)
        # Priority 2: poll Cloudflare Worker directly (local/standalone mode)
        pending_json = os.environ.get('SUYING_PENDING_LINKS', '')
        if pending_json:
            try:
                links = json.loads(pending_json)
                log(f'从环境变量获取 {len(links)} 条链接 (workflow已轮询)')
            except Exception as e:
                log(f'环境变量解析失败: {e}')
                links = []
        else:
            links = poll_cloudflare(config)
        if not links:
            log('无待处理链接, 退出')
            sys.exit(0)
        log(f'获取到 {len(links)} 条待处理链接')

        publish_strategy = os.environ.get('SUYING_PUBLISH_STRATEGY', 'immediate')
        publish_date = os.environ.get('SUYING_PUBLISH_DATE', '')

        pipeline = Pipeline(config)
        all_success = True
        for i, link in enumerate(links):
            log(f'\n{"="*60}')
            log(f'处理第 {i+1}/{len(links)} 条: {link}')
            ok = pipeline.run(link, publish_strategy=publish_strategy, publish_date=publish_date)
            all_success = all_success and ok
            # Reset pipeline state after each link
            if i < len(links) - 1:
                pipeline = Pipeline(config)
        if not all_success:
            sys.exit(2)

    elif len(sys.argv) >= 2:
        # Single item mode
        pipeline = Pipeline(config)
        success = pipeline.run(sys.argv[1])
        if not success:
            sys.exit(2)
        sys.exit(0 if success else 1)
    else:
        print('用法: python cli_pipeline.py <抖音链接或文案文件>')
        print('      python cli_pipeline.py --poll   (轮询 Cloudflare Worker)')
        sys.exit(1)


if __name__ == '__main__':
    main()
