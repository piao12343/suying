import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


FAILURE_PATTERNS = (
    "发布失败",
    "处理失败",
    "任务失败",
    "cookie文件已失效",
    "流水线错误",
    "音频提取失败",
    "Traceback",
    "Exception:",
)
IMPORTANT_PATTERNS = (
    "GitHub Actions 已取到链接",
    "系统依赖安装完成",
    "发布组件准备完成",
    "抖音 cookie 写入完成",
    "GitHub Actions 已进入视频处理步骤",
    "[步骤",
    "[1/4]",
    "[2/4]",
    "[3/4]",
    "[4/4]",
    "链接:",
    "作者:",
    "文案长度",
    "模型:",
    "标题:",
    "改写文案",
    "分镜",
    "搜索词",
    "AI关键词",
    "音频已保存",
    "音频提取完成",
    "语音识别完成",
    "TTS语音合成",
    "词边界",
    "视频渲染",
    "Ken Burns",
    "拼接片段",
    "叠加TTS音频",
    "渲染标题封面",
    "生成智能字幕",
    "渲染字幕",
    "完成!",
    "大小:",
    "竖封面",
    "横封面",
    "自动发布到抖音",
    "方式:",
    "发布超时限制",
    "发布成功",
    "发布失败",
    "上传",
    "封面",
    "确认",
    "确定",
    "定时",
    "发布按钮",
    "云端任务完成",
    "云端任务失败",
)
URGENT_PATTERNS = (
    "自动发布到抖音",
    "发布超时限制",
    "发布成功",
    "发布失败",
    "上传",
    "封面",
    "确认",
    "确定",
    "定时",
    "发布按钮",
    "云端任务完成",
    "云端任务失败",
)
NOISE_PATTERNS = (
    "Current runner version",
    "Runner Image",
    "Operating System",
    "GITHUB_TOKEN Permissions",
    "Prepare workflow directory",
    "Download action repository",
    "Node 20 is being deprecated",
    "Node.js 20 is deprecated",
    "DeprecationWarning",
    "Run actions/",
    "##[group]",
    "##[endgroup]",
    "with:",
    "env:",
    "SUYING_",
    "GITHUB_",
    "FFMPEG_PATH:",
    "SAU_DIR:",
    "shell:",
    "pythonLocation:",
    "PKG_CONFIG_PATH:",
    "Python_ROOT_DIR:",
    "LD_LIBRARY_PATH:",
    "Cache hit",
    "Cache Size:",
    "Received ",
    "Uploaded bytes",
    "Artifact ",
    "Beginning upload of artifact",
    "Finished uploading artifact",
    "SHA256 digest",
    "Installing collected packages:",
    "Successfully installed",
    "Requirement already satisfied",
    "Collecting ",
    "Using cached ",
    "Downloading ",
    "Attempting uninstall:",
    "Found existing installation:",
    "Uninstalling ",
    "Successfully uninstalled",
    "pip's dependency resolver",
    "libavutil",
    "libavcodec",
    "libavformat",
    "libavdevice",
    "libavfilter",
    "libswscale",
    "libswresample",
    "libpostproc",
    "configuration:",
    "built with",
)
FLUSH_INTERVAL_SECONDS = 5.0
HEARTBEAT_INTERVAL_SECONDS = 300.0
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
TIME_PREFIX_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
TEXT_ANSI_RE = re.compile(r"\^\[\[[0-9;?]*[ -/]*[@-~]")
GITHUB_TS_PREFIX_RE = re.compile(r"^[^	]*	[^	]*	\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s*")
LOGURU_MESSAGE_RE = re.compile(r"(?:INFO|SUCCESS|WARNING|ERROR):\s*(.+)$")
STEP_RE = re.compile(r"\[步骤(\d)/7\]\s*(.+)")
TITLE_RE = re.compile(r"标题:\s*(.+)")
AUTHOR_RE = re.compile(r"作者:\s*(.+)")
LINK_RE = re.compile(r"链接:\s*(https?://\S+)")
NARRATION_LEN_RE = re.compile(r"文案长度:\s*(\d+)\s*字")
ASR_DONE_RE = re.compile(r"语音识别完成,?\s*用时\s*([0-9.]+)\s*秒")
REWRITE_LEN_RE = re.compile(r"改写文案:\s*(\d+)\s*字")
SEGMENT_COUNT_RE = re.compile(r"共\s*(\d+)\s*个分镜")
AUDIO_DURATION_RE = re.compile(r"音频基准时长:\s*([0-9.]+)s")
VIDEO_SIZE_RE = re.compile(r"大小:\s*([0-9.]+)\s*MB")


def debug_enabled():
    return os.environ.get("SUYING_DEBUG_LOGS", "").lower() == "true"


def add_beijing_time(line):
    text = str(line)
    if TIME_PREFIX_RE.match(text):
        return text
    ts = datetime.now(BEIJING_TZ).strftime("%H:%M:%S")
    return f"[{ts}] {text}"


def clean_line(line):
    text = TEXT_ANSI_RE.sub("", ANSI_RE.sub("", str(line))).strip()
    text = GITHUB_TS_PREFIX_RE.sub("", text).strip()
    if "\t" in text:
        text = text.split("\t")[-1].strip()
    text = TEXT_ANSI_RE.sub("", ANSI_RE.sub("", text)).strip()
    m = LOGURU_MESSAGE_RE.search(text)
    if m:
        text = m.group(1).strip()
    return text


def is_failure_line(text):
    return any(pattern in text for pattern in FAILURE_PATTERNS)


def is_recoverable_line(text):
    return (
        "备用下载尝试" in text
        or "ffmpeg直连失败" in text
        or "Error opening input file" in text
        or "Error opening input files" in text
        or "Error opening input:" in text
        or "Server returned 403 Forbidden" in text
        or "HTTP error 403 Forbidden" in text
    )


def is_important_line(text):
    return any(pattern in text for pattern in IMPORTANT_PATTERNS) or is_failure_line(text)


def is_urgent_line(text):
    return any(pattern in text for pattern in URGENT_PATTERNS) or is_failure_line(text)


def should_keep_line(text):
    if not text:
        return False
    if any(pattern in text for pattern in NOISE_PATTERNS):
        return False
    if text.startswith(("Run ", "[command]/", "hint:", "remote:", "From https://", "python ")):
        return False
    if "douyin_logger." in text or "_msg(" in text:
        return False
    if text.startswith("^[["):
        return False
    if "cloud_log_relay.py --line" in text:
        return False
    if "源码/cloud_log_relay.py" in text:
        return False
    if is_important_line(text):
        return True
    return False


class LogSummarizer:
    def __init__(self):
        self.current_step = None
        self.current_status = ""
        self.link = ""
        self.author = ""
        self.title = ""
        self.asr_seconds = ""
        self.narration_chars = ""
        self.rewrite_chars = ""
        self.segment_count = ""
        self.audio_duration = ""
        self.video_size = ""
        self.seen_search_done = False
        self.seen_publish_upload = False

    def consume(self, text):
        outputs = []
        urgent = False

        step_match = STEP_RE.search(text)
        if step_match:
            step_num = int(step_match.group(1))
            step_name = step_match.group(2).strip().rstrip(".。")
            self.current_step = step_num
            if step_num == 1:
                status = "步骤1/7 正在提取原视频文案"
                outputs.append("步骤1/7：正在提取原视频文案")
            elif step_num == 2:
                status = "步骤2/7 正在 AI 改写文案"
                outputs.append("步骤2/7：正在 AI 改写文案")
            elif step_num == 3:
                status = "步骤3/7 正在按故事情节分镜"
                outputs.append("步骤3/7：正在按故事情节分镜")
            elif step_num == 4:
                status = "步骤4/7 正在搜索配图"
                outputs.append("步骤4/7：正在搜索配图")
            elif step_num == 5:
                status = "步骤5/7 正在合成配音"
                outputs.append("步骤5/7：正在合成配音")
            elif step_num == 6:
                status = "步骤6/7 正在渲染视频"
                outputs.append("步骤6/7：正在渲染视频")
            elif step_num == 7:
                status = "步骤7/7 正在发布到抖音"
                outputs.append("步骤7/7：正在发布到抖音")
                urgent = True
            else:
                status = f"步骤{step_num}/7 {step_name}"
                outputs.append(f"步骤{step_num}/7：{step_name}")
            self.current_status = status
            return outputs, urgent

        link_match = LINK_RE.search(text)
        if link_match:
            self.link = link_match.group(1)
            return [], False

        author_match = AUTHOR_RE.search(text)
        if author_match:
            self.author = author_match.group(1).strip()
            if self.link:
                outputs.append(f"原视频信息：作者 {self.author}，链接 {self.link}")
            else:
                outputs.append(f"原视频信息：作者 {self.author}")
            return outputs, False

        asr_match = ASR_DONE_RE.search(text)
        if asr_match:
            self.asr_seconds = asr_match.group(1)
            return [], False

        narration_match = NARRATION_LEN_RE.search(text)
        if narration_match:
            self.narration_chars = narration_match.group(1)
            if self.asr_seconds:
                outputs.append(f"步骤1完成：语音识别完成，文案长度 {self.narration_chars} 字，用时 {self.asr_seconds} 秒")
            else:
                outputs.append(f"步骤1完成：语音识别完成，文案长度 {self.narration_chars} 字")
            return outputs, False

        title_match = TITLE_RE.search(text)
        if title_match:
            self.title = title_match.group(1).strip()
            if self.current_step == 2:
                return [], False
            if self.current_step == 7:
                outputs.append(f"发布信息：标题 {self.title}")
                urgent = True
            return outputs, urgent

        rewrite_match = REWRITE_LEN_RE.search(text)
        if rewrite_match:
            self.rewrite_chars = rewrite_match.group(1)
            title_part = f"，标题《{self.title}》" if self.title else ""
            outputs.append(f"步骤2完成：AI 改写完成{title_part}，文案 {self.rewrite_chars} 字")
            return outputs, False

        segment_match = SEGMENT_COUNT_RE.search(text)
        if segment_match:
            self.segment_count = segment_match.group(1)
            outputs.append(f"步骤3完成：已分成 {self.segment_count} 个分镜")
            return outputs, False

        if "AI关键词提取完成" in text or "AI成功提取" in text:
            if not self.seen_search_done:
                self.seen_search_done = True
                self.current_status = "步骤4/7 正在下载配图"
                outputs.append("步骤4进度：配图关键词已生成")
            return outputs, False

        if "TTS语音合成" in text:
            return [], False

        if "词边界:" in text:
            self.current_status = "步骤5/7 配音已生成，等待进入视频渲染"
            outputs.append("步骤5完成：配音已生成")
            return outputs, False

        audio_match = AUDIO_DURATION_RE.search(text)
        if audio_match:
            self.audio_duration = audio_match.group(1)
            self.current_status = "步骤6/7 正在渲染视频"
            outputs.append(f"步骤6进度：开始渲染，音频时长 {self.audio_duration} 秒")
            return outputs, False

        if any(part in text for part in ("[6b]", "[6c]", "[6d]", "[6e]", "[6f]")):
            label = text.split("]", 1)[-1].strip() if "]" in text else text
            self.current_status = f"步骤6/7 {label}"
            outputs.append(f"步骤6进度：{label}")
            return outputs, False

        size_match = VIDEO_SIZE_RE.search(text)
        if size_match:
            self.video_size = size_match.group(1)
            outputs.append(f"步骤6完成：视频已生成，大小 {self.video_size} MB")
            return outputs, False

        if "方式:" in text:
            outputs.append(text.strip())
            return outputs, True

        if "发布超时限制" in text:
            outputs.append(text.strip())
            return outputs, True

        if "上传前检查通过" in text:
            self.current_status = "步骤7/7 上传前检查通过"
            outputs.append("发布进度：上传前检查通过")
            return outputs, True

        if "正在赶往上传主页" in text:
            self.current_status = "步骤7/7 正在打开抖音上传页"
            outputs.append("发布进度：正在打开抖音上传页")
            return outputs, True

        if "正在努力上传视频" in text:
            if not self.seen_publish_upload:
                self.seen_publish_upload = True
                self.current_status = "步骤7/7 正在上传视频"
                outputs.append("发布进度：正在上传视频")
            return outputs, True

        if "正在设置视频封面" in text:
            self.current_status = "步骤7/7 正在设置封面"
            outputs.append("发布进度：正在设置封面")
            return outputs, True

        if "封面" in text and ("上传" in text or "关闭" in text or "完成" in text):
            self.current_status = "步骤7/7 正在处理封面"
            outputs.append(f"发布进度：{text}")
            return outputs, True

        if "视频发布成功" in text or "发布成功" in text:
            self.current_status = ""
            outputs.append("步骤7完成：抖音发布成功")
            return outputs, True

        if "云端任务完成" in text:
            self.current_status = ""
            outputs.append("云端任务完成")
            return outputs, True

        if is_recoverable_line(text):
            return [], False

        if is_failure_line(text):
            self.current_status = ""
            outputs.append(text)
            return outputs, True

        return [], False


def post_log(lines, status="running", reset=False):
    if not debug_enabled():
        return

    base_url = os.environ.get("SUYING_LISTENER_WORKER_URL", "").rstrip("/")
    secret = os.environ.get("SUYING_LISTENER_SECRET", "")
    if not base_url or not secret:
        return

    payload = json.dumps(
        {
            "secret": secret,
            "status": status,
            "lines": [add_beijing_time(line) for line in lines],
            "reset": reset,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        base_url + "/api/log",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "suying-log-relay"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except Exception:
        # Debug logs must never break the real video workflow.
        return


class TimedLogBuffer:
    def __init__(self, interval_seconds=FLUSH_INTERVAL_SECONDS):
        self.interval_seconds = interval_seconds
        self.pending = []
        self.current_status = ""
        self.next_heartbeat_at = time.monotonic() + HEARTBEAT_INTERVAL_SECONDS
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def set_status(self, status):
        with self.lock:
            if status != self.current_status:
                self.current_status = status
                self.next_heartbeat_at = time.monotonic() + HEARTBEAT_INTERVAL_SECONDS

    def add(self, line):
        with self.lock:
            self.pending.append(line)

    def add_urgent(self, line, status="running"):
        self.flush()
        post_log([line], status)
        self._mark_emitted()

    def flush(self, status="running"):
        with self.lock:
            if not self.pending:
                return
            lines = self.pending
            self.pending = []
        post_log(lines, status)
        self._mark_emitted()

    def maybe_post_heartbeat(self):
        now = time.monotonic()
        line = None
        with self.lock:
            if self.current_status and now >= self.next_heartbeat_at:
                line = f"仍在运行：{self.current_status}，暂无新进度，已继续等待约 5 分钟"
                self.next_heartbeat_at = now + HEARTBEAT_INTERVAL_SECONDS
        if line:
            post_log([line], "running")

    def _mark_emitted(self):
        with self.lock:
            self.next_heartbeat_at = time.monotonic() + HEARTBEAT_INTERVAL_SECONDS

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=1)
        self.flush()

    def _run(self):
        while not self.stop_event.wait(self.interval_seconds):
            self.flush()
            self.maybe_post_heartbeat()


def stream_stdin():
    saw_failure = False
    buffer = TimedLogBuffer()
    last_kept = ""
    summarizer = LogSummarizer()
    post_log(["GitHub Actions 已进入视频处理步骤。"], "running")

    try:
        for line in sys.stdin:
            raw = line.rstrip("\n")
            print(raw, flush=True)
            text = clean_line(raw)
            if not should_keep_line(text):
                continue
            if is_failure_line(text) and not is_recoverable_line(text):
                saw_failure = True
            outputs, urgent = summarizer.consume(text)
            buffer.set_status(summarizer.current_status)
            for output in outputs:
                if output == last_kept:
                    continue
                last_kept = output
                if is_failure_line(output):
                    buffer.add_urgent(output, "failed")
                elif urgent or is_urgent_line(output):
                    buffer.add_urgent(output, "running")
                else:
                    buffer.add(output)
    finally:
        buffer.close()

    if saw_failure:
        post_log(["检测到失败日志, 标记云端任务失败。"], "failed")
        return 2
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--line", help="Post one status line and exit.")
    parser.add_argument("--status", default="running")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    if args.line:
        print(args.line, flush=True)
        post_log([args.line], args.status, args.reset)
        return

    sys.exit(stream_stdin())


if __name__ == "__main__":
    main()
