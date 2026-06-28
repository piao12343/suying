import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


FAILURE_PATTERNS = (
    "发布失败",
    "处理失败",
    "任务失败",
    "cookie文件已失效",
    "Traceback",
)
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
TIME_PREFIX_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]")


def debug_enabled():
    return os.environ.get("SUYING_DEBUG_LOGS", "").lower() == "true"


def add_beijing_time(line):
    text = str(line)
    if TIME_PREFIX_RE.match(text):
        return text
    ts = datetime.now(BEIJING_TZ).strftime("%H:%M:%S")
    return f"[{ts}] {text}"


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


def stream_stdin():
    saw_failure = False
    post_log(["GitHub Actions 已进入视频处理步骤。"], "running")

    for line in sys.stdin:
        text = line.rstrip("\n")
        print(text, flush=True)
        if any(pattern in text for pattern in FAILURE_PATTERNS):
            saw_failure = True
        post_log([text], "running")

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
