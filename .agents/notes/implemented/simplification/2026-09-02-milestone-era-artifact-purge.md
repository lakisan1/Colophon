# Agent Note: milestone-era-artifact-purge

Status: implemented

## Problem

Colophon was built through a milestone-driven agent-automation process (M0–M8, heartbeat files, STATE.json, LAN agent-concurrency rules). That process is gone — the repo now lives on GitHub with no automation — but its residue was everywhere: 73 milestone tags across ~40 files, "local git only, never push" in both authoritative docs, references to four deleted docs (PLAN.md, GITHUB.md, AUTOMATION.md, ASSUMPTIONS.md) plus a fifth (VISUAL-QA.md) found mid-cleanup, a corrupted T12 row in DECISIONS.md that had fused with the Engineering-culture section, and a doctor.sh that permanently reported RED because its STATE.json and heartbeat inputs no longer exist. Every one of these pointed new contributors at workflow machinery that no longer exists.

## Decision

Docs now describe only the shipped product. docs/DECISIONS.md lost T13 (agent concurrency) and all [ASSUMPTION]/"veto" framing; the Engineering-culture section is restored as a proper section and reads "AGPL-3.0 licensed. Development happens on GitHub."; D4 now says `app/genres.py` (the regenres.py typo is gone). docs/SPEC.md §16 (Build order / PLAN.md) is deleted. scripts/doctor.sh keeps exactly three checks — dirty working tree, schema drift vs alembic head, and the optional --tests gate — and no longer reads STATE.json or .run/heartbeat; the `make beat` target is removed. All M0–M8 tags are stripped from app/ headers, templates, static CSS/JS banners, tests/ headers, and alembic migration docstrings; prose that depended on them was reworded to name the real thing ("seed fixture", "the auth layer", "the cover cache"). Negative guarantees: no executable Python behavior changed (comments/docstrings only; py_compile-verified), tests were not run in this sandbox (no .venv; the changes cannot affect them), and `purge_expired_sessions` in app/auth.py — whose "called by the backup/health path" docstring was removed — is in fact called by nothing and was deliberately LEFT in place pending a real dead-code decision.
## Alternatives considered

**Deleting doctor.sh and `make doctor` outright.** Lost because the schema-drift check catches a real failure mode (dev DB behind head 500s on login) that nothing else reports. **Keeping T13 and the [ASSUMPTION] flags as historical record.** Lost per the owner's explicit choice in this session — the repo's history is the archive, and a locked-decisions table describing agent concurrency for a missing AUTOMATION.md misleads more than it informs. **Rewriting git history to purge milestone commit messages.** Rejected as out of scope: history is immutable record and the working-tree docs are what readers consult. **Deleting `purge_expired_sessions`.** Rejected this pass: removing a function is a code change that deserves its own review, not a comment-cleanup commit.
## Consequences

The trade bought docs that a stranger can trust at face value and a doctor that exits GREEN on a healthy tree instead of manufacturing a RED. It cost: `COLOPHON_STALE_MINUTES` and the heartbeat protocol no longer exist (anything that still runs `make beat` gets an unknown-target error); the GREEN/RED output lost the milestone/status fields; and the repo no longer carries any record of the agent-concurrency rules beyond git history. Follow-ups surfaced but not done: `purge_expired_sessions` is dead code, and README's quickstart omits `make migrate` (`make dev` alone 500s on a fresh clone).

