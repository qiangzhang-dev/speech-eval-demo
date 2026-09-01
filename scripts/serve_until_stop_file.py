#!/usr/bin/env python3
"""Run the local dashboard until a scoped stop file requests clean shutdown."""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from speech_eval.web import create_server


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    args = parser.parse_args()

    stop_file = args.stop_file.resolve()
    if stop_file.exists():
        raise ValueError(f"stop file must not exist before startup: {stop_file}")

    server = create_server(args.database, host=args.host, port=args.port)

    stopped = threading.Event()

    def watch_stop_file() -> None:
        while not stopped.is_set() and not stop_file.is_file():
            time.sleep(0.05)
        if not stopped.is_set():
            server.shutdown()

    watcher = threading.Thread(target=watch_stop_file, name="workbench-stop-file", daemon=True)
    watcher.start()
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        stopped.set()
        server.server_close()
    watcher.join(timeout=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
