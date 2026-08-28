# Automation and liveness

Why this file exists: the M2 agent died and nothing noticed for 8 hours. The failure was not the crash,
it was that **no one was awake to see it**. This file records what can wake the run, what cannot, and the
rules that keep parallel agents from colliding.

## What can wake the run

| Mechanism | Wakes the agent? | Notes |
| --- | --- | --- |
| Completion goal (`create_goal`) | **Yes** | The only real self-continuation. Rounds keep going without Lazar poking. Dies with the session. |
| Subagent completion notification | **Yes, mid-round** | A death is noticed while a round is active — which is exactly when it was *not* noticed on M2. |
| `make beat` / `.run/heartbeat` | No | Liveness evidence only. Nothing reads it unless something else is awake. |
| `scripts/doctor.sh` | No | Truth on demand. Run it at session start, from CI, or from a timer that alerts Lazar. |
| systemd user timer / cron | **No — alerts Lazar only** | `systemctl --user` and `crontab` both exist on this machine, but a timer runs a script; it cannot start or wake an agent turn. |

Honest consequence: with no goal and no active turn, a stall is invisible until a human looks. The timer
options below exist to shorten that window, not to eliminate it.

## Heartbeat protocol

Anything doing work touches the heartbeat, and every milestone ends with a commit + a `STATE.json` update.

```
make beat            # touch .run/heartbeat (ignored file, mtime is the signal)
make doctor          # GREEN/RED in one line
make doctor CHECK=1  # also runs pytest (~7s)
./scripts/doctor.sh  # same thing without make; exit code is truthful here, make wraps it as Error 1
```

Doctor is RED when: heartbeat older than `COLOPHON_STALE_MINUTES` (default 90), `STATE.json` milestone
disagrees with `docs/PLAN.md`, or the tree is dirty (something stopped mid-edit). It never fixes anything.

## Model concurrency (T13)

At most **one DeepSeek and one Qwen at the same time**. Never two of the same model. This supersedes the
"serial model calls only" line that was in this file's first draft and in `docs/PLAN.md`.

Parallel agents are only allowed when their file sets are disjoint, because two agents editing the same
file lose work silently. Declared ownership:

| Milestone | Owns | Collision risk |
| --- | --- | --- |
| M3 Shelf UI | `app/templates/shelf*`, `app/static/**`, `app/routers/shelf.py`, `app/scale.py`, shelf tests | shares `app/main.py` wiring |
| M4 Add/edit + lookup + photos | `app/routers/books.py`, `app/services/**`, upload/crop code and tests | shares `app/main.py`, `app/deps.py` |
| M5 Auth + settings | `app/auth*`, `app/routers/auth.py`, `app/routers/settings.py`, auth tests | shares `app/main.py`, `app/deps.py` |
| M6 Shelves 2–4, search, stats, export | shelf routers + queries | **overlaps M3 — never parallel with M3** |
| M7 Hardening + packaging | `Dockerfile`, `scripts/**`, CI config, README | logically last |

Rule: `app/main.py` and `app/deps.py` belong to the orchestrator. A worker that needs wiring describes the
change in its report instead of editing shared files.

## Model servers: check these first

Both models are self-hosted on the LAN (`~/.dsh/settings.yaml`): `local` = `deepseek-v4-flash` on
`192.168.1.30:8000`, `local2` = `qwen3.8-flash-next` on `192.168.1.31:8080`. Hosted DeepSeek is never
used, and a failed call is reported, never re-routed to another provider.

```
curl -s -m 6 http://192.168.1.30:8000/v1/models   # deepseek
curl -s -m 6 http://192.168.1.31:8080/v1/models   # qwen
```

A stalled or empty agent report means run these two curls **before** reading any code. A server that is
down or thrashing looks exactly like an agent that gave up. Both answered on 2026-08-28, which is why
the 8-hour M2 stall was attributed to nobody-was-looking rather than to infrastructure.

Concurrency limit, restated from T13: one `local` and one `local2` in flight at most, never two of the
same. Background `bash` jobs are free to overlap — they make no model calls.

## Death protocol

When an agent dies (empty closing message, `list_agents` status without a report):

1. `list_agents` and `job_list` — confirm nothing is actually running.
2. Read the workspace, not the agent's word. Check `git status` for a mid-edit tree.
3. Re-verify every claim independently: run the migration on a throwaway DB, run the suite, boot the app.
   An agent that produced nothing and an agent that finished look identical from a dead message.
4. Commit or discard the leftovers before starting new work, so the next death is unambiguous.

On M2 this protocol caught two things a trusting read would have shipped: the dead agent had written
**nothing**, and its label ("M2 seed fixture") did not match the milestone I had assumed it was doing.
