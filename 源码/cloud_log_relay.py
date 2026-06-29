import argparse
import json
import os
import re
import sys
import threading
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
    "Error opening input",
    "HTTP 403",
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
    "TTS",
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
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
TIME_PREFIX_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def debug_enabled():
    return os.environ.get("SUYING_DEBUG_LOGS", "").lower() == "true"


def add_beijing_time(line):
    text = str(line)
    if TIME_PREFIX_RE.match(text):
        return text
    ts = datetime.now(BEIJING_TZ).strftime("%H:%M:%S")
    return f"[{ts}] {text}"


def clean_line(line):
    return ANSI_RE.sub("", str(line)).strip()


def is_failure_line(text):
    return any(pattern in text for pattern in FAILURE_PATTERNS)


def is_important_line(text):
    return any(pattern in text for pattern in IMPORTANT_PATTERNS) or is_failure_line(text)


def is_urgent_line(text):
    return any(pattern in text for pattern in URGENT_PATTERNS) or is_failure_line(text)


def should_keep_line(text):
    if not text:
        return False
    if is_important_line(text):
        return True
    if any(pattern in text for pattern in NOISE_PATTERNS):
        return False
    if text.startswith(("Run ", "[command]/", "hint:", "remote:", "From https://")):
        return False
    return False


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
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def add(self, line):
        with self.lock:
            self.pending.append(line)

    def add_urgent(self, line, status="running"):
        self.flush()
        post_log([line], status)

    def flush(self, status="running"):
        with self.lock:
            if not self.pending:
                return
            lines = self.pending
            self.pending = []
        post_log(lines, status)

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=1)
        self.flush()

    def _run(self):
        while not self.stop_event.wait(self.interval_seconds):
            self.flush()


def stream_stdin():
    saw_failure = False
    buffer = TimedLogBuffer()
    post_log(["GitHub Actions 已进入视频处理步骤。"], "running")

    try:
        for line in sys.stdin:
            raw = line.rstrip("\n")
            print(raw, flush=True)
            text = clean_line(raw)
            if not should_keep_line(text):
                continue
            if is_failure_line(text):
                saw_failure = True
                buffer.add_urgent(text, "failed")
            elif is_urgent_line(text):
                buffer.add_urgent(text, "running")
            else:
                buffer.add(text)
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
