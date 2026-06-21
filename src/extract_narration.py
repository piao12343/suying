"""
抖音视频文案提取工具
用法: python extract_narration.py "抖音分享链接"
功能: 粘贴抖音分享链接 → 自动下载视频 → 提取音频 → 语音识别 → 输出口播文案
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

# 隐藏子进程控制台窗口
NW = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}

# 关闭SSL警告（抖音短链接SSL握手有时不稳定）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============ 配置 ============
_BASE = Path(__file__).resolve().parent.parent

# ffmpeg 路径: 优先环境变量, 回退到系统 PATH
FFMPEG_PATH = os.environ.get('FFMPEG_PATH', 'ffmpeg')

WHISPER_MODEL = "small"  # small模型对中文效果好，首次运行会自动下载(~500MB)

# 缓存目录: 优先环境变量, 回退到仓库根目录下 cache/
_CACHE_BASE = Path(os.environ.get('SUYING_CACHE_DIR', str(_BASE / 'cache')))
WORK_DIR = _CACHE_BASE
WORK_DIR.mkdir(parents=True, exist_ok=True)

# 模型缓存(首次运行自动下载)
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
    """从分享文本中提取抖音链接"""
    patterns = [
        r'https?://v\.douyin\.com/[A-Za-z0-9\-_]+/?',
        r'https?://www\.douyin\.com/video/\d+',
        r'https?://www\.iesdouyin\.com/share/video/\d+',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0)
    # 尝试提取任何 http 链接
    m = re.search(r'https?://\S+', text)
    if m:
        return m.group(0).rstrip('/')
    return text.strip()


def resolve_video_id(share_url):
    """解析短链接获取视频ID"""
    print(f"[1/4] 解析抖音链接...")
    resp = requests.get(share_url, headers=HEADERS, allow_redirects=True, timeout=30, verify=False)
    final_url = resp.url
    # 从URL中提取视频ID
    vid_match = re.search(r'/video/(\d+)', final_url)
    if vid_match:
        return vid_match.group(1)
    # 尝试从URL路径末尾提取
    vid = final_url.split('?')[0].strip('/').split('/')[-1]
    if vid.isdigit():
        return vid
    return None


def get_video_info(video_id):
    """获取视频信息（标题、描述、无水印地址）"""
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
    item = loader[pk]['videoInfoRes']['item_list'][0]

    desc = item.get('desc', '')
    nickname = item.get('author', {}).get('nickname', '未知')

    # 获取无水印视频地址
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
    """下载视频并提取音频"""
    print(f"[3/4] 下载视频并提取音频...")
    # 直接用 ffmpeg 下载并提取音频，一步到位
    cmd = [
        FFMPEG_PATH,
        '-y',
        '-headers', f'User-Agent: {HEADERS["User-Agent"]}\r\nReferer: {HEADERS["Referer"]}\r\n',
        '-i', video_url,
        '-vn',           # 不要视频
        '-acodec', 'pcm_s16le',  # WAV格式，faster-whisper识别效果更好
        '-ar', '16000',  # 16kHz采样率，Whisper标准
        '-ac', '1',      # 单声道
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, **NW)
    if result.returncode != 0:
        print(f"ffmpeg 错误: {result.stderr[-500:]}")
        raise Exception("音频提取失败")
    print(f"   音频已保存: {output_path}")


def transcribe_audio(audio_path):
    """使用 faster-whisper 进行语音识别"""
    print(f"[4/4] 语音识别中（首次运行需下载模型，请耐心等待）...")
    from faster_whisper import WhisperModel

    # small 模型对中文效果好，CPU也能跑
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(audio_path),
        language="zh",
        beam_size=5,
        vad_filter=True,  # 过滤静音片段，提高准确度
    )

    print(f"   检测到语言: {info.language} (概率: {info.language_probability:.2f})")
    print(f"   音频时长: {info.duration:.1f}秒")

    full_text = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            full_text.append(text)
            # 实时显示进度
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

    # 1. 解析视频ID
    video_id = resolve_video_id(share_url)
    if not video_id:
        print("错误: 无法从链接中解析视频ID")
        sys.exit(1)

    # 2. 获取视频信息
    info = get_video_info(video_id)
    print(f"   作者: {info['author']}")
    print(f"   描述: {info['desc'][:100]}...")
    if not info['video_url']:
        print("错误: 无法获取视频下载地址")
        sys.exit(1)

    # 3. 下载并提取音频
    audio_path = WORK_DIR / f"audio_{video_id}.wav"
    try:
        download_and_extract_audio(info['video_url'], audio_path)
    except Exception as e:
        print(f"下载失败: {e}")
        sys.exit(1)

    # 4. 语音识别
    try:
        narration = transcribe_audio(audio_path)
    except Exception as e:
        print(f"语音识别失败: {e}")
        sys.exit(1)

    # 5. 输出结果
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

    # 清理音频临时文件
    if audio_path.exists():
        os.remove(audio_path)

    return narration


if __name__ == '__main__':
    main()
