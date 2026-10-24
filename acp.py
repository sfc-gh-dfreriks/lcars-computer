"""Bridge to Cortex Code over the Agent Client Protocol (`cortex acp serve`, JSON-RPC on stdio).

Runs the session in bypass mode, so tools and plans execute without prompts. The one
exception is the agent's own questions (ask_user_question). They arrive as
session/request_permission with `_meta.questionIndex`, and are passed to the UI so the
computer can ask them aloud. A spoken free-form answer goes back in
`outcome._meta.freeFormText`.
"""
import asyncio
import itertools
import json
import logging
import os

log = logging.getLogger("lcars.acp")

PREAMBLE = """[Voice interface] You are being operated through a voice interface styled after the
USS Enterprise-D computer. Work exactly as you normally would, with all your tools.
When you finish, end your reply with one final line:
VOICE: <a spoken summary of at most 25 words, in the calm, clipped, factual style of a starship
computer, for example "Working." / "Query complete. Three connections found." / "Unable to comply.">
When you need a decision from me, use ask_user_question with short option labels; they are read aloud.

Request: """


class AcpSession:
    def __init__(self, on_event, cwd=None, model=None):
        self.on_event = on_event              # async callback(dict) -> forwarded to the browser
        self.cwd = cwd or os.path.expanduser("~")
        self.model = model
        self.proc = None
        self.sid = None
        self.ids = itertools.count(1)
        self.pending = {}                     # our request id -> Future
        self.questions = {}                   # agent request id -> question params
        self.first_prompt = True
        self.busy = False

    # ------------------------------------------------------------- lifecycle
    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            "cortex", "acp", "serve", cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=64 * 1024 * 1024)
        asyncio.create_task(self._reader())
        await self.request("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        res = await self.request("session/new", {"cwd": self.cwd, "mcpServers": []})
        self.sid = res["sessionId"]
        await self.request("session/set_config_option", {"sessionId": self.sid, "configId": "mode", "value": "bypass"})
        if self.model:
            await self.request("session/set_config_option", {"sessionId": self.sid, "configId": "model", "value": self.model})
        self.first_prompt = True
        model = next((o["currentValue"] for o in res.get("configOptions", []) if o["id"] == "model"), "?")
        await self.on_event({"type": "session", "sessionId": self.sid, "model": self.model or model})

    async def stop(self):
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()

    # ------------------------------------------------------------- JSON-RPC
    async def _send(self, obj):
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self.proc.stdin.drain()

    async def request(self, method, params):
        i = next(self.ids)
        fut = asyncio.get_running_loop().create_future()
        self.pending[i] = fut
        await self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        return await fut

    async def _reader(self):
        async for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            try:
                await self._dispatch(msg)
            except Exception:
                log.exception("dispatch failed")
        await self.on_event({"type": "error", "text": "Cortex Code session ended."})

    async def _dispatch(self, msg):
        if "id" in msg and ("result" in msg or "error" in msg) and msg["id"] in self.pending:
            fut = self.pending.pop(msg["id"])
            if "error" in msg:
                fut.set_exception(RuntimeError(json.dumps(msg["error"])))
            else:
                fut.set_result(msg["result"])
            return
        method = msg.get("method")
        if method == "session/update":
            await self._update(msg["params"]["update"])
        elif method == "session/request_permission":
            await self._permission(msg["id"], msg["params"])
        elif "id" in msg:                     # any other client method we don't implement
            await self._send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported"}})

    async def _update(self, u):
        kind = u.get("sessionUpdate")
        if kind == "agent_message_chunk":
            await self.on_event({"type": "text", "text": u["content"].get("text", "")})
        elif kind == "tool_call":
            await self.on_event({"type": "tool", "id": u["toolCallId"], "title": u.get("title", ""),
                                 "kind": u.get("kind", ""), "status": u.get("status", "pending")})
        elif kind == "tool_call_update":
            await self.on_event({"type": "tool", "id": u["toolCallId"], "status": u.get("status", "")})
        elif kind == "plan":
            await self.on_event({"type": "plan", "entries": u.get("entries", [])})

    async def _permission(self, rid, p):
        opts = p.get("options", [])
        if "questionIndex" in (p.get("_meta") or {}):          # the agent is asking me something
            self.questions[rid] = p
            await self.on_event({"type": "question", "rid": rid, "question": p["toolCall"]["title"],
                                 "options": [{"id": o["optionId"], "label": o["name"], "desc": o.get("description", ""),
                                              "free": bool((o.get("_meta") or {}).get("freeForm"))} for o in opts],
                                 "index": p["_meta"]["questionIndex"], "total": p["_meta"].get("totalQuestions", 1)})
            return
        # Anything else (tool or plan approval) is auto-approved: bypass + auto-execute is the contract.
        pick = next((o for k in ("allow_always", "allow_once") for o in opts if o.get("kind") == k), opts[0] if opts else None)
        await self.on_event({"type": "auto_approved", "title": p.get("toolCall", {}).get("title", "")})
        await self._send({"jsonrpc": "2.0", "id": rid,
                          "result": {"outcome": {"outcome": "selected", "optionId": pick["optionId"]} if pick else {"outcome": "cancelled"}}})

    # ------------------------------------------------------------- user actions
    async def prompt(self, text):
        if self.busy:
            await self.on_event({"type": "error", "text": "Still working on the previous request."})
            return
        self.busy = True
        body = (PREAMBLE + text) if self.first_prompt else text
        self.first_prompt = False
        await self.on_event({"type": "working"})
        try:
            res = await self.request("session/prompt", {"sessionId": self.sid, "prompt": [{"type": "text", "text": body}]})
            await self.on_event({"type": "done", "stopReason": res.get("stopReason")})
        except Exception as e:
            await self.on_event({"type": "error", "text": str(e)})
        finally:
            self.busy = False

    async def answer(self, rid, option_id, free_text=""):
        self.questions.pop(rid, None)
        outcome = {"outcome": "selected", "optionId": option_id}
        if free_text:
            outcome["_meta"] = {"freeFormText": free_text}
        await self._send({"jsonrpc": "2.0", "id": rid, "result": {"outcome": outcome}})

    async def cancel(self):
        for rid in list(self.questions):
            await self._send({"jsonrpc": "2.0", "id": rid, "result": {"outcome": {"outcome": "cancelled"}}})
            self.questions.pop(rid)
        if self.sid:
            await self._send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.sid}})
