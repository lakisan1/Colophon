# Assumptions log

Every decision the assistant made without asking, newest first. Lazar's morning job is to veto entries,
not to answer questions. Format: date, what was decided, one-line reason, what to change if vetoed.

- 2027-08-27 (M0→M1) — **T12 was wrong about its own source and has been corrected in place.**
  `data/bisac.json` contained a 14-byte `404: Not Found` body — an earlier fetch saved the error
  text as the file. Re-downloaded from `raw.githubusercontent.com/edsu/bisac/master/bisac.json`:
  3,737 entries (that number was right), 700,893 bytes, sha256
  `0a2a6f2807d7af16649714d1ab5f6d37e68b6ddc42775d072bc8c100276ccf9a`. The correction that changes
  code: **the JSON is keyed by label, and the code sits in `notation`**, so `app/genres.py` must key
  on `notation`; "51 top-level" counts first path segments, while three-letter notation prefixes
  number **53**. Verified too: `FIC` = 98 entries / 54 second-level labels, and **zero** labels
  contain `Young Adult`, so the `LOCAL/YA` deviation stands. Reason: M1 builds `genres.py` on this
  file and would otherwise have keyed the curated list on the wrong thing. Veto = revert T12/SPEC §4
  and re-vendor, but the file shape is a fact, not a choice.
- 2027-08-27 (M0) — `requirements.txt` pins **every** installed distribution (transitives included) from a
  real Python 3.12 install; `requirements-dev.txt` re-lists `httpx` at the identical pin because
  starlette's TestClient needs it. Reason: byte-identical environments (T4). Veto = re-pin from a fresh venv.
- 2027-08-27 (M0) — Env naming: `COLOPHON_` prefix for all knobs **except** `GOOGLE_BOOKS_KEY`, which T5
  names literally. Reason: stay faithful to the decision text. Veto = rename in `app/settings.py`.
- 2027-08-27 (M0) — A relative `COLOPHON_DB_PATH` / `COLOPHON_UPLOAD_DIR` resolves against the project root,
  not the current working directory. Reason: alembic and uvicorn can then never open two different files.
  Veto = edit `app/settings.py`.
- 2027-08-27 (M0) — `current_user` before M5: bypass ON returns a placeholder `CurrentUser(id=0,
  username="dev")`; bypass OFF raises `LoginRequired`, which the app renders as a **303 redirect to
  `/login`**. A placeholder `/` route is guarded by the dependency so the redirect is testable now; the
  `/login` stub answers 501 until M5. Veto = `app/deps.py`, `app/main.py`, `app/routers/pages.py`.
- 2027-08-27 (M0) — `base.html` renders `data-theme="light"` by default via a `theme` template variable;
  the user-facing theme switcher is M5. Reason: dark tokens must exist from day one. Veto = template + tokens.
- 2027-08-27 (M0) — `docs/SPEC.md` documents `app/genres.py`; `DECISIONS.md` D4 writes `app/regenres.py`,
  which is a typo (PLAN and earlier assumptions say `app/genres.py`). Veto = fix D4 if it meant otherwise.
- 2027-08-27 (M0) — `.gitignore` gained `.pytest_cache/` and `.ruff_cache/`; pytest/ruff config lives in
  `pyproject.toml`, which carries no `[project]` table because Colophon is an app, not a package. Veto = move
  tool config or drop the ignores.
- 2027-08-27 — Shelf scale/LOD/zoom model written into DECISIONS.md (A1–A8). Reason: highest-risk item,
  needs a concrete model before code, and Lazar was going to sleep. Veto = edit A1–A8 and rerun M3.
- 2027-08-27 — Genre list curated to ~45 BISAC entries (fiction at second level). Reason: raw 51
  top-levels bury all fiction in one bucket. Veto = edit `app/genres.py`, nothing else depends on the list shape.
- 2027-08-27 — Format enum drafted (~13 values). Reason: D1 requires an enum, no source existed. Veto = edit `app/formats.py`.
- 2027-08-27 — `sqlite3` CLI absent on this machine → Python backup API used. Forced by the environment.
