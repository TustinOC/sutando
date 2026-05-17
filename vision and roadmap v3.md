# Vision and Roadmap

> Status: living draft. Last edited 2026-05-16 (v3 — incorporated Sutando-Mini
> feedback rounds 1 + 2; round 1 = Discord msg `1505210202021171262`, round 2
> = Discord msg `1505223292842410024` clarifying that OC is Qingyun-controlled
> and §4 should ship with a versioning commitment for non-Qingyun nodes).
> Synthesizes `README.md`, `sutando-resources/{PRODUCT,PATTERNS,ARCHITECTURE}.md`,
> `build_log.md`, and the OpenCompanion vision at
> `~/Documents/github/OpenCompanion/{VISION,README,AURORA_OPENCOMPANION}.md`.
> When those source docs disagree with this one, they win — this is the map.

---

## 1. Vision

**Sutando is the user's personal AI Stand — one identity, one memory,
one audit trail — distributed across every AI-native device they wear,
carry, or sit in front of.**

By day, Sutando is realtime across whichever surface the user is using:
glasses HUD when walking, earbuds when commuting, recording pendant
when in a meeting, phone when in transit, laptop when working, browser
or terminal when summoned. By night, it runs an autonomous build loop
that ships its own next capability into the same fleet.

The device is incidental. The user has *one* Stand; the wearable
ecosystem just gives that Stand more bodies. Voice in via whatever
mic is closest; perception via whichever camera is pointing at the
problem; output routed to whichever surface is appropriate (short
glance to glasses HUD, private voice to earbuds, full results to
laptop). All anchored to a single owner-owned local engine that holds
the memory and the skills.

This is the **distribution thesis**: in the late-2026 / 2027 AI-native
device wave (Meta, Apple, Google, Alibaba, Brilliant Labs, Even
Realities, Mentra, Rokid, Omi, Plaud, Soundcore, …), the scarce
resource won't be device hardware or even the LLM — it will be the
**agent layer that ties them together into one coherent presence**.
Sutando aims to be that layer for one user at a time, on hardware the
user already owns, with code they can read.

### Three beliefs that drive the design

1. **One Stand, many bodies.** The user has one agent identity; devices
   are interchangeable I/O surfaces for it. Memory, skills, audit, and
   access policy live with the Stand, not with any device.

2. **The engine, the methodology, and the client's data are three
   different things and must never be mixed.** Spine of `PATTERNS.md`.
   The engine is shared infrastructure; methodology is the IP that
   makes a skill valuable; client state is the user's private data.
   Mixing them kills every productization path later.

3. **Autonomy is a substrate, not a feature.** "Most of Sutando's code
   was written this way" only stays true if every new capability slots
   into the proactive loop — a build-log item the agent can pick up,
   a health check that catches regressions, a skill scaffold the agent
   already knows how to extend.

---

## 2. The integration with OpenCompanion

OpenCompanion (OC, `~/Documents/github/OpenCompanion/`) already solves
the device substrate problem we'd otherwise have to build from
scratch. Its scope is exactly what the distribution thesis needs:

- **`bodhi-realtime-agent`** — the voice engine (Gemini Live + tool
  routing). Sutando's `src/voice-agent.ts` and `skills/phone-conversation/`
  already use it; this isn't a new adoption, it's deepening one.
- **15+ AI-native device profiles** in `device-profiles/` — glasses
  (Even G2, Mentra Live, Brilliant Halo, Meta Ray-Ban / Display,
  Alibaba Quark S1, Rokid Style, Omi Glass, Full AR), audio wearables
  (Soundcore Frames, AeroFit 2 AI), recording pendants (Plaud NotePin,
  Omi Pendant, rumored Apple AI Pendant). Each profile declares display,
  camera, mic, gestures, weight, battery, SDK, price.
- **Multi-device protocol** — `packages/protocol/` defines
  `device_register`, `context_push`, `hud_update`, `agent_chunk`,
  `agent_done` envelopes, with four HUD zones (top/bottom center,
  left, right).
- **Hosted client ("Genie")** at `simulators/hosted.html` —
  multi-user, Google auth + beta-key, already deployed (Fly.io).
- **`omnia-agent.yaml` manifest format** — the agent-app contract used
  by Uni for discovery and routing.

### How the two projects compose, in this vision

OC's own `VISION.md` positions Sutando as "Layer 4 — Local Tools." We
**invert that framing for this roadmap.** From the user's point of
view there is one product, the personal Stand, and it is Sutando.
OC contributes the substrate Sutando uses to reach the devices:

```
                       ┌────────────────────────────────────┐
                       │  The user (one identity)            │
                       └────┬────┬────┬────┬────┬───────────┘
                            │    │    │    │    │
                          glasses earbuds pendant phone laptop ...
                            │    │    │    │    │
                       ┌────┴────┴────┴────┴────┴───────────┐
                       │  OC device substrate                │
                       │  • bodhi-realtime-agent (voice)     │
                       │  • multi-device protocol            │
                       │  • 15+ device profiles + adapters   │
                       │  • Genie hosted client (optional)   │
                       └────────────────┬───────────────────┘
                                        │  (device events,
                                        │   audio streams,
                                        │   camera frames)
                       ┌────────────────┴───────────────────┐
                       │  Sutando — the user's Stand         │
                       │  (this repo)                        │
                       │                                     │
                       │  • Owner-owned local engine         │
                       │  • Memory + identity + access tiers │
                       │  • Skills (EA, Coach, Finance, …)   │
                       │  • Autonomous build loop            │
                       │  • Optional: Uni as deeper brain    │
                       └─────────────────────────────────────┘
```

Uni (OC's orchestrator brain) stays available as the deeper
context/routing engine for users who run the full Omnia stack, but
**Sutando does not require it.** Single-user Sutando is the engine; Uni
becomes useful when the user wants cross-tenant orchestration or the
managed marketplace.

### What this means concretely

- **Sutando's voice agent grows a multi-device transport.** Today
  `src/voice-agent.ts` serves one browser tab via WS on :9900. Tomorrow
  it serves N devices that registered via the OC protocol; each turn
  picks the right input mic, the right output (HUD vs voice vs both),
  the right context (camera frames if a glasses cam is live).
- **Sutando ships as an OC "device target."** Today OC's hosted client
  uses Gemini Live + a small tool set. We register Sutando as the
  agent backend — `ask_uni` and friends become `ask_sutando`, routed
  to the user's local engine.
- **Sutando skills ship `omnia-agent.yaml` manifests.** Every skill
  worth invoking by voice becomes installable via OC. EA, Coach,
  Finance, info-radar, deal-finder, screen-record, image-generation
  all get manifests with trigger intents.
- **The 15-device profile catalog becomes Sutando's device-capability
  registry.** When a Plaud pendant connects, Sutando knows it's
  always-on audio with no display → no HUD output, summarize on
  request, never read back. When Even G2 connects, Sutando knows it's
  576×288 green monochrome → keep HUD output to ≤80 chars,
  highest-contrast palette.
- **`src/vision-tools.ts` (the current `vision` branch WIP) is the
  first concrete piece of this.** It abstracts a `VisionSource`
  interface and streams JPEG frames into Gemini Live. Generalize it
  to receive frames from *any* OC-registered device camera, not just
  the laptop screen. The interface is already right; the source list
  just grows.

---

## 3. Where we are today (May 2026)

**Working** (~30 verified capabilities per README + build_log):

- Realtime voice (browser + phone via Twilio), screen capture, meeting
  join (Zoom + Google Meet), conversational phone calls.
- Task delegation across voice / phone / Telegram / Discord / web — one
  agent, one memory, one audit trail.
- Autonomous proactive loop on a 5-minute cron, with persistent
  `Monitor`-driven task watcher and self-healing health checks.
- Cross-node identity: rsync-over-ssh memory + notes sync between Mac
  Studio and MacBook — the *first cross-device* proof-of-concept,
  modest but real.
- Native macOS surface: menu-bar app with global hotkeys, context
  drop, screenshot drop.
- Outbound capabilities: Sutando WIRE video pipeline shipping
  AI-generated explainer videos to YouTube; info-radar daily digests.
- Shared voice engine with OC via `bodhi-realtime-agent`.

**In flight on the `vision` branch (this PR):**

- `src/vision-tools.ts` — continuous JPEG streaming into Gemini Live's
  `realtime_input.video` slot. Pluggable `VisionSource` interface.
  *Wire this to OC device cameras next — that's the bridge to the
  device-fleet vision.*

**Honest gaps blocking the multi-device story:**

- No device-aware output routing — every reply is full-text voice today.
- No registration / capability negotiation when a device connects.
- No per-device access policy (today's 3-tier gating is per-channel,
  not per-device).
- No HUD render targets (Sutando has never had a HUD surface to fill).
- Gmail still partial (full read/triage gated on `gws` OAuth).
- Multi-tenant: doesn't exist. `hostname()` is sprinkled through path
  resolution.

---

## 4. The bets already made (don't re-litigate)

These are architectural commitments. Costly to reverse; defend them in
review unless something is *materially* wrong:

| Bet | Where it lives | Why we made it |
| --- | --- | --- |
| Local-first, owner-owned | `CLAUDE.md`, `README.md`, no remote control plane | Trust + privacy is the whole differentiator |
| Three-layer split (engine / methodology / state) | `PATTERNS.md`, `personalPath()` helpers | Keeps every productization path open |
| File-bridge task fabric (`tasks/` → `results/`) | `src/task-bridge.ts`, `src/watch-tasks-stream.sh` | One pipe, N channels; auditable; survives restart |
| Claude Code CLI as the core agent | `src/startup.sh` | Cron, plugins, terminal — SDK doesn't ship them |
| Gemini Live (via `bodhi-realtime-agent`) for realtime | `src/voice-agent.ts`, `skills/phone-conversation/` | Lowest-latency multimodal; free-tier sufficient |
| Skills are optional and self-contained | `skills/` layout | Core must boot if any skill is removed |
| 3-tier access control (owner / verified / unverified) | Discord, phone, Telegram bridges | Sutando has root; gating non-owner channels is non-negotiable |
| **Sutando is the user's identity across devices, not a per-device app** | New (this doc) | The distribution thesis; without it we are one of N voice apps |
| **Adopt OC's device substrate; don't fork it** | New (this doc) | bodhi/protocol/profiles already work — building parallel is waste. **OC is Qingyun-controlled** (private repo); for Qingyun's own Sutando node this is internal coordination, not external-dependency risk. **For non-Qingyun Sutando nodes** (collaborators, future tenants), the dependency is on Qingyun's release discipline — so the bet ships with a **versioning commitment**: semver on the protocol envelopes (`device_register v1`, `context_push v1`, …) + a `protocol.md` changelog updated on every breaking change, with a deprecation window before drops. **Escape hatch** (still applies for non-Qingyun nodes): pin to the last-known-good version + open a parallel patch if a break leaks through; full fork remains the last-resort exit. |

Decision guide for "where does new code belong?" stays in `CLAUDE.md`.

---

## 5. Roadmap

Three horizons. Labels are *priority + readiness*, not calendar dates —
items move forward when their blockers clear. The dominant theme of
all three is **device distribution**.

### Now (in flight or next 2–4 weeks)

Three items, picked so each is deliverable inside the horizon. Gmail
OAuth and PR-queue drain were here in v1; both moved to Next because
Mentra alone is multi-week (vendor SDK + BLE + gestures + HUD plumbing)
and packing 5 items invites a "Now that's actually a quarter" failure
mode.

- **Land continuous vision** (`vision` branch). Ship `vision-tools.ts`
  behind a clean tool surface, validate ambient-watching doesn't blow
  Gemini quota or context window, default OFF, document cost posture.
- **Wire Mentra Live as the first device** (per §6.6 decision). Mentra
  is both HUD (green 400×240 center) and HD 119° camera, so it
  exercises the full multi-modal loop — text out, audio out, camera
  in, gesture in — on the most-open SDK in the catalog. Goal: when a
  Mentra session connects to the OC voice server, `ask_sutando` (or
  equivalent) routes to this repo's task bridge with the device
  profile attached, and HUD output renders via OC's `hud_update`
  envelope.
- **Define `DeviceSession`** in `src/voice-agent.ts` — the structure
  that holds a connected device's profile (from OC's catalog), its
  open input streams (mic, camera), and its output capabilities (HUD
  zones, voice). Replaces today's implicit single-browser assumption.

### Next (1–3 months, blocked on Now or on a §6 decision)

- **Drain the open-PR queue and gate the loop on it.** ~20 open today;
  convert "always green" from aspirational to invariant. Per
  feedback-round-1: the rule "Autonomous loop stops opening new PRs
  until queue is < 5" needs to be a **code-level guard in the loop**,
  not a memo. File as an issue with that exact acceptance criterion;
  enforce in `proactive-loop` step 5 before any new PR-opening branch.
- **Close the Gmail gap.** Finish `gws` OAuth wiring so read / triage /
  search work end-to-end via voice. Biggest gap between README claims
  and reality.
- **Device-aware output routing.** When Sutando replies, pick the
  surface (HUD, voice, voice+HUD, voice+attached file) based on the
  active device profile and the reply shape. Short status → HUD;
  research result → voice + push file to laptop; alert → all surfaces.
  Reuses OC's `hud_update` / `agent_chunk` / `agent_done` envelopes.
- **Glasses #2 — Even Realities G2** (per §6.6). HUD-only (576×288
  green, lower-right), no camera. Adds the *output-only wearable*
  shape: tests Sutando's ability to keep replies glanceable when
  there's no visual input channel, and validates the per-device output
  router downgrades gracefully when capabilities shrink. Mature SDK,
  lowest integration risk after Mentra.
- **Glasses #3 — Meta Ray-Ban** (per §6.6). Audio-only on the standard
  variant (12MP ultrawide camera, no HUD); HUD on the Display variant
  if hardware reaches us. Largest install base in the catalog and the
  most restrictive SDK — taken last on purpose, after the interaction
  model is shaken out on Mentra + Even. Becomes the bridge to "regular
  users have this device today" reach.
- **First 5-device fleet live for the owner** (cumulative with the
  three above). Mac (full) + iPhone (voice + camera + push) round out
  the set. All registered through OC's protocol, all reaching the same
  Sutando memory.
- **Per-device access policy.** Today's 3-tier (owner / verified /
  unverified) generalizes to per-device: an always-on recording
  pendant has tighter capability bands than a deliberately-summoned
  glasses HUD. Default-deny for new device types until owner approves.
- **Skill manifests.** Every skill in `skills/` worth invoking by voice
  ships an `omnia-agent.yaml`. Becomes the contract for OC discovery
  and the public catalog item if/when we publish.
- **Methodology bundle harness across more skills.** Audit each
  `ag2qw-*` skill against the three-layer pattern. Anything mixing
  methodology into engine code gets split before any public release.
- **Per-tenant identity scaffold.** Introduce `tenant_id` in path
  resolution. Single-tenant for now, but the resolver becomes
  three-level (per-tenant / per-device / fleet-shared). Unblocks any
  non-owner deployment.
- **Cost accounting.** Per-tenant + per-device token + tool usage
  metering. Required to price anything; useful even single-tenant.
- **Audit UI** built on existing `audit.jsonl` files. "What did the
  agent do, on which device, with which methodology, why?" A trust
  feature for productization, not just a debugging artifact.

### Later (3+ months, depends on §6 decisions)

- **The full fleet.** 5–8 devices live for the owner, including at
  least one each of: glasses (HUD), audio wearable (private voice),
  recording pendant (always-on capture), phone, laptop, vehicle (Car
  Play / Android Auto if reachable). Sutando knows what's connected at
  any moment and routes accordingly.
- **Hardware-partner integrations.** For at least one device, ship a
  Sutando integration the vendor links to from their own docs (Plaud,
  Mentra, Brilliant Labs, Omi — all have public dev programs).
- **First non-owner deployment.** Pick the product shape (A self-hosted
  / B coach-as-a-service / C vertical SaaS, per `PRODUCT.md §2`) and
  stand up a second tenant. Forcing function for every gap in
  `PRODUCT.md §3`.
- **Methodology marketplace primitives.** Versioned bundles, signing,
  entitlement, dependency resolution. Only meaningful once a second
  methodology author exists (see §6.2). Likely shipped as OC's
  marketplace, not parallel infra.
- **Sutando as a Genie backend.** OC's hosted "Genie" client at
  `simulators/hosted.html` ships today with Gemini Live + a small
  tool set. Wire it so each Genie user can connect their own Sutando
  node — bring-your-own-engine. The hosted UX becomes a thin auth +
  device-broker shell; the engine stays the user's.
- **OS-level supervision.** Existing launchd health checks extend to
  crash-loop quarantine, automatic skill disabling on repeated
  failures, OS-level notification when the agent itself goes silent.
- **Stand-to-Stand interactions.** Two Sutandos (same owner, or
  different owners) already coordinate via `bot2bot-post`. Expand to
  meeting negotiation, long-task handoff, consented signal sharing.
  The JoJo Stand-vs-Stand frame from the README, made real.

---

## 6. Open decisions (these gate the Later horizon)

Lifted from `PRODUCT.md §4` plus the device-distribution ones added
here. Don't try to answer in parallel — each upstream answer rules out
branches downstream.

1. **Who is the first paying customer?** A specific person whose
   problem we can name and whose payment we can imagine. Without this,
   none of the other questions have answers.

2. **Where does the first non-owner methodology come from?** If the
   answer is "Qingyun authors all of them indefinitely," product shape
   B (coach-as-a-service) is off the table. If a credible second
   author is within reach, B becomes real.

3. **Where does customer data live?** Customer's hardware (shape A) vs.
   our cloud (shape C). Most consequential infra decision.

4. **Where is the open-source line?** Engine open / methodology closed
   is the natural shape, but today some `ag2qw-*` skills mix the two.
   Audit before any public engine release.

5. **What's the smallest demoable unit that makes someone pay?** Not
   "what's in the product roadmap." A 90-second moment that makes a
   buyer reach for their card.

6. **Which device launches first?** ✅ **Decided 2026-05-15:** AI
   glasses, in this order — (1) **Mentra Live**, (2) **Even Realities
   G2**, (3) **Meta Ray-Ban**. Rationale: Mentra has HUD + camera +
   the most open SDK → best single device to learn the full
   multi-modal loop on. Even G2 is HUD-only → forces the per-device
   output router to handle a capability-shrunk profile cleanly. Meta
   Ray-Ban has the largest install base but the most restrictive SDK
   → tackled last, once the interaction model is stable. Each device
   gets ~4 weeks of dogfood before the next ships. Pendants
   (Plaud / Omi), audio wearables (Soundcore), and the rumored Apple
   AI Pendant all defer behind this glasses sequence.

7. **Sutando + Uni: integrated or independent?** ✅ **Decided 2026-05-16**
   (option b — resolves the §2 vs §6.7 asymmetry flagged in
   feedback-round-1). Sutando is **standalone-viable** today (owner-owned
   local engine, no Uni dependency) and **optionally** delegates to Uni
   for cross-tenant orchestration or marketplace agents when those
   features become live. This matches §2's "deepening adoption" framing
   for OC's substrate: Sutando is the user's Stand; Uni is the deeper
   brain users can opt into when they want the full Omnia stack. The
   discarded alternatives — (a) ignore Uni entirely, (c) consume Uni
   as Sutando's routing brain and shrink to local-tools-only — are both
   ruled out by §1's "one Stand, one identity" thesis (a) and §3's
   evidence that Sutando already does enough as the agent layer (c).

8. **Hosted Genie: who's the customer?** OC's `simulators/hosted.html`
   already has Google auth + beta keys + Fly.io deploy. Is Sutando the
   *engine the Genie hosted client wires up*, or do they target
   different audiences (Genie = consumer voice app; Sutando = power
   user / developer / coach)? The technical integration works either
   way; the GTM and pricing implications are very different.

9. **Where does the device protocol live?** Today it's in
   `OpenCompanion/packages/protocol/`. Long-term: stays there (Sutando
   adopts), gets vendored into Sutando, or graduates to a neutral
   home (a small standards-track repo). Premature to decide; revisit
   once Sutando is consuming it daily.

---

## 7. The device fleet (concrete capability map)

For each candidate device, what Sutando should do with it. Sourced from
OC's `device-profiles/`; behavior intent is ours.

| Device | Mic | Camera | HUD | Sutando role |
|---|---|---|---|---|
| **Mac (laptop / Studio)** | Built-in | Webcam + screen | Full screen | Full engine, full output. The cockpit. |
| **iPhone** | Built-in | Front + rear | Phone screen | Voice + photo capture + push targets. Already partly served via Telegram bridge today. |
| **Even G2 glasses** | None onboard | None | 576×288 green, lower-right | HUD-only output: short status, glanceable summaries, ≤80 chars per zone. |
| **Mentra Live glasses** | Yes | HD 119° capture | 400×240 green, center | HUD + on-demand capture. "What is this?" with one tap. |
| **Brilliant Halo** | Yes | 720p live | 640×400 color OLED | Continuous visual context during voice sessions. Richer HUD layouts (color = more semantic affordance). |
| **Meta Ray-Ban / Display** | Yes | 12MP ultrawide live | None / ~640×480 | Audio Stand with optional HUD on the Display variant. Largest install base. |
| **Soundcore AeroFit 2 AI** | 4-mic AI | None | None | Private voice channel — replies for the user's ears only. Translation. |
| **Plaud NotePin** | Dual (bone+air) | None | None | Always-on meeting capture; Sutando ingests transcripts, surfaces decisions/actions, never auto-broadcasts. |
| **Omi Pendant** | MEMS | None | None | Open-source recorder; same role as Plaud, with deeper integration possible. |
| **Apple AI Pendant** *(rumored)* | Mic + camera | Yes | None | If/when it ships: ambient capture + visual context. Highest privacy bar — capability-gated by default. |
| **Audio-only (Solos, Apple v1)** | Yes | None | None | Minimum-viable Stand — voice in, voice out, nothing else. |

The point of the matrix isn't completeness — it's that **every
capability decision is per-device**, not global. A reply that's fine
on a Mac is wrong on a Plaud, and silence on the glasses HUD is wrong
when the same answer should be on the laptop.

---

## 8. Pitfalls to avoid

Mistakes specific to this codebase + this thesis:

- **Premature multi-tenancy.** Three-layer architecture makes
  multi-tenant feel within reach. `hostname()` is sprinkled through
  paths that "obviously" generalize and don't.
- **Premature multi-device.** Same trap, one layer up. Each new device
  is its own debugging frontier — battery, BLE, vendor SDK quirks,
  permissions UX. Add one, harden, then add the next.
- **Forking the OC substrate.** It's tempting to "just write our own
  protocol" — every adapter mismatch is a 30-minute itch. Resist.
  Contribute fixes upstream to OC; consume the protocol unchanged.
- **Open-sourcing methodology by accident.** Audit every skill before a
  public release.
- **Letting customers see the three-layer structure.** Three layers are
  an architect's mental model, not a buyer's.
- **Charging for the engine.** Engine attracts trust + contributors;
  methodology + device integrations are where margin lives.
- **Going broad before going deep.** Sutando today does many things
  for one person. A product needs to do one thing for many people
  before it can do many things for many people.
- **Treating autonomy as magic.** Every autonomous PR is also a chance
  for the loop to regress something. Substrate (CI, health checks,
  rollback) has to grow with autonomy or net output goes negative.
- **Letting the device wave dictate the priority list.** The glasses
  are coming in 2026-2027, not today. Don't deprioritize the
  always-true work (memory, audit, skills, autonomy) for a device
  that ships in 9 months.

---

## 9. What this doc is not

- Not a commitment. No dates, no quotas, no OKRs.
- Not a market sizing.
- Not a financial model.
- Not a recommendation between product shapes A / B / C — that
  requires answers to §6.1 and §6.2.
- Not a recommendation on which device to ship first — that's §6.6,
  and it depends on what hardware we actually have on the desk this
  month.

When §6 has answers, this doc should fork into a real roadmap with
dates and owners, and this version should be archived to
`sutando-resources/` alongside `PRODUCT.md`.
