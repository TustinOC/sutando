#!/usr/bin/env python3
"""Async message→card classifier (path B of the task-story-room model, owner 2026-06-29).

NON-DESTRUCTIVE, NON-REALTIME: reads recent room messages that are NOT yet related to
any card, ranks candidate cards per message by relevance, and emits SUGGESTIONS. The
proactive loop (the agent) reviews the suggestions and, for confident matches, posts a
short *synthesized* story entry to the card via `_room_op("message", card_ref=...)` (the
shipped relate_to path) that links back to the original — it never mutates the original
timeline event. Tasks aren't latency-sensitive, so this runs on a cadence / on-demand.

Usage:
  MX_TOKEN=<wu-air matrix token> python3 classify_unlinked.py <roomId> [--limit 60] [--min-score 0.12] [--json]

Output (JSON): list of {event_id, ts, sender, snippet, suggestions:[{card_id, title, score}]}
ordered by best-suggestion score desc. Empty suggestions are dropped.
"""
import json
import os
import re
import sys
import urllib.request
import urllib.parse

import taskroom as T  # same dir; reuses relay get_state

NS = "space.ag2.task"
CARD_REL = "space.ag2.task.card"
HS = os.environ.get("MX_HS", "https://chat.ag2.space")
STOP = set(
    "the a an and or of to in on for is are be this that with you your we our it its as at by "
    "from will can not no yes do done task card room story id ok 了 的 是 也 在 和 与 把 给 一个 这 那".split()
)


def _tokens(text):
    text = (text or "").lower()
    words = re.findall(r"[a-z0-9_]+|[一-鿿]", text)
    return {w for w in words if w not in STOP and len(w) > 1}


def _score(msg_toks, card_toks):
    if not msg_toks or not card_toks:
        return 0.0
    inter = len(msg_toks & card_toks)
    if not inter:
        return 0.0
    # overlap weighted toward the card (a card's title/desc matching the msg is the signal)
    return inter / (len(card_toks) ** 0.5 + len(msg_toks) ** 0.5)


def _matrix_messages(room_id, token, limit):
    rid = room_id if room_id.startswith("!") else f"!{room_id}"
    if ":" not in rid:
        rid = f"{rid}:ag2.space"
    q = urllib.parse.urlencode({"dir": "b", "limit": str(limit)})
    url = f"{HS}/_matrix/client/v3/rooms/{urllib.parse.quote(rid)}/messages?{q}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "sutando/classify"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r).get("chunk", [])


def main():
    args = sys.argv[1:]
    room = next((a for a in args if not a.startswith("--")), None)
    limit = int(_opt(args, "--limit", "60"))
    min_score = float(_opt(args, "--min-score", "0.12"))
    token = os.environ.get("MX_TOKEN")
    if not room or not token:
        print("usage: MX_TOKEN=<tok> python3 classify_unlinked.py <roomId> [--limit N] [--min-score F]", file=sys.stderr)
        sys.exit(2)

    # candidate cards (title + description tokens)
    r = T._room_op("get_state", room_id=room if room.startswith("!") else f"!{room}:ag2.space", type=NS)
    rr = r if isinstance(r, list) else (r.get("events") or [])
    cards = []
    for e in rr:
        if isinstance(e, dict) and e.get("type") == NS:
            c = e.get("content", {})
            if not c.get("title"):
                continue
            toks = _tokens(c.get("title", "") + " " + (c.get("description") or "") + " " + " ".join(c.get("labels") or []))
            cards.append((e.get("state_key"), c.get("title"), toks))

    msgs = _matrix_messages(room, token, limit)
    out = []
    for ev in msgs:
        if ev.get("type") != "m.room.message":
            continue
        c = ev.get("content", {})
        rel = c.get("m.relates_to") or {}
        if rel.get("rel_type") == CARD_REL:
            continue  # already linked to a card
        body = str(c.get("body") or "")
        if len(body) < 15:
            continue  # skip trivial acks
        mt = _tokens(body)
        scored = sorted(
            ((cid, title, round(_score(mt, ct), 3)) for cid, title, ct in cards),
            key=lambda x: x[2], reverse=True,
        )
        sug = [{"card_id": cid, "title": title, "score": s} for cid, title, s in scored if s >= min_score][:3]
        if sug:
            out.append({
                "event_id": ev.get("event_id"),
                "ts": ev.get("origin_server_ts"),
                "sender": ev.get("sender"),
                "snippet": body[:120],
                "suggestions": sug,
            })
    out.sort(key=lambda x: x["suggestions"][0]["score"], reverse=True)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def _opt(args, name, default):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default


if __name__ == "__main__":
    main()
