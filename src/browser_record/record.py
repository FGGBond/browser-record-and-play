"""bh-record — interactive browser operation recorder.

Records all network requests via CDP while the user operates the browser,
letting them mark steps with text descriptions. Saves a structured JSON file
for later AI analysis to extract API call patterns.

UI is built with prompt_toolkit so the live counter and the input prompt
never corrupt each other.
"""
import argparse, asyncio, json, os, sys, time, threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

# browser-harness internals
from browser_harness import _ipc as ipc
from browser_harness.admin import ensure_daemon, NAME


# ── prompt style (claude-code-like) ──────────────────────────────────────────

STYLE = Style.from_dict({
    "prompt":        "#00aaff bold",
    "prompt-arrow":  "#666666",
})


# ── ipc helpers ──────────────────────────────────────────────────────────────

def _send(req):
    c, token = ipc.connect(NAME, timeout=5.0)
    try:
        r = ipc.request(c, token, req)
    finally:
        c.close()
    if "error" in r:
        raise RuntimeError(r["error"])
    return r


def _drain_events():
    return _send({"meta": "drain_events"})["events"]


def _get_response_body(request_id):
    try:
        r = _send({"method": "Network.getResponseBody", "params": {"requestId": request_id}})
        result = r.get("result", {})
        body = result.get("body", "")
        if result.get("base64Encoded"):
            return {"base64": body}
        return body
    except Exception:
        return None


# ── utilities ─────────────────────────────────────────────────────────────────

def _hostname(url):
    try:
        h = urlparse(url).hostname or url
        return h.removeprefix("www.")
    except Exception:
        return url


def _fmt_elapsed(seconds):
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def _default_output_dir():
    d = Path.home() / ".browser-harness" / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _print_tab_summary():
    """Print active tab count and up to 3 domains, marking the attached tab."""
    try:
        targets = _send({"method": "Target.getTargets", "params": {}}).get("result", {}).get("targetInfos", [])
        current = _send({"meta": "current_tab"})
        attached_id = current.get("targetId")
    except Exception:
        return

    internal = ("chrome://", "chrome-untrusted://", "devtools://", "chrome-extension://", "about:")
    pages = [t for t in targets
             if t.get("type") == "page" and not t.get("url", "").startswith(internal)]
    total = len(pages)
    if total == 0:
        return

    print(f"  {total} active tab{'s' if total != 1 else ''}:")
    for t in pages[:3]:
        domain = _hostname(t.get("url", ""))
        marker = "  ◀ recording" if t.get("targetId") == attached_id else ""
        print(f"    {domain}{marker}")
    if total > 3:
        print(f"    ... and {total - 3} more")
    print()


# ── recorder core ─────────────────────────────────────────────────────────────

class Recorder:
    def __init__(self):
        self.steps = []
        self.pending = []
        self.all_requests = []
        self._responses = {}
        self._finished = {}
        self.start_time = None
        self._total = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def _poller(self):
        while not self._stop.is_set():
            try:
                events = _drain_events()
            except Exception:
                time.sleep(0.3)
                continue

            with self._lock:
                for ev in events:
                    method = ev.get("method", "")
                    params = ev.get("params", {})
                    rid = params.get("requestId")

                    if method == "Network.requestWillBeSent":
                        entry = {
                            "request_id": rid,
                            "method": params.get("request", {}).get("method", "GET"),
                            "url": params.get("request", {}).get("url", ""),
                            "request_headers": params.get("request", {}).get("headers", {}),
                            "request_body": params.get("request", {}).get("postData"),
                            "timestamp": params.get("timestamp", 0),
                            "response_status": None,
                            "response_headers": None,
                            "response_body": None,
                        }
                        self.pending.append(entry)
                        self.all_requests.append(entry)
                        self._total += 1

                    elif method == "Network.responseReceived" and rid:
                        resp = params.get("response", {})
                        self._responses[rid] = {
                            "status": resp.get("status"),
                            "headers": resp.get("headers", {}),
                        }
                        self._apply_response(rid)

                    elif method == "Network.loadingFinished" and rid:
                        self._finished[rid] = True

            time.sleep(0.3)

    def _apply_response(self, rid):
        resp = self._responses.get(rid)
        if not resp:
            return
        for entry in self.all_requests:
            if entry["request_id"] == rid:
                entry["response_status"] = resp["status"]
                entry["response_headers"] = resp["headers"]
                break

    def start(self):
        self.start_time = time.time()
        t = threading.Thread(target=self._poller, daemon=True)
        t.start()
        return t

    def mark_step(self, description):
        elapsed = time.time() - self.start_time
        with self._lock:
            start_at = self.steps[-1]["end_at"] if self.steps else 0.0
            step = {
                "index": len(self.steps) + 1,
                "description": description,
                "start_at": round(start_at, 3),
                "end_at": round(elapsed, 3),
                "requests": list(self.pending),
            }
            count = len(self.pending)
            self.pending = []
        self.steps.append(step)
        return step["index"], count, elapsed

    def finish(self):
        self._stop.set()
        elapsed = time.time() - self.start_time
        with self._lock:
            tail_requests = list(self.pending)
            total_requests = self._total
        self._enrich_response_bodies()
        recorded_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "version": "1",
            "recorded_at": recorded_at,
            "duration_s": round(elapsed, 1),
            "total_requests": total_requests,
            "steps": self.steps,
            "tail": {"description": None, "requests": tail_requests},
        }

    def _enrich_response_bodies(self):
        for entry in self.all_requests:
            rid = entry["request_id"]
            if not self._finished.get(rid):
                continue
            url = entry.get("url", "")
            if any(url.endswith(ext) for ext in (
                ".png", ".jpg", ".jpeg", ".gif", ".svg",
                ".woff", ".woff2", ".ttf", ".eot", ".css", ".ico"
            )):
                continue
            resp_headers = entry.get("response_headers") or {}
            content_type = next(
                (v for k, v in resp_headers.items() if k.lower() == "content-type"), ""
            )
            if not any(t in content_type for t in ("json", "text", "form")):
                continue
            body = _get_response_body(rid)
            if body is not None:
                entry["response_body"] = body

    @property
    def total(self):
        return self._total


# ── async interactive loop ────────────────────────────────────────────────────

async def _live_printer(rec: Recorder, stop_event: asyncio.Event):
    """Prints the live counter every 300ms above the prompt via patch_stdout."""
    prev = -1
    while not stop_event.is_set():
        n = rec.total
        if n != prev:
            print(f"\r\033[K  [live] {n} requests", flush=True)
            prev = n
        await asyncio.sleep(0.3)


async def _run_async(output_dir: Path):
    rec = Recorder()
    rec.start()

    print("\nRecording started")
    _print_tab_summary()
    print('  输入 "done" 或 "stop" 结束录制\n')

    session = PromptSession()
    stop_live = asyncio.Event()
    step_count = 0

    with patch_stdout():
        asyncio.create_task(_live_printer(rec, stop_live))

        try:
            while True:
                try:
                    line = await session.prompt_async(
                        HTML("<prompt>描述已完成的操作</prompt> <prompt-arrow"
                             ">>>> </prompt-arrow>"),
                        style=STYLE,
                    )
                except (EOFError, KeyboardInterrupt):
                    print("\n中断录制，保存已有内容...")
                    break

                text = line.strip()
                if not text:
                    continue
                if text.lower() in ("done", "stop"):
                    break

                idx, count, elapsed = rec.mark_step(text)
                step_count += 1
                print(f'\n✓ MARKED STEP {idx} @ {_fmt_elapsed(elapsed)} "{text}"')
                print(f"  └─ {count} requests recorded\n")

        finally:
            stop_live.set()

    data = rec.finish()
    tail_count = len(data["tail"]["requests"])
    total_requests = data["total_requests"]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"recording_{ts}.json"
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    print(f"\nRecording saved → {out_path}")
    print(f"  {step_count} steps · {total_requests} requests · {_fmt_elapsed(data['duration_s'])}")
    if tail_count:
        print(f"  ({tail_count} requests after last step saved in 'tail')")


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="bh-record",
        description="Record browser network requests with step annotations.",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="DIR",
        help="Directory to save the recording (default: ~/.browser-harness/recordings/)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output).expanduser().resolve() if args.output else _default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    ensure_daemon()
    asyncio.run(_run_async(output_dir))


if __name__ == "__main__":
    main()
