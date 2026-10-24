#!/bin/bash
# Double-click to start the LCARS computer and open it in the browser.
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q mlx-whisper fastapi "uvicorn[standard]" numpy soundfile; }
lsof -ti tcp:1701 >/dev/null || (.venv/bin/python server.py > /tmp/lcars-computer.log 2>&1 &)
until curl -sf http://127.0.0.1:1701/ >/dev/null; do sleep 1; done
open http://127.0.0.1:1701/
