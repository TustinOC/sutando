---
name: whatsapp
description: Send WhatsApp messages, list chats, and search history via wacli (local CLI backed by a synced store at ~/.wacli). Use after the user has run `wacli auth` to pair the device.
---

# WhatsApp (wacli)

Send messages, list chats, and search history using [wacli](https://github.com/steipete/wacli) — a local CLI that syncs a WhatsApp Web session into `~/.wacli/`. No third-party service, no API key; the auth flow is a QR-code scan from the user's phone.

## When to use

- The user asks to send a WhatsApp message, list chats, or search WhatsApp history.
- The user asks "did X message me on WhatsApp?" or similar history-search questions.
- **Not** available until `wacli auth` has completed on this Mac. Probe with `wacli chats list --limit 1`; if it errors "not authenticated," tell the user to run `wacli auth` (QR-code pairing from their phone).

## Install + auth

```bash
brew install steipete/tap/wacli       # one-time install (3rd-party tap, NOT Homebrew-core)
wacli auth                            # opens a QR for the user to scan from WhatsApp → Linked Devices
```

`steipete/tap` is a third-party Homebrew tap (not Homebrew-core), and wacli stores a WhatsApp Web session locally at `~/.wacli/`. Review the tap source before installing if security-sensitive.

The session lives at `~/.wacli/`. Stays signed in across reboots until the user revokes the linked device from their phone.

Optional `.env` settings (label shown in WhatsApp's Linked Devices screen on the user's phone):

```bash
WACLI_DEVICE_LABEL=Sutando            # any string; appears next to the device in WhatsApp → Linked Devices
WACLI_DEVICE_PLATFORM=CHROME          # default per wacli is DESKTOP; CHROME is the fallback if an invalid value is set.
                                      # See wacli's docs (https://github.com/steipete/wacli) for the full platform-string list.
```

## Commands

```bash
wacli send text --to "+14155551234" --message "Hello!"     # send
wacli send text --to "+14155551234" --message "$(cat draft.txt)"  # multi-line via stdin / file
wacli chats list --limit 20                                # recent chats
wacli messages search "keyword" --limit 10                 # search history
wacli messages list --chat "+14155551234" --limit 20       # read a thread
```

Phone numbers are in E.164 (`+countrycode...`). For groups, pass the JID returned by `wacli chats list` instead of a phone number.

## Conventions

- **Always confirm message content with the user before sending.** Matches the iMessage / SMS / X-post pattern in CLAUDE.md. **The confirmation flow lives in the agent / bridge that calls `wacli send` — this skill itself has no confirm step.** Treating the SKILL.md as the confirmation surface would skip the check entirely on direct CLI invocations.
- For multi-line / long messages, write to a `/tmp/wa-*.txt` and pass via `--message "$(cat /tmp/wa-X.txt)"` — avoids shell-escape issues with quotes and emoji.
- WhatsApp delivers messages best-effort; `wacli send text` returning success means the message hit WhatsApp's servers, not that the recipient has read it.

## Inbound: self-chat listener (talk to Sutando over WhatsApp)

The commands above are the **outbound/query** half. The **inbound** half lets the
owner *message Sutando* over WhatsApp: because this Mac is a linked device on the
owner's own account, the owner writes to their own **self-chat** ("Message
Yourself") and Sutando picks it up.

Daemon: `scripts/selfchat-bridge.py` (in this skill). Flow:
- Owner sends plain text in the WhatsApp self-chat → bridge writes an owner-tier
  `tasks/task-*.txt` (`source: whatsapp`) → core processes it → bridge sends the
  result back into the self-chat **prefixed `🤖 Sutando:`**.
- **Echo-suppression:** inbound polling skips any message starting with `🤖`, so
  the bot never reacts to its own replies (no loop).
- **Single-instance:** `flock` on `~/.wa-selfchat-bridge.lock` — a startup.sh
  launch and a launchd/manual launch can't double-process.
- **First run:** acts only on messages from start-time onward (no history replay).

Config (all optional): `WA_SELF_JID` (default: auto-detected from
`wacli auth status`, so no phone number is hardcoded), `WA_POLL_S` (default 8).

Launch: `src/startup.sh` starts it automatically when wacli is authenticated
(skip with `SKIP_WHATSAPP=1`), mirroring the telegram/discord bridge blocks.
Manual: `python3 skills/whatsapp/scripts/selfchat-bridge.py`.

**Store-lock gotcha:** only one process can hold the wacli store lock
(`~/.wacli`). Don't leave a stray `wacli auth --follow` running — it blocks the
bridge's `sync`. Pass `--lock-wait` on any manual wacli command issued while the
bridge is running.

## Failure modes

- `wacli: not authenticated` → user must run `wacli auth` (QR-code flow). One-time per Mac.
- `wacli: linked device revoked` → user revoked the session from their phone. Re-run `wacli auth`.
- `wacli: rate limited` → WhatsApp is throttling. Back off ~30s and retry.
- Phone-number invalid → confirm the user provided E.164 format with a `+` prefix.

## Origin

Synthesizes the proposal in [PR #180](https://github.com/sonichi/sutando/pull/180) by @priyansh4320 (stale + conflicts, never landed) and the wacli reference already documented in CLAUDE.md's "Built-in capabilities" section. Issue #179 (asking for WhatsApp support) was closed 2026-05-16 noting the capability had landed as inline doc.
