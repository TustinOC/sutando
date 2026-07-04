---
name: screen-record
description: Start or stop a screen recording of the Mac via ffmpeg. Use when the user asks to record/capture their screen to a video file (a demo, a repro, a walkthrough) — not for a single still screenshot (use macos-tools screen capture for that).
---

# Screen Record

Start and stop screen recording using ffmpeg.

**Usage**: `/screen-record start` or `/screen-record stop`

## When to use

The user asks to **record** the screen to a video over time (demo, bug repro,
walkthrough). For a single still frame, use the screen-capture path instead.

## On activation

If argument is `start`:
```bash
python3 skills/screen-record/scripts/record.py start
```

If argument is `stop`:
```bash
python3 skills/screen-record/scripts/record.py stop
```

## Output & failure modes
- **Done =** an mp4 written by `record.py` (it prints the path on stop).
- `start` while already recording → the script no-ops/returns the existing pid; `stop` with nothing running → no-op.
- ffmpeg missing → `brew install ffmpeg`. No frames / black video → the invoking terminal lacks macOS **Screen Recording** permission (same grant the screen-capture server uses).
