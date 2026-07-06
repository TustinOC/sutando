# Sutando roadmap — North Star

Sutando's direction is three pillars — **Understand · Act · Grow** — with **Safe** as a
cross-cutting constraint on every one of them. This file is the stable part: the pillars,
the safety constraint, and the litmus for what belongs in the open-source kernel versus a
user's own tuning. The queryable, progress-derivable layer lives in **GitHub milestones +
issues** (one milestone per pillar; issues = the U/A/G items below), so progress is derived
from issue state rather than hand-edited percentages.

## OSS-vs-product litmus

> Same kernel. Yours to tune.

For every milestone, ask: **would a stranger's checkout benefit from this unchanged?** If yes,
it's **OSS** (the kernel). If it's owner-specific tuning, identity, or wiring, it's **product**
(a user's own layer). Each pillar names its split explicitly below.

---

## Understand

*Know the user across every signal — voice, text, screen, calendar, and the quieter ones:
tone, pace, what they stopped asking about. An agent that is legible to you by knowing you.*

Today Sutando understands **explicit** signals (what you say, type, show). The aspiration is
the **implicit** ones. This is the highest-leverage unstarted frontier — it is what makes
every other pillar personal rather than generic.

- **U1 — Passive signal capture.** Ingest at least one implicit signal beyond explicit request
  text (cadence, topic-drop, tone).
  - *done* = a passive signal is captured and *measurably changes* a response (A/B a reply with
    vs. without it); the user can see that it fired.
- **U2 — Legible understanding.** The user can ask "what do you think you know about me, and
  why," and get a **sourced, editable** answer (memory provenance surfaced, one-tap correct).
- **U3 — Cross-signal fusion.** Voice + screen + calendar + history combined into one model of
  the moment.
  - *done* = a single task draws on ≥2 signal sources without the user stitching them together.

OSS = the signal-capture + provenance substrate. Product = the user's actual model of
themselves (their memory and taste).

---

## Act

*Reach into every part of the stack. Software today, hardware next, the body eventually.
Not advisory — operational.*

Software is largely here. The forward arc (hardware → body) is the least-milestoned part of
the roadmap.

- **A1 — Operational depth, not advisory.** For a named workflow, Sutando *completes* it
  end-to-end rather than suggesting steps.
  - *done* = a real workflow runs start→finish agent-driven, gated **only** at the irreversible
    step. (An existence proof already exists — the goal is to generalize the pattern.)
- **A2 — Reach expansion is config, not code.** Adding a new tool/surface is a skill-manifest
  entry with no core edit, and a stranger's checkout can add one unchanged.
  - *note:* tool surfaces already work this way via manifest-loaded skills. The remaining bar is
    a **non-tool** surface (e.g. a new bridge *channel* via manifest) — or reclassify as
    validation of the existing capability.
- **A3 — Hardware bridge.** The first non-screen actuator.
  - *done* = one physical/IoT/robotic actuator is drivable through the same task bridge, behind
    the approval gate.

OSS = the tool-bridge + manifest surface + actuator abstraction. Product = the user's specific
stack wiring and credentials.

---

## Grow

*Watches itself, heals itself, improves itself — the loop closing on its own code.*

Furthest along and already partly built. The gap is **detect → propose**, not detect: we detect
(health-check), coordinate, and prep fixes on branches — but the propose step is still manual.
Grow is about making "the user reviews a ready PR" the default.

- **G0 — The loop that must not die (foundational; sequence first).** Everything below assumes
  the agent's substrate stays up. Scheduled work driven by the interactive session can be
  silently dropped when the session is busy at fire time — and a self-healing loop whose
  *verifier* silently stops is unverifiable. OS-level scheduling + liveness alerting fix this.
  - *done* = scheduled fires survive a busy session (no manual catch-up), with off-machine
    liveness alerting. Cheapest milestone in the set; everything in Grow stands on it.
    (Tracked by #1897 launchd cron-runner + #1932 dead-man's switch.)
- **G1 — Detect → propose is one artifact.** A fixable regression auto-drafts the fix on a
  branch **and** writes a machine-readable fix-ready record (branch, diff summary, test
  evidence, blast radius).
  - *done* = ≥1 real regression flows detect → branch → queued-with-evidence, with no human
    utterance needed to reach "queued."
- **G2 — One-tap approve.** The user approves from where they already are; the node does the
  mechanical PR-open *on* approval, not before.
  - *done* = an approve signal triggers PR creation (owner-identity, owner-initiated) from the
    prepped branch; tap → PR in under 30s. PRs open as **draft** and the tap marks
    ready-for-review.
- **G3 — Self-scoped blast-radius gate (safety interlock).** A node may only auto-*propose*
  (never auto-merge) fixes whose diff stays inside a declared self-maintenance allowlist.
  User-facing, secret, or core diffs stay prose-surfaced.
  - *done* = a classifier rejects out-of-scope diffs with a logged reason; test covers in-scope
    accept + out-of-scope reject. **Must land with or before G2.**
- **G4 — Close the observe → verify loop.** After a self-maintained fix merges, re-run the
  originally-failing check.
  - *done* = post-merge, the trigger check is re-run and its result recorded against the
    fix-ready item. "Self-maintained" means *confirmed repaired*, not *PR opened*.

OSS = the detect → propose → verify loop + fix-ready schema + scope classifier. Product = the
user's approve surface and owner-identity PR mechanics.

---

## Safe (cross-cutting constraint)

*Every operational path has a human bookend.*

- **Invariant:** no milestone above ships an irreversible, user-facing, or self-modifying action
  without a **logged approval point**. Self-promotion is the canonical case: it earns its place
  only by riding the approval gate, never by bypassing it.
- *done* = an audit shows every operational or self-modifying path has an approval checkpoint
  with a logged user signal.

---

## Sequencing

- **G0 first, unconditionally.** Everything in Grow — especially G4's "confirmed repaired" — is
  meaningless if the loop that runs the verifier keeps dropping fires. It is also the cheapest
  milestone.
- **Grow** is furthest along and fastest to finish — good momentum.
- **Understand**'s implicit-signal frontier (U1/U2) is the highest-leverage *unstarted* work and
  the recommended flagship new investment; it is what makes the other pillars personal.
- **Act**'s hardware arc (A3) is the most ambitious — milestone it now, sequence it last.
- **The load-bearing risk is G3.** An agent that auto-proposes fixes is one bad classifier away
  from churning surfaces it shouldn't. The scope gate must land *with or before* G2. That
  ordering is the real design call.
