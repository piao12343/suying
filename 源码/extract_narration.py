"""
Douyin narration extraction tool
Usage: python extract_narration.py "douyin_share_link"
"""

import sys
import os
import re
import json
import requests
import subprocess
import tempfile
import urllib3
from pathlib import Path

# Hide subprocess console window
NW = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}

# Suppress SSL warnings (Douyin short links have unstable SSL)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============ Config ============
_BASE = Path(__file__).resolve().parent.parent

# ffmpeg path: env var > system PATH
FFMPEG_PATH = os.environ.get('FFMPEG_PATH', 'ffmpeg')

WHISPER_MODEL = "small"  # small model works well for Chinese, auto-downloads on first run (~500MB)

# Cache dir: env var > repo cache/
_CACHE_BASE = Path(os.environ.get('SUYING_CACHE_DIR', str(_BASE / 'cache')))
WORK_DIR = _CACHE_BASE
WORK_DIR.mkdir(parents=True, exist_ok=True)

# Model cache (auto-downloads on first run)
if not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = str(_CACHE_BASE / 'hf_models')
    os.environ["HF_HUB_CACHE"] = str(_CACHE_BASE / 'hf_models' / 'hub')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) '
                  'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 '
                  'Mobile/15E148 Safari/604.1',
    'Referer': 'https://www.douyin.com/',
}


def extract_share_url(text):
    """Extract Douyin URL from share text"""
    patterns = [
        r'https?://v\.douyin\.com/[A-Za-z0-9\-_]+/?',
        r'https?://www\.douyin\.com/video/\d+',
        r'https?://www\.iesdouyin\.com/share/video/\d+',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0)
    # Try extracting any HTTP link
    m = re.search(r'https?://\S+', text)
    if m:
        return m.group(0).rstrip('/')
    return text.strip()


def resolve_video_id(share_url):
    """Resolve short link to get video ID"""
    print(f"[1/4] 解析抖音链接...")
    resp = requests.get(share_url, headers=HEADERS, allow_redirects=True, timeout=30, verify=False)
    final_url = resp.url
    # Extract video ID from URL
    vid_match = re.search(r'/video/(\d+)', final_url)
    if vid_match:
        return vid_match.group(1)
    # Try extracting from URL path end
    vid = final_url.split('?')[0].strip('/').split('/')[-1]
    if vid.isdigit():
        return vid
    return None


def get_video_info(video_id):
    """Get video info (title, desc, watermark-free URL)"""
    print(f"[2/4] 获取视频信息 (ID: {video_id})...")
    url = f'https://www.iesdouyin.com/share/video/{video_id}'
    resp = requests.get(url, headers=HEADERS, timeout=30, verify=False)
    text = resp.text

    idx = text.find('_ROUTER_DATA')
    if idx == -1:
        raise Exception("无法解析视频页面，未找到 _ROUTER_DATA")

    script_start = text.rfind('<script>', 0, idx)
    script_end = text.find('</script>', idx)
    content = text[script_start + 8:script_end]
    eq_idx = content.find('=')
    json_str = content[eq_idx + 1:].strip()
    data = json.loads(json_str)

    loader = data['loaderData']
    pk = [k for k in loader if 'page' in k][0]
    video_info = loader[pk]['videoInfoRes']

    # Douyin returns different structures depending on IP/region/environment:
    #   - Local:  videoInfoRes.item_list (array)
    #   - Cloud:  videoInfoRes.aweme_detail (single object) or other keys
    if 'item_list' in video_info and video_info['item_list']:
        item = video_info['item_list'][0]
    elif 'aweme_detail' in video_info and video_info['aweme_detail']:
        item = video_info['aweme_detail']
    else:
        available_keys = list(video_info.keys())
        raise Exception(f"无法解析视频信息，可用字段: {available_keys}")

    desc = item.get('desc', '')
    nickname = item.get('author', {}).get('nickname', '未知')

    # Get watermark-free video URL
    play_addr = item.get('video', {}).get('play_addr', {})
    url_list = play_addr.get('url_list', [])
    video_url = url_list[0].replace('playwm', 'play') if url_list else None

    return {
        'video_id': video_id,
        'desc': desc,
        'author': nickname,
        'video_url': video_url,
    }


def download_and_extract_audio(video_url, output_path):
    """Download video and extract audio"""
    print(f"[3/4] 下载视频并提取音频...")
    # Use ffmpeg to download and extract audio in one step
    cmd = [
        FFMPEG_PATH,
        '-y',
        '-headers', f'User-Agent: {HEADERS["User-Agent"]}\r\nReferer: {HEADERS["Referer"]}\r\n',
        '-i', video_url,
        '-vn',           # no video
        '-acodec', 'pcm_s16le',  # WAV format, better for whisper
        '-ar', '16000',  # 16kHz, Whisper standard
        '-ac', '1',      # mono
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **NW)
    if result.returncode != 0:
        print(f"ffmpeg 错误: {result.stderr[-500:]}")
        raise Exception("音频提取失败")
    print(f"   音频已保存: {output_path}")


def transcribe_audio(audio_path):
    """Speech recognition via faster-whisper"""
    print(f"[4/4] 语音识别中（首次运行需下载模型，请耐心等待）...")
    from faster_whisper import WhisperModel

    # small model works well for Chinese, runs on CPU too
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(audio_path),
        language="zh",
        beam_size=5,
        vad_filter=True,  # filter silence segments for accuracy
    )

    print(f"   检测到语言: {info.language} (概率: {info.language_probability:.2f})")
    print(f"   音频时长: {info.duration:.1f}秒")

    full_text = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            full_text.append(text)
            # Show real-time progress
            progress = segment.end / info.duration * 100
            print(f"   [{progress:5.1f}%] {text}")

    return '\n'.join(full_text)


def main():
    if len(sys.argv) < 2:
        print("用法: python extract_narration.py <抖音分享链接>")
        print("示例: python extract_narration.py \"https://v.douyin.com/xxxxx/\"")
        sys.exit(1)

    input_text = sys.argv[1]
    share_url = extract_share_url(input_text)
    print(f"链接: {share_url}")

    # 1. Resolve video ID
    video_id = resolve_video_id(share_url)
    if not video_id:
        print("错误: 无法从链接中解析视频ID")
        sys.exit(1)

    # 2. Get video info
    info = get_video_info(video_id)
    print(f"   作者: {info['author']}")
    print(f"   描述: {info['desc'][:100]}...")
    if not info['video_url']:
        print("错误: 无法获取视频下载地址")
        sys.exit(1)

    # 3. Download and extract audio
    audio_path = WORK_DIR / f"audio_{video_id}.wav"
    try:
        download_and_extract_audio(info['video_url'], audio_path)
    except Exception as e:
        print(f"下载失败: {e}")
        sys.exit(1)

    # 4. Speech recognition
    try:
        narration = transcribe_audio(audio_path)
    except Exception as e:
        print(f"语音识别失败: {e}")
        sys.exit(1)

    # 5. Output results
    output_file = WORK_DIR / f"narration_{video_id}.txt"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"标题: {info['desc']}\n")
        f.write(f"作者: {info['author']}\n")
        f.write(f"视频ID: {info['video_id']}\n")
        f.write(f"{'='*50}\n")
        f.write(f"口播文案:\n")
        f.write(narration)

    print(f"\n{'='*50}")
    print(f"提取完成！文案已保存到: {output_file}")
    print(f"{'='*50}")
    print(f"\n{info['desc']}")
    print(f"{'─'*50}")
    print(narration)

    # Clean up temp audio file
    if audio_path.exists():
        os.remove(audio_path)

    return narration


if __name__ == '__main__':
    main()
