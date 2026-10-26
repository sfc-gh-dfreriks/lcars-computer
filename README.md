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

If the TrekCore computer sounds are present, they are used; otherwise the app falls back to
original synthesized tones (`voice.py` → `static/sounds/`).

| Event | TrekCore file (`voice.TREK_SOUNDS`) | Fallback |
|---|---|---|
| Request acknowledged | `input_ok_1_clean` | rising two-note chirp |
| Tool running (at most every 6 s) | `computer_work_beep` | soft three-note burble |
| Input required | `hailbeep_clean` | falling-rising tones |
| Task complete | `computerbeep_1` | ascending triad |
| Unable to comply | `computer_error` | low double tone |
| Mic open (hold TALK / space bar) | `computerbeep_8` | short blip |

The MP3s live in `static/sounds/trek/library/` (from https://www.trekcore.com/audio/computer/),
which is **git-ignored**, so they stay on this Mac. To use a different sound, drop its MP3 in
that folder, change the name in `TREK_SOUNDS`, and restart; it is trimmed and levelled to
`static/sounds/trek/<event>.wav` automatically. To fetch the default set on a new machine:

```bash
mkdir -p static/sounds/trek/library && cd static/sounds/trek/library
for f in input_ok_1_clean computer_work_beep hailbeep_clean computerbeep_1 computer_error computerbeep_8; do
  curl -sfLO "https://www.trekcore.com/audio/computer/$f.mp3"; done
```

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
  speaker treatment). It is not a clone of Majel Barrett-Roddenberry's voice; her voice likeness
  belongs to her estate. The TrekCore sound effects are kept out of this repo.
- LCARS is the Star Trek interface design; this is an original, personal-use homage.
- Bypass means spoken requests can change things in Snowflake and on this Mac without a
  confirmation step. Use ABORT if it starts something you didn't intend.
