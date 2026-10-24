# LCARS Computer for Cortex Code

A local, voice-driven console for Cortex Code, styled after the USS Enterprise-D bridge
computer. You talk (or type); it chirps to acknowledge, works through Cortex Code with
full tool access, asks its questions aloud, and answers in a calm starship-computer voice.

```
browser (LCARS UI, mic) ──WebSocket── server.py ──JSON-RPC/stdio── cortex acp serve (bypass mode)
                                        │
                                        ├─ voice.listen   Whisper large-v3-turbo on Apple silicon (mlx-whisper)
                                        └─ voice.speak    macOS Samantha + shipboard-speaker filter, cached
```

## Start

Double-click **`LCARS Computer.command`**, or:

```bash
cd ~/Documents/GitHub/lcars-computer
.venv/bin/python server.py      # then open http://127.0.0.1:1701
```

Allow microphone access the first time. The server only listens on 127.0.0.1.

## Talking to it

| Action | How |
|---|---|
| Ask | Hold **SPACE** (or the TALK pill), speak, release. Or type and press ENGAGE. |
| Hands-free | Toggle HANDS-FREE, then say **"Computer, …"**. Phrases without the wake word are ignored, and the mic is muted while the computer speaks. |
| Answer a question | Say the option ("option two", or its name), click a pill, or say anything else for a free-form answer. |
| Cancel | "Computer, cancel" / "abort" / "belay that", or the ABORT pill. |
| Fresh context | "Computer, new session" or NEW SESSION. |

## Sounds

Original synthesized tones (`voice.py` → `static/sounds/`), in the spirit of the TNG chirps:
`ack` (request acknowledged), `working` (tool running, at most every 6 s), `question`
(input required), `done`, `error`, `listen` (mic open).

## Behaviour

- The Cortex Code session runs in **bypass** mode: SQL, shell and file tools run without asking,
  and any plan or tool approval that still arrives is auto-approved and noted in the log.
  Only the agent's own `ask_user_question` prompts are routed to you, by voice.
- The first request tells the agent to finish each answer with a `VOICE:` line, a short
  computer-style summary. That line is spoken; the full answer is shown in the log.
- Settings (environment variables): `LCARS_VOICE` (default `Samantha`), `LCARS_RATE` (168 wpm),
  `LCARS_MODEL` (Cortex Code model), `LCARS_CWD` (working directory, default `~`), `LCARS_PORT` (1701).

## Notes

- The voice is in the *style* of the Enterprise-D computer (measured, even, clipped, with a light
  speaker treatment). It is not a clone of Majel Barrett-Roddenberry's voice and uses no audio
  from the show; her voice likeness belongs to her estate.
- LCARS is the Star Trek interface design; this is an original, personal-use homage.
- Bypass means spoken requests can change things in Snowflake and on this Mac without a
  confirmation step. Use ABORT if it starts something you didn't intend.
