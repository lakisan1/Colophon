# Assumptions log

Every decision the assistant made without asking, newest first. Lazar's morning job is to veto entries,
not to answer questions. Format: date, what was decided, one-line reason, what to change if vetoed.

- 2027-08-27 — Shelf scale/LOD/zoom model written into DECISIONS.md (A1–A8). Reason: highest-risk item,
  needs a concrete model before code, and Lazar was going to sleep. Veto = edit A1–A8 and rerun M3.
- 2027-08-27 — Genre list curated to ~45 BISAC entries (fiction at second level). Reason: raw 51
  top-levels bury all fiction in one bucket. Veto = edit `app/genres.py`, nothing else depends on the list shape.
- 2027-08-27 — Format enum drafted (~13 values). Reason: D1 requires an enum, no source existed. Veto = edit `app/formats.py`.
- 2027-08-27 — `sqlite3` CLI absent on this machine → Python backup API used. Forced by the environment.
