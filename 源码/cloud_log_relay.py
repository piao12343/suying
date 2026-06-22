import argparse
import json
import os
import sys
import time
import urllib.request


def debug_enabled():
    return os.environ.get("SUYING_DEBUG_LOGS", "").lower() == "true"


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
            "lines": lines,
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
    buffer = []
    last_flush = time.time()
    post_log(["GitHub Actions 已进入视频处理步骤。"], "running")

    for line in sys.stdin:
        text = line.rstrip("\n")
        print(text, flush=True)
        buffer.append(text)
        now = time.time()
        if len(buffer) >= 20 or now - last_flush >= 3:
            post_log(buffer, "running")
            buffer = []
            last_flush = now

    if buffer:
        post_log(buffer, "running")


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

    stream_stdin()


if __name__ == "__main__":
    main()
