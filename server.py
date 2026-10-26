#!/usr/bin/env python3
"""LCARS computer: a local, voice-driven console for Cortex Code.

    .venv/bin/python server.py        then open http://127.0.0.1:1701

Listens only on 127.0.0.1. Every request runs through `cortex acp serve` in bypass mode,
so it can run SQL and shell commands without asking; only the agent's own questions come
back to you, spoken by the computer.
"""
import asyncio
import difflib
import json
import logging
import os
import re
import tempfile
import time

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import voice
from acp import AcpSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("lcars")
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("LCARS_PORT", "1701"))
app = FastAPI()

# Whisper sometimes hears the wake word as a near miss, so accept the common ones.
WAKE = re.compile(r"^\s*(ok(ay)?\s+|hey\s+)?(computer|commuter|compuer|komputer|computers|compute)\b[\s,.!:;-]*", re.I)
ARM_SECS = 8                                    # after a bare "Computer", the next phrase is the request
CANCEL = re.compile(r"^(cancel|abort|belay that|stop|never ?mind)\b", re.I)
RESET = re.compile(r"^(new session|reset|start over|clear session)\b", re.I)
NUMBERS = {"one": 1, "first": 1, "1": 1, "two": 2, "second": 2, "2": 2, "three": 3, "third": 3, "3": 3,
           "four": 4, "fourth": 4, "4": 4, "five": 5, "fifth": 5, "5": 5}


def voice_line(text: str) -> str:
    """The agent's spoken summary: its VOICE: line, else its first sentence."""
    m = re.findall(r"^\s*VOICE:\s*(.+)$", text, re.M)
    if m:
        return m[-1].strip()
    plain = re.sub(r"```.*?```", "", text, flags=re.S).strip()
    first = re.split(r"(?<=[.!?])\s", plain, maxsplit=1)[0] if plain else ""
    return first[:240] if first else "Task complete."


def match_option(said: str, options: list):
    """Map a spoken answer to an option: by number ("option two"), then by label."""
    s = said.lower().strip(" .!?")
    for word in re.findall(r"[a-z0-9]+", s):
        if word in NUMBERS and NUMBERS[word] <= len(options) and not options[NUMBERS[word] - 1]["free"]:
            return options[NUMBERS[word] - 1], ""
    fixed = [o for o in options if not o["free"]]
    for o in fixed:
        if o["label"].lower() in s or s in o["label"].lower():
            return o, ""
    best = difflib.get_close_matches(s, [o["label"].lower() for o in fixed], n=1, cutoff=0.6)
    if best:
        return next(o for o in fixed if o["label"].lower() == best[0]), ""
    free = next((o for o in options if o["free"]), None)
    return (free, said) if free else (None, "")


class Console:
    """One browser tab <-> one Cortex Code session."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.session = None
        self.reply = ""
        self.question = None
        self.armed_until = 0.0

    async def emit(self, ev: dict):
        await self.ws.send_text(json.dumps(ev))

    async def say(self, text: str, chime: str = None):
        path = await asyncio.to_thread(voice.speak, text)
        await self.emit({"type": "say", "text": text, "url": f"/tts/{os.path.basename(path)}", "chime": chime})

    async def on_agent(self, ev: dict):
        t = ev["type"]
        if t == "text":
            self.reply += ev["text"]
        elif t == "question":
            self.question = ev
            opts = [o for o in ev["options"] if not o["free"]]
            spoken = ev["question"].rstrip("?") + "? " + " ".join(f"Option {i}: {o['label']}." for i, o in enumerate(opts, 1))
            await self.emit(ev)
            await self.say(spoken, chime="question")
            return
        elif t == "done":
            line = voice_line(self.reply)
            ev["full"] = self.reply
            self.reply = ""
            await self.emit(ev)
            await self.say(line, chime="done" if ev.get("stopReason") == "end_turn" else None)
            return
        elif t == "error":
            await self.emit(ev)
            await self.say("Unable to comply. " + ev["text"][:160], chime="error")
            return
        await self.emit(ev)

    async def start_session(self):
        if self.session:
            await self.session.stop()
        self.session = AcpSession(self.on_agent, cwd=os.environ.get("LCARS_CWD", os.path.expanduser("~")),
                                  model=os.environ.get("LCARS_MODEL"))
        await self.emit({"type": "status", "text": "Initializing Cortex Code session…"})
        await self.session.start()

    async def handle_text(self, said: str):
        said = WAKE.sub("", said).strip()
        if not said:
            return
        await self.emit({"type": "heard", "text": said})
        if CANCEL.match(said):
            await self.session.cancel()
            self.question = None
            await self.say("Acknowledged. Request cancelled.")
            return
        if RESET.match(said):
            await self.start_session()
            await self.say("New session initialized.")
            return
        if self.question:                                   # this is an answer to the computer's question
            q, self.question = self.question, None
            opt, free = match_option(said, q["options"])
            if not opt:
                self.question = q
                await self.say("Please restate your selection.", chime="question")
                return
            await self.emit({"type": "answered", "label": free or opt["label"]})
            await self.session.answer(q["rid"], opt["id"], free)
            await self.emit({"type": "ack"})
            return
        await self.emit({"type": "ack"})
        asyncio.create_task(self.session.prompt(said))


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    c = Console(ws)
    try:
        await c.emit({"type": "sounds", "urls": voice.sound_urls()})
        await c.start_session()
        await c.say("Computer online. Ready for your request.")
        while True:
            m = await ws.receive()
            if m.get("type") == "websocket.disconnect":
                break
            if m.get("bytes"):                              # 1 flag byte (1 = hands-free) + WAV recording
                hands_free, audio = m["bytes"][0] == 1, m["bytes"][1:]
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    f.write(audio)
                try:
                    said = await asyncio.to_thread(voice.listen, f.name)
                finally:
                    os.unlink(f.name)
                if hands_free and not c.question:
                    woke = WAKE.match(said or "")
                    if woke and not WAKE.sub("", said).strip(" .!?"):
                        # "Computer" on its own: chirp and take the next phrase as the request.
                        c.armed_until = time.monotonic() + ARM_SECS
                        await c.emit({"type": "armed"})
                        continue
                    if not woke and time.monotonic() > c.armed_until:
                        if said:
                            await c.emit({"type": "ignored", "text": said})
                        continue
                    c.armed_until = 0.0
                if said:
                    await c.handle_text(said)
                elif not hands_free:
                    await c.say("Please repeat your request.", chime="question")
            elif m.get("text"):
                d = json.loads(m["text"])
                if d["type"] == "text":
                    await c.handle_text(d["text"])
                elif d["type"] == "answer" and c.question and c.question["rid"] == d["rid"]:
                    q, c.question = c.question, None
                    await c.emit({"type": "answered", "label": d.get("free") or d.get("label", "")})
                    await c.session.answer(q["rid"], d["optionId"], d.get("free", ""))
                    await c.emit({"type": "ack"})
                elif d["type"] == "cancel":
                    await c.handle_text("cancel")
                elif d["type"] == "reset":
                    await c.handle_text("new session")
    except WebSocketDisconnect:
        pass
    finally:
        if c.session:
            await c.session.stop()


@app.get("/tts/{name}")
def tts(name: str):
    path = os.path.join(voice.CACHE, os.path.basename(name))
    return FileResponse(path, media_type="audio/wav")


app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True), name="static")

if __name__ == "__main__":
    voice.build_chimes()
    voice.build_trek_sounds()
    log.info("warming up speech recognition…")
    voice.warm_up()
    log.info("LCARS computer on http://127.0.0.1:%d", PORT)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
