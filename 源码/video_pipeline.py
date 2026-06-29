"""
抖音短视频自动化流水线
用法: python video_pipeline.py <文案文件路径> [输出目录]
功能: 读取口播文案 → 分镜切分 → 搜索实拍图片 → TTS语音合成 → 生成竖屏短视频

可移植设计: 所有配置通过 config.json 管理, 不硬编码路径
"""

import os
import sys
import json
import re
import time
import shutil
import requests
import subprocess
from pathlib import Path

from ai_client import call_openrouter_chat

# 隐藏子进程控制台窗口
NW = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}

# ============ 工具函数 ============

def load_config(script_dir):
    """加载配置文件, 支持脚本同目录和当前目录"""
    for p in [script_dir / 'config.json', Path.cwd() / 'config.json']:
        if p.exists():
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
    raise FileNotFoundError("找不到 config.json, 请放在脚本同目录下")


def ensure_dir(path):
    """确保目录存在"""
    Path(path).mkdir(parents=True, exist_ok=True)
    return Path(path)


# ============ 步骤1: 文案分镜切分 ============

def _split_sentences(text):
    """按中文句子边界切分文本。"""
    sentences = re.split(r'(?<=[。？！…])\s*', text.strip())
    return [s.strip() for s in sentences if len(s.strip()) > 1]


def _build_segments_from_sentence_groups(sentences, groups):
    result = []
    for i, group in enumerate(groups):
        seg_text = ''.join(sentences[group[0]:group[1]])
        est_duration = max(len(seg_text) / 3.5, 3.0)
        result.append({
            'id': i + 1,
            'text': seg_text,
            'duration': round(est_duration, 1),
        })
    return result


def split_narration(text, num_shots=10):
    """
    将口播文案按句子边界切分为多个分镜段落
    返回: [{"id": 1, "text": "...", "duration": 秒}, ...]
    """
    sentences = _split_sentences(text)

    if not sentences:
        raise ValueError("文案为空, 无法分镜")

    total_chars = sum(len(s) for s in sentences)
    chars_per_shot = max(total_chars // num_shots, 30)

    segments = []
    current = []
    current_len = 0

    for s in sentences:
        current.append(s)
        current_len += len(s)
        if current_len >= chars_per_shot and len(segments) < num_shots - 1:
            segments.append(current)
            current = []
            current_len = 0

    if current:
        segments.append(current)

    # 确保不超过 num_shots
    while len(segments) > num_shots:
        # 合并最短的两段
        lengths = [sum(len(s) for s in seg) for seg in segments]
        min_idx = lengths.index(min(lengths))
        if min_idx == 0:
            segments[0].extend(segments[1])
            segments.pop(1)
        elif min_idx == len(segments) - 1:
            segments[-2].extend(segments[-1])
            segments.pop()
        else:
            # 合并到较短的邻居
            if lengths[min_idx - 1] <= lengths[min_idx + 1]:
                segments[min_idx - 1].extend(segments[min_idx])
                segments.pop(min_idx)
            else:
                segments[min_idx].extend(segments[min_idx + 1])
                segments.pop(min_idx + 1)

    result = []
    for i, seg in enumerate(segments):
        seg_text = ''.join(seg)
        # 预估时长: 中文约3.5字/秒
        est_duration = max(len(seg_text) / 3.5, 3.0)
        result.append({
            'id': i + 1,
            'text': seg_text,
            'duration': round(est_duration, 1),
        })

    return result


def ai_split_narration(text, config, num_shots=5, max_retries=2, log_func=print):
    """
    用AI按故事情节决定分镜边界。
    AI只返回句子编号范围, 程序用原文拼接, 避免AI改写/删文案。
    """
    sentences = _split_sentences(text)
    if not sentences:
        raise ValueError("文案为空, 无法分镜")

    if len(sentences) <= 1:
        return _build_segments_from_sentence_groups(sentences, [(0, len(sentences))])

    sentence_lines = '\n'.join(
        f'{i + 1}. {sentence}' for i, sentence in enumerate(sentences)
    )
    prompt = (
        "你是民间故事短视频分镜师。下面是按顺序编号的故事句子。\n"
        "请根据故事情节把它们分成几个连续镜头段落, 适合后续配图和视频画面切换。\n\n"
        "要求:\n"
        f"- 最多分成 {num_shots} 段, 不要超过这个数量\n"
        "- 按剧情阶段分段, 例如开端、矛盾出现、冲突升级、反转、结局\n"
        "- 必须覆盖全部句子, 不能遗漏、不能重叠、不能打乱顺序\n"
        "- 只能返回 JSON 数组, 不要解释, 不要 markdown\n"
        "- JSON 格式示例: [{\"start\":1,\"end\":3},{\"start\":4,\"end\":8}]\n"
        "- start/end 都是句子编号, end 表示该段最后一句, 包含 end\n\n"
        f"{sentence_lines}"
    )

    data = call_openrouter_chat(
        config,
        prompt,
        max_tokens=800,
        retries=max_retries,
        timeout=120,
        log_func=log_func,
    )
    used_model = data.get('_used_model')
    if used_model and used_model != config.get('openrouter_model') and log_func:
        log_func(f'  AI分镜实际使用模型: {used_model}')

    content = data.get('choices', [{}])[0].get('message', {}).get('content')
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("AI分镜返回内容为空")

    m = re.search(r'\[[\s\S]*\]', content.strip())
    if not m:
        raise RuntimeError("AI分镜未返回JSON数组")

    try:
        raw_groups = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"AI分镜JSON解析失败: {e}") from e

    if not isinstance(raw_groups, list) or not raw_groups:
        raise RuntimeError("AI分镜结果为空")
    if len(raw_groups) > num_shots:
        raise RuntimeError(f"AI分镜超过上限: {len(raw_groups)}/{num_shots}")

    groups = []
    expected_start = 1
    for item in raw_groups:
        if not isinstance(item, dict):
            raise RuntimeError("AI分镜格式错误: 分段不是对象")
        start = item.get('start')
        end = item.get('end')
        if not isinstance(start, int) or not isinstance(end, int):
            raise RuntimeError("AI分镜格式错误: start/end 不是整数")
        if start != expected_start or end < start or end > len(sentences):
            raise RuntimeError("AI分镜句子范围不连续或越界")
        groups.append((start - 1, end))
        expected_start = end + 1

    if expected_start != len(sentences) + 1:
        raise RuntimeError("AI分镜没有覆盖全部句子")

    return _build_segments_from_sentence_groups(sentences, groups)


# ============ 步骤2: 提取图片搜索关键词 ============

def extract_search_keywords(text, keyword_map):
    """从文本中提取图片搜索关键词(静态字典匹配, 作为AI的兜底方案)"""
    found_keywords = []
    # 先查精确匹配
    for zh_word, en_keyword in keyword_map.items():
        if zh_word in text:
            found_keywords.append(en_keyword)

    # 去重, 取前3个最相关的
    seen = set()
    unique = []
    for kw in found_keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique[:3] if unique else None


def prefer_chinese_people_query(query):
    """人物画面优先搜索中国人, 避免民间故事视频里混入外国人物图片。"""
    if not query:
        return query
    lower = query.lower()
    if 'chinese' in lower or 'asian' in lower:
        return query
    people_words = (
        'woman', 'man', 'mother', 'father', 'elderly', 'old ', 'wife', 'husband',
        'daughter', 'son', 'couple', 'family', 'bride', 'groom', 'girl', 'boy',
        'grandmother', 'grandfather', 'doctor', 'patient', 'villager', 'people',
    )
    if any(word in lower for word in people_words):
        return f'chinese {query}'
    return query


def ai_extract_image_keywords(segments, config, max_retries=2, learning_context=''):
    """
    用AI为每个镜头提取图片搜索关键词。
    一次API调用处理所有镜头, 返回 {shot_id: [keyword1, keyword2]} 字典。
    AI失败时返回空字典, 调用方应回退到静态 keyword_map。
    learning_context: 用户过往关键词修正记录, 注入到提示词中。
    """
    seg_list = '\n'.join(f"{seg['id']}. {seg['text']}" for seg in segments)
    learn_part = ''
    if learning_context:
        learn_part = f'\n{learning_context}\n'
    prompt = (
        "你是一个民间故事短视频配图搜索助手。下面每个编号是一组中文镜头文案，"
        "请为每组提取1-2个适合在Pexels图片网站搜索的英文关键词。\n"
        "要求:\n"
        "- 关键词必须是英文, 偏向视觉画面描述 (人物、场景、动作、氛围)\n"
        "- 如果画面出现人物, 必须优先中国人/亚洲人, 关键词里写 chinese, 例如 chinese mother, elderly chinese woman, chinese family\n"
        "- 画面适合民间故事频道: colorful, cinematic, rural village, folk tale mood\n"
        "- 每组关键词用逗号分隔, 2-4个单词的短语最佳\n"
        "- 严格按编号顺序输出, 格式: 编号. keyword1, keyword2\n"
        f"- 只输出关键词, 不要任何解释或多余文字\n{learn_part}\n"
        f"{seg_list}"
    )

    try:
        data = call_openrouter_chat(
            config,
            prompt,
            max_tokens=600,
            retries=max_retries,
            timeout=120,
            log_func=print,
        )
        used_model = data.get('_used_model')
        if used_model and used_model != config.get('openrouter_model'):
            print(f"   AI关键词实际使用模型: {used_model}")
    except Exception as e:
        print(f"   AI提取关键词失败: {e}")
        return {}

    # 解析AI返回的关键词。免费模型偶尔会返回 content=None, 此时回退静态关键词。
    content = data.get('choices', [{}])[0].get('message', {}).get('content')
    if not isinstance(content, str) or not content.strip():
        print("   AI提取关键词失败: 模型返回内容为空")
        return {}
    text_resp = content.strip()
    result = {}
    for line in text_resp.split('\n'):
        line = line.strip()
        if not line:
            continue
        m = re.match(r'(\d+)[\.\)、]\s*(.+)', line)
        if m:
            shot_id = int(m.group(1))
            kws = [kw.strip() for kw in m.group(2).split(',') if kw.strip()]
            if kws:
                result[shot_id] = kws

    print(f"   AI成功提取 {len(result)}/{len(segments)} 个镜头的搜索关键词")
    return result


# ============ 步骤3: Pexels图片搜索 ============

def search_pexels(query, api_key, per_page=3, orientation='portrait'):
    """从Pexels搜索图片, 返回图片URL列表"""
    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "orientation": orientation,
        "per_page": per_page,
        "size": "large",
    }
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers=headers, params=params, timeout=15
        )
        if resp.status_code != 200:
            print(f"   Pexels搜索失败 ({resp.status_code}): {query}")
            return []
        data = resp.json()
        urls = []
        for photo in data.get('photos', []):
            # 优先用 large2x (高分辨率), 回退到 original
            url = photo['src'].get('large2x') or photo['src'].get('original', '')
            if url:
                urls.append(url)
        return urls
    except Exception as e:
        print(f"   Pexels异常: {e}")
        return []


def search_baidu_images(query, per_page=3):
    """从百度图片搜索图片(备用方案)"""
    try:
        url = "https://image.baidu.com/search/acjson"
        params = {
            "tn": "resultjson_com",
            "word": query,
            "pn": 0,
            "rn": per_page * 2,  # 多请求一些, 过滤无效链接
            "ie": "utf-8",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        data = resp.json()
        urls = []
        for item in data.get('data', []):
            img_url = item.get('thumbURL') or item.get('middleURL', '')
            if img_url and img_url.startswith('http'):
                urls.append(img_url)
                if len(urls) >= per_page:
                    break
        return urls
    except Exception as e:
        print(f"   百度图片异常: {e}")
        return []


def download_image(url, save_path, timeout=30):
    """下载图片到本地"""
    try:
        resp = requests.get(url, timeout=timeout, stream=True,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200 and len(resp.content) > 5000:
            with open(save_path, 'wb') as f:
                f.write(resp.content)
            return True
    except:
        pass
    return False


def select_story_image_segments(segments, max_images=5):
    """为民间故事选少量关键分镜配图, 避免每句话都换图。"""
    if not segments:
        return []

    max_images = max(1, min(max_images, len(segments)))
    if len(segments) <= max_images:
        return segments

    picked = []
    last_pos = -1
    for i in range(max_images):
        pos = round(i * (len(segments) - 1) / (max_images - 1)) if max_images > 1 else 0
        pos = max(pos, last_pos + 1)
        pos = min(pos, len(segments) - (max_images - i))
        picked.append(segments[pos])
        last_pos = pos
    return picked


def fill_missing_story_images(segments, results):
    """没有单独配图的分镜复用最近图片, 让渲染流程保持简单稳定。"""
    if not segments or not results:
        return results

    by_id = {r['id']: r for r in results}
    filled = []
    current = None
    result_ids = sorted(by_id)
    for seg in segments:
        if seg['id'] in by_id:
            current = by_id[seg['id']]
        elif current is None:
            next_id = next((rid for rid in result_ids if rid > seg['id']), None)
            current = by_id.get(next_id)
        if current:
            filled.append({
                'id': seg['id'],
                'image_path': current['image_path'],
                'search_query': current.get('search_query', ''),
                'reused_from': current['id'],
            })
    return filled


def search_and_download_images(segments, config, output_dir, learning_context=''):
    """
    为每个分镜段落搜索并下载实拍图片。
    关键词优先级: AI动态提取 > 静态keyword_map > fallback_terms
    返回: [{"id": 1, "image_path": "...", "search_query": "..."}, ...]
    learning_context: 用户过往关键词修正记录, 传递给AI提取。
    """
    api_key = config['pexels_api_key']
    keyword_map = config.get('image_search_keywords_map', {})
    fallback_terms = config.get('fallback_search_terms', [])
    img_dir = ensure_dir(output_dir / 'images')
    max_story_images = int(config.get('story_image_count', 5) or 5)
    search_segments = select_story_image_segments(segments, max_story_images)
    if len(search_segments) < len(segments):
        print(f"   民间故事配图模式: 只搜索 {len(search_segments)} 张关键图, 其他分镜复用图片")

    # 优先用AI提取每个镜头的搜索关键词
    ai_keywords = {}
    or_key = config.get('openrouter_api_key', '')
    or_model = config.get('openrouter_model', '')
    if or_key and or_model:
        print("   正在用AI提取图片搜索关键词...")
        ai_keywords = ai_extract_image_keywords(
            search_segments,
            config,
            learning_context=learning_context,
        )
        if ai_keywords:
            print("   AI关键词提取完成, 使用AI关键词搜索")
        else:
            print("   AI提取失败, 回退到静态关键词表")
    else:
        print("   未配置OpenRouter, 使用静态关键词表")

    results = []
    used_queries = set()

    for i, seg in enumerate(search_segments):
        print(f"   分镜 {seg['id']}: 搜索图片...")

        # 关键词优先级: AI提取 > 静态keyword_map > fallback
        query = None
        source = ''

        # 1) AI关键词
        if seg['id'] in ai_keywords:
            kws = ai_keywords[seg['id']]
            query = kws[0] if kws else None
            source = 'AI'

        # 2) 静态keyword_map兜底
        if not query:
            keywords = extract_search_keywords(seg['text'], keyword_map)
            query = keywords[0] if keywords else None
            source = '静态'

        # 3) fallback泛用词兜底
        if not query:
            query = fallback_terms[i % len(fallback_terms)]
            source = '泛用'

        # 避免重复搜索
        query = prefer_chinese_people_query(query)
        if query in used_queries:
            query = query + " aesthetic"
        used_queries.add(query)

        print(f"      搜索词[{source}]: {query}")

        # Pexels搜索
        urls = search_pexels(query, api_key, per_page=5)

        # Pexels失败则用百度备用
        if not urls:
            print(f"      Pexels无结果, 尝试百度图片...")
            zh_keywords = [k for k, v in keyword_map.items() if v == query]
            zh_query = zh_keywords[0] if zh_keywords else query
            urls = search_baidu_images(zh_query, per_page=5)

        # 下载第一张成功的图片
        downloaded = False
        for j, img_url in enumerate(urls):
            ext = 'jpg'
            save_path = img_dir / f"shot_{seg['id']:02d}_{j}.{ext}"
            if download_image(img_url, save_path):
                results.append({
                    'id': seg['id'],
                    'image_path': str(save_path),
                    'search_query': query,
                })
                downloaded = True
                print(f"      ✓ 已下载: {query} → {save_path.name}")
                break

        if not downloaded:
            print(f"      ✗ 未找到图片, 使用默认搜索词")
            # 最后的兜底
            fallback_query = fallback_terms[i % len(fallback_terms)]
            urls = search_pexels(fallback_query, api_key, per_page=3)
            for j, img_url in enumerate(urls):
                save_path = img_dir / f"shot_{seg['id']:02d}_fallback.{ext}"
                if download_image(img_url, save_path):
                    results.append({
                        'id': seg['id'],
                        'image_path': str(save_path),
                        'search_query': fallback_query,
                    })
                    print(f"      ✓ 兜底下载: {fallback_query}")
                    downloaded = True
                    break

        if not downloaded:
            print(f"      ✗ 分镜{seg['id']}图片获取失败!")

        time.sleep(0.5)  # 避免API频率限制

    return fill_missing_story_images(segments, results)


# ============ 步骤4: TTS语音合成 ============

def generate_tts(text, output_path, voice, rate="-5%"):
    """使用edge-tts生成语音，同时捕获词边界数据用于精准字幕对齐。
    返回: (output_path, word_boundaries)
        word_boundaries: [{'text': str, 'start': float(秒), 'end': float(秒)}, ...]
    """
    import edge_tts

    boundaries = []
    c = edge_tts.Communicate(text, voice, rate=rate, boundary='WordBoundary')
    with open(str(output_path), 'wb') as f:
        for chunk in c.stream_sync():
            if chunk['type'] == 'audio':
                f.write(chunk['data'])
            elif chunk['type'] == 'WordBoundary':
                start_s = chunk['offset'] / 10_000_000
                end_s = (chunk['offset'] + chunk['duration']) / 10_000_000
                boundaries.append({
                    'text': chunk['text'],
                    'start': start_s,
                    'end': end_s,
                })

    return output_path, boundaries


def generate_subtitles(audio_path, ffmpeg_path, output_dir):
    """
    用ffmpeg获取音频时长, 然后按文案分段生成字幕文件
    返回: (音频时长秒数, 字幕文件路径)
    """
    # 获取音频时长
    cmd = [
        str(ffmpeg_path), '-i', str(audio_path),
        '-f', 'null', '-'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, **NW)
    # 从stderr中提取Duration
    duration_match = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.?\d*)', result.stderr)
    if duration_match:
        h, m, s = duration_match.groups()
        total_seconds = int(h) * 3600 + int(m) * 60 + float(s)
    else:
        total_seconds = 0

    return total_seconds


# ============ 步骤5: 视频合成 ============

def prepare_image(image_path, output_path, width, height, ffmpeg_path):
    """预处理图片: 缩放裁切到目标分辨率"""
    cmd = [
        str(ffmpeg_path), '-y',
        '-i', str(image_path),
        '-vf', f'scale={width*2}:{height*2}:force_original_aspect_ratio=increase,crop={width*2}:{height*2}',
        '-q:v', '2',
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True, timeout=30, **NW)
    return output_path


def create_kenburns_clip(image_path, output_path, duration, width, height, fps,
                         ffmpeg_path, direction='zoom_in', transition_duration=0,
                         fade_in_duration=None, fade_out_duration=None):
    """创建Ken Burns效果的单个视频片段。
    transition_duration: 淡入淡出时长(秒), 0=不添加转场。
    fade_in_duration/fade_out_duration: 分别控制开头淡入和结尾淡出;
        为 None 时沿用 transition_duration。
    """
    total_frames = int(duration * fps)
    w2 = width * 2  # 先放大再缩放, 保证zoompan有足够像素
    h2 = height * 2

    # 先预处理图片到2倍分辨率
    prep_path = str(output_path).replace('.mp4', '_prep.jpg')
    prepare_image(image_path, prep_path, width, height, ffmpeg_path)

    if direction == 'zoom_in':
        # 短视频风格: 轻微推近, 保持高清稳定, 避免PPT式大幅缩放。
        zp = (
            f"zoompan=z='min(zoom+0.00045,1.14)'"
            f":x='iw/2-(iw/zoom/2)'"
            f":y='ih/2-(ih/zoom/2)'"
            f":d={total_frames}:s={width}x{height}:fps={fps}"
        )
    elif direction == 'zoom_out':
        # 轻微拉远。
        zp = (
            f"zoompan=z='if(eq(on,1),1.14,max(zoom-0.00045,1.0))'"
            f":x='iw/2-(iw/zoom/2)'"
            f":y='ih/2-(ih/zoom/2)'"
            f":d={total_frames}:s={width}x{height}:fps={fps}"
        )
    elif direction == 'pan_left':
        # 轻微横移, 不做夸张滑动。
        zp = (
            f"zoompan=z='1.10'"
            f":x='if(eq(on,1),0,min(x+0.9,iw-iw/zoom))'"
            f":y='ih/2-(ih/zoom/2)'"
            f":d={total_frames}:s={width}x{height}:fps={fps}"
        )
    else:  # pan_right
        zp = (
            f"zoompan=z='1.10'"
            f":x='if(eq(on,1),iw-iw/zoom,max(x-0.9,0))'"
            f":y='ih/2-(ih/zoom/2)'"
            f":d={total_frames}:s={width}x{height}:fps={fps}"
        )

    # 构建视频滤镜链
    visual_style = (
        "eq=brightness=0.025:contrast=1.08:saturation=1.16,"
        "unsharp=5:5:0.45:3:3:0.15"
    )
    vf = f"{zp},{visual_style},format=yuv420p"
    if fade_in_duration is None:
        fade_in_duration = transition_duration
    if fade_out_duration is None:
        fade_out_duration = transition_duration
    if fade_in_duration > 0:
        vf += f",fade=t=in:st=0:d={fade_in_duration}"
    if fade_out_duration > 0:
        fade_out_start = max(duration - fade_out_duration, 0)
        vf += f",fade=t=out:st={fade_out_start}:d={fade_out_duration}"

    cmd = [
        str(ffmpeg_path), '-y',
        '-loop', '1', '-i', str(prep_path),
        '-vf', vf,
        '-t', str(duration),
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-pix_fmt', 'yuv420p',
        str(output_path),
    ]
    timeout_seconds = max(120, int(duration * 3))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds, **NW)
    # 清理预处理图片
    if os.path.exists(prep_path):
        os.remove(prep_path)
    return result.returncode == 0


def add_title_bar(video_path, output_path, title, width, height, bar_height, ffmpeg_path):
    """在视频顶部添加标题栏"""
    # 标题栏: 半透明黑底 + 白色标题文字
    title_escaped = title.replace("'", "'\\''").replace(":", "\\:")
    font_path = "C\\:/Windows/Fonts/msyh.ttc"  # 微软雅黑
    vf = (
        f"drawbox=x=0:y=0:w={width}:h={bar_height}:color=black@0.7:t=fill,"
        f"drawtext=fontfile='{font_path}'"
        f":text='{title_escaped}'"
        f":fontsize=42:fontcolor=white"
        f":x=(w-text_w)/2:y=({bar_height}-text_h)/2"
    )
    cmd = [
        str(ffmpeg_path), '-y',
        '-i', str(video_path),
        '-vf', vf,
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '25',
        '-c:a', 'copy',
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, **NW)
    return result.returncode == 0


def add_subtitles(video_path, srt_path, output_path, width, height, ffmpeg_path, font_size=38):
    """在视频底部添加字幕"""
    # 用ASS字幕样式获得更好效果
    srt_escaped = str(srt_path).replace('\\', '/').replace(':', '\\:')
    vf = (
        f"subtitles='{srt_escaped}':fontsdir='C\\:/Windows/Fonts':force_style='"
        f"FontSize={font_size},"
        f"FontName=Microsoft YaHei,"
        f"PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,"
        f"BorderStyle=3,"
        f"Outline=2,"
        f"Shadow=1,"
        f"BackColour=&H80000000,"
        f"MarginV=80,"
        f"Alignment=2'"
    )
    cmd = [
        str(ffmpeg_path), '-y',
        '-i', str(video_path),
        '-vf', vf,
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '25',
        '-c:a', 'copy',
        str(output_path),
    ]
    env = os.environ.copy()
    env['FONTCONFIG_PATH'] = r'D:\models\ffmpeg_fonts'
    env['FONTCONFIG_FILE'] = 'fonts.conf'
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env, **NW)
    return result.returncode == 0


def generate_srt(segments, total_duration, output_path):
    """根据分镜段落和总时长生成SRT字幕文件"""
    total_chars = sum(len(s['text']) for s in segments)

    srt_lines = []
    current_time = 0.0

    for i, seg in enumerate(segments):
        # 按文字长度比例分配时间
        seg_duration = (len(seg['text']) / total_chars) * total_duration if total_chars > 0 else total_duration / len(segments)

        start_time = current_time
        end_time = min(current_time + seg_duration, total_duration)

        # SRT时间格式: HH:MM:SS,mmm
        def fmt_time(t):
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = int(t % 60)
            ms = int((t % 1) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        # 将长文本分行显示(每行最多20个字)
        text = seg['text']
        display_lines = []
        for j in range(0, len(text), 18):
            display_lines.append(text[j:j+18])
        display_text = '\n'.join(display_lines[:3])  # 最多3行

        srt_lines.append(f"{i+1}")
        srt_lines.append(f"{fmt_time(start_time)} --> {fmt_time(end_time)}")
        srt_lines.append(display_text)
        srt_lines.append("")

        current_time = end_time

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(srt_lines))

    return output_path


def synthesize_video(segments, image_results, audio_path, title, config, output_dir):
    """
    合成最终视频:
    1. 每张图片做Ken Burns效果
    2. 拼接所有片段(带淡入淡出)
    3. 叠加音频
    4. 添加标题栏
    5. 添加字幕
    """
    ffmpeg = config['ffmpeg_path']
    W = config['video_width']
    H = config['video_height']
    fps = config['fps']
    bar_h = config.get('title_bar_height', 140)
    font_size = config.get('subtitle_font_size', 38)

    clips_dir = ensure_dir(output_dir / 'clips')
    directions = ['zoom_in', 'zoom_out', 'pan_left', 'pan_right']

    # 1. 创建Ken Burns片段
    print("   [1/5] 创建Ken Burns动画片段...")
    clip_files = []
    total_chars = sum(len(s['text']) for s in segments)

    for seg in segments:
        # 找到对应的图片
        img_result = next((r for r in image_results if r['id'] == seg['id']), None)
        if not img_result:
            print(f"      分镜{seg['id']}无图片, 跳过")
            continue

        clip_path = clips_dir / f"clip_{seg['id']:02d}.mp4"
        direction = directions[(seg['id'] - 1) % len(directions)]

        # 计算此片段的时长
        seg_duration = (len(seg['text']) / total_chars) * sum(s['duration'] for s in segments)
        seg_duration = max(seg_duration, 3.0)

        success = create_kenburns_clip(
            img_result['image_path'], clip_path,
            seg_duration, W, H, fps, ffmpeg, direction
        )
        if success:
            clip_files.append(str(clip_path))
            print(f"      ✓ 片段{seg['id']}: {direction}, {seg_duration:.1f}s")
        else:
            print(f"      ✗ 片段{seg['id']}创建失败")

    if not clip_files:
        raise RuntimeError("没有成功创建任何视频片段!")

    # 2. 拼接所有片段(带淡入淡出转场)
    print("   [2/5] 拼接片段并添加转场...")
    concat_path = output_dir / 'concat_video.mp4'

    # 创建concat文件列表
    concat_list = output_dir / 'concat_list.txt'
    with open(concat_list, 'w', encoding='utf-8') as f:
        for cf in clip_files:
            f.write(f"file '{cf}'\n")

    # 先用concat拼接, 再统一加淡入淡出
    cmd = [
        ffmpeg, '-y',
        '-f', 'concat', '-safe', '0',
        '-i', str(concat_list),
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
        '-pix_fmt', 'yuv420p',
        str(concat_path),
    ]
    subprocess.run(cmd, capture_output=True, timeout=300, **NW)

    # 3. 叠加音频
    print("   [3/5] 叠加TTS音频...")
    with_audio_path = output_dir / 'video_with_audio.mp4'
    cmd = [
        ffmpeg, '-y',
        '-i', str(concat_path),
        '-i', str(audio_path),
        '-c:v', 'copy',
        '-c:a', 'aac', '-b:a', '128k',
        '-shortest',
        '-map', '0:v:0', '-map', '1:a:0',
        str(with_audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, **NW)

    # 获取最终视频时长
    probe_cmd = [ffmpeg, '-i', str(with_audio_path), '-f', 'null', '-']
    probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30, **NW)
    dur_match = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.?\d*)', probe_result.stderr)
    if dur_match:
        h, m, s = dur_match.groups()
        video_duration = int(h) * 3600 + int(m) * 60 + float(s)
    else:
        video_duration = sum(s['duration'] for s in segments)

    # 4. 生成字幕文件
    print("   [4/5] 生成字幕...")
    srt_path = output_dir / 'subtitles.srt'
    generate_srt(segments, video_duration, srt_path)

    # 5. 添加标题栏 + 字幕
    print("   [5/5] 添加标题栏和字幕...")

    # 先加标题栏
    titled_path = output_dir / 'video_titled.mp4'
    title_ok = add_title_bar(with_audio_path, titled_path, title, W, H, bar_h, ffmpeg)

    # 再加字幕
    final_path = output_dir / 'final_video.mp4'
    source = titled_path if title_ok else with_audio_path
    sub_ok = add_subtitles(source, srt_path, final_path, W, H, ffmpeg, font_size)

    if not sub_ok:
        # 字幕添加失败, 用无字幕版本
        print("      ⚠ 字幕渲染失败, 输出无字幕版本")
        if source != final_path:
            shutil.copy2(source, final_path)

    # 清理中间文件
    for tmp in [concat_path, with_audio_path, titled_path, concat_list]:
        if tmp.exists() and tmp != final_path:
            try:
                os.remove(tmp)
            except:
                pass

    return final_path


# ============ 主流程 ============

def main():
    if len(sys.argv) < 2:
        print("用法: python video_pipeline.py <口播文案文件>")
        print("示例: python video_pipeline.py 01_rewritten_narration.txt")
        sys.exit(1)

    # 加载配置
    script_dir = Path(__file__).parent
    config = load_config(script_dir)
    ffmpeg = config['ffmpeg_path']

    # 检查ffmpeg
    if not os.path.exists(ffmpeg):
        print(f"错误: ffmpeg不存在于 {ffmpeg}")
        sys.exit(1)

    # 读取文案
    narration_file = Path(sys.argv[1])
    if not narration_file.exists():
        print(f"错误: 文案文件不存在: {narration_file}")
        sys.exit(1)

    text = narration_file.read_text(encoding='utf-8').strip()

    # 提取标题和正文
    title_match = re.search(r'【标题】\s*\n(.+)', text)
    body_match = re.search(r'【优化口播文案】\s*\n([\s\S]+)', text)
    if title_match and body_match:
        title = title_match.group(1).strip()
        narration = body_match.group(1).strip()
    else:
        # 没有格式标记, 全文作为口播文案
        title = narration_file.stem[:10]
        narration = text

    print(f"{'='*60}")
    print(f" 抖音短视频自动化流水线")
    print(f"{'='*60}")
    print(f"标题: {title}")
    print(f"文案长度: {len(narration)} 字")
    print(f"预估时长: {len(narration)/3.5/60:.1f} 分钟")

    # 输出目录
    output_dir = ensure_dir(script_dir / 'output')
    print(f"输出目录: {output_dir}")

    # 步骤1: 分镜切分
    print(f"\n[步骤1] 文案分镜切分...")
    segments = split_narration(narration, config.get('num_shots', 10))
    total_est = sum(s['duration'] for s in segments)
    print(f"   共 {len(segments)} 个分镜, 预估总时长 {total_est:.0f} 秒")
    for seg in segments:
        preview = seg['text'][:30].replace('\n', ' ')
        print(f"   镜{seg['id']:2d} ({seg['duration']:5.1f}s): {preview}...")

    # 保存分镜脚本
    storyboard_path = output_dir / 'storyboard.json'
    with open(storyboard_path, 'w', encoding='utf-8') as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)

    # 步骤2: 搜索实拍图片
    print(f"\n[步骤2] 搜索实拍图片...")
    image_results = search_and_download_images(segments, config, output_dir)
    print(f"   成功获取 {len(image_results)}/{len(segments)} 张图片")

    # 步骤3: TTS语音合成
    print(f"\n[步骤3] TTS语音合成 ({config['tts_voice']})...")
    audio_path = output_dir / 'narration.mp3'
    audio_path, _ = generate_tts(narration, audio_path, config['tts_voice'], config.get('tts_rate', '-5%'))
    print(f"   音频已保存: {audio_path}")

    # 步骤4: 视频合成
    print(f"\n[步骤4] 视频合成...")
    final_path = synthesize_video(
        segments, image_results, audio_path, title, config, output_dir
    )

    # 重命名最终文件
    final_name = output_dir / f"{title}.mp4"
    if final_path.exists():
        if final_name.exists():
            os.remove(final_name)
        os.rename(final_path, final_name)
        final_path = final_name

    print(f"\n{'='*60}")
    print(f" ✓ 视频生成完成!")
    print(f" 文件: {final_path}")
    print(f"{'='*60}")

    return str(final_path)


if __name__ == '__main__':
    main()
