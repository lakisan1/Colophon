# Session-expiry verification at the HTTP layer

- **Class:** testing
- **Topic:** session-expiry-http-layer-verification
- **Date:** 2026-11-01

## Problem

The user suspected login tokens never expire ('login tokeni nikad ne ističu'). The expiry maths
existed and were unit-tested in tests/test_auth.py with injected clocks, but nothing proved the HTTP
layer enforces them end to end, and the cookie max-age constant (app/cookies.py SESSION_MAX_AGE) and
the server-side cap (app/auth.py ABSOLUTE_LIFETIME) were separate definitions that could drift
silently — either direction leaves a reader holding a cookie for a session that no longer exists, or
a live session the browser already dropped.

## Decision

Verified rather than assumed, then pinned. Traced the full chain: create_session writes expires_at =
created_at + 90 days at login; deps.current_user → load_session on every request rejects and revokes
any session past expires_at; touch_session slides to last-use + 30 days clamped under the cap.
Conclusion: expiry IS enforced — no bug. Added tests/hardening/test_hardening_session_expiry.py with
two tests: (1) a live TestClient whose session row is forced into the past gets 303 to /login on the
next request (proves the running app, not just the maths, signs expired sessions out); (2)
SESSION_MAX_AGE == int(ABSOLUTE_LIFETIME.total_seconds()) as a drift tripwire between the
browser-side and server-side lifetimes. Full suite green (516 passed), ruff clean.

## Alternatives considered

- **Fixing an expiry bug.** None found — the suspicion did not survive tracing load_session /
  touch_session / current_user; the honest outcome was verification plus coverage, not a code change.
- **Shortening the 30/90-day lifetimes or adding an idle-timeout knob.** Lost: SPEC §13 documents
  these values and they are reasonable for a personal library; changing security posture on an
  unfounded suspicion would trade usability for nothing measurable.
- **A background sweeper for expired rows.** Lost: load_session already deletes an expired row on
  first touch, so rows cannot accumulate beyond one per abandoned session; a sweeper adds a moving
  part for no observable gain.

## Consequences

Bought: proof at the HTTP layer that expired sessions are signed out, and a visible tripwire that
fails make test if anyone changes one lifetime constant without the other. Cost: none in runtime
behavior — this round changed only tests and documentation of the existing contract. The verification
is recorded on the closed kanban card 'Verify and fix session-expiry enforcement end to end' so the
user's question has a durable answer.