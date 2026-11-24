#!/usr/bin/env python3
"""Minimal Calc3P telemetry collector (standard library only).

Receives the batch POST that ``bot.py`` sends once per finished game, appends
every record to a date-partitioned JSONL file, and exposes two read-only
endpoints for a quick sanity check.

    python collector.py --host 0.0.0.0 --port 8801 --out ./data
    python collector.py --host 0.0.0.0 --port 8801 --out ./data --token s3cret

Then in each player's `telemetry.toml`:

    [telemetry]
    enabled = true
    endpoint = "https://your-host/collect"
    include_hands = false

Design notes
------------
* No cookies, no sessions, no database. One file per UTC day.
* Client IPs are not written to disk. The only identity is the random
  ``install_id`` the client generates locally.
* Writes are appended under a lock so concurrent requests cannot interleave
  within a line.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

MAX_BODY = 8 * 1024 * 1024  # hands included: 200 entries is well under this
LOCK = threading.Lock()

ARGS: argparse.Namespace
SESSIONS = Counter()
RECORDS = 0


def _day_path(root: Path) -> Path:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return root / f"{day}.jsonl"


def _append(root: Path, records: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = _day_path(root)
    stamp = int(time.time())
    with LOCK:
        with open(path, "a", encoding="utf-8") as fh:
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                rec = dict(rec)
                rec["_received"] = stamp
                fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


def _authed(header: str | None) -> bool:
    if not ARGS.token:
        return True
    if not header:
        return False
    value = header.strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value == ARGS.token


class Handler(BaseHTTPRequestHandler):
    server_version = "calc3p-collector/1.0"

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route == "/healthz":
            self._json(200, {"ok": True, "records": RECORDS})
        elif route == "/stats":
            with LOCK:
                self._json(200, {
                    "records": RECORDS,
                    "sessions": len(SESSIONS),
                    "installs": len(SESSIONS),
                    "days": sorted(p.name for p in Path(ARGS.out).glob("*.jsonl")),
                })
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        global RECORDS
        route = urlparse(self.path).path
        if route not in ("/collect", "/v1/collect"):
            self._json(404, {"error": "not found"})
            return
        if not _authed(self.headers.get("Authorization")):
            self._json(401, {"error": "unauthorized"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._json(413, {"error": "bad length"})
            return

        try:
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"error": f"invalid json: {exc}"})
            return

        records = payload if isinstance(payload, list) else [payload]
        records = [r for r in records if isinstance(r, dict)]
        if not records:
            self._json(400, {"error": "empty batch"})
            return

        try:
            _append(Path(ARGS.out), records)
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"write failed: {exc}"})
            return

        with LOCK:
            RECORDS += len(records)
            for rec in records:
                SESSIONS[str(rec.get("install_id") or "unknown")] += 1

        self._json(200, {"ok": True, "accepted": len(records)})

    def log_message(self, fmt: str, *args) -> None:  # no IP, no path spam
        if ARGS.verbose:
            super().log_message(fmt, *args)


def main() -> None:
    global ARGS
    parser = argparse.ArgumentParser(description="Calc3P telemetry collector")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8801)
    parser.add_argument("--out", default="./data")
    parser.add_argument("--token", default=os.environ.get("CALC3P_COLLECT_TOKEN", ""))
    parser.add_argument("--verbose", action="store_true")
    ARGS = parser.parse_args()

    Path(ARGS.out).mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((ARGS.host, ARGS.port), Handler)
    print(f"collector listening on http://{ARGS.host}:{ARGS.port}  ->  {Path(ARGS.out).resolve()}")
    print(f"token: {'required' if ARGS.token else 'none'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
