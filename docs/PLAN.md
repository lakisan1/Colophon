# Build plan

One milestone at a time, serial (single local model instance per global rule). A milestone is done only
when its verification command passes **and** the assistant has re-run that command itself. Two failures on
the same task → park it in `docs/QUESTIONS.md` and move to the next independent milestone.

- [x] **M0 Scaffold** — pinned `requirements.txt`, `.venv`, ruff, pytest, Alembic, `Makefile`, `docker-compose.yml`,
      `app/` package, `current_user` dependency with dev bypass, `/healthz`, `LICENSE` (AGPL-3.0),
      English `README.md` rewrite + `docs/SPEC.md` split, `docs/GITHUB.md`.
      _Verify:_ `make test` green, `make lint` clean, `uvicorn` boots and `curl /healthz` returns 200.
      _Done 2026-08-27, re-verified by the orchestrator:_ `pytest -q` → 5 passed, `ruff check .` → clean,
      `ruff format --check .` → 21 files already formatted, `import app.main` ok, `/healthz` → 200,
      pins confirmed installed (fastapi 0.141.1, uvicorn 0.52.4, SQLAlchemy 2.0.52, alembic 1.19.1,
      bcrypt 5.0.0, pillow 12.3.0, httpx 0.28.1).
- [x] **M1 Schema** — tables `users, books, authors, series, genres, rooms, loans, photos, settings`,
      migrations, ISBN-10/13 normalise+validate util, hardcoded `genres.py` / `formats.py`.
      _Verify:_ `alembic upgrade head && alembic downgrade base` clean; unit tests on ISBN.
      _Done 2026-08-27, re-verified by the orchestrator on a throwaway DB:_ upgrade → 12 tables + 45 seeded
      genres + seeded global settings row; `downgrade base` → only `alembic_version` survives; re-upgrade
      restores all 25 objects. `pytest -q` → 62 passed; `ruff check .` clean; `ruff format --check .` →
      29 files already formatted. Orchestrator also re-checked all 45 curated notations against
      `data/bisac.json`: zero missing codes, zero label mismatches, `FIC` = 98 entries, no label contains
      "Young Adult" (so `LOCAL/YA` is justified).
- [x] **M2 Seed fixture** — ~150 books with real mm dimensions, Horus-Heresy-style decimal series numbers,
      boxed sets, manga, dictionaries, borrowed + sold + wishlist entries.
      _Verify:_ row-count and series-ordering assertions; `make seed` idempotent.
      _Done 2026-08-28:_ `app/seed_data.py` (210 books — more than the ~150 asked for, still one shelf)
      plus `app/seed.py` (`make seed`, rebuilds the demo user only, so it is safe next to real data) and
      25 tests. The fixture covers all 5 ownership statuses, all 4 reading states, all 13 formats, decimal
      positions (0.25 / 0.5 / 1.5 / 2.5), 10 multi-author books including a 4-author one, 5 loans, 4 gifts,
      11 cached provider photo rows, and the two planned duplicate titles. `isbn` is deliberately NULL
      everywhere: inventing codes would make M6's Open Library lookups resolve to the wrong book.
      _Verified:_ `pytest -q` → 87 passed; `ruff check .` clean; `ruff format --check .` → 32 files;
      seeding twice returns identical counts; `make seed` on the dev DB → 210 books / 222 links / 5 loans.
- [x] **M3 Shelf** — scale model A1–A8, zoom presets, LOD, generated cloth spine, series badge, wooden rows
      with genre section labels, flip-to-cover, light/dark theme tokens.
      _Verify:_ rendered HTML snapshot tests + `docs/VISUAL-QA.md` checklist for Lazar.
      _Done 2026-08-28:_ `app/shelf.py` (pure mm→px model: A1 linear height, A3 clamp, A4 LOD, A5 hashed
      cloth), `app/shelf_store.py` (one plain-SQL query), `app/prefs.py`, `app/routers/shelf.py`,
      `app/templates/shelf.html`, `app/static/css/shelf.css`, `app/static/js/shelf.js`. 42 new tests.
      _Verified:_ `pytest -q` → 131 passed; `ruff check .` clean; `ruff format --check .` clean; live boot
      served `/` 200 with 181 owned spines in 37 labelled rows, `/?zoom=L` 200, `shelf.css` 200.
      Three real bugs found by those tests and fixed: the stored zoom preset was ignored in favour of the
      hardcoded default (A2), 300 mm folios were clipped by the fixed row height, and raw BISAC paths were
      being printed as shelf labels. One pre-existing vacuous assertion replaced
      (`"dev" in response.text` matched `width=device-width`).
      _Open for Lazar:_ `docs/VISUAL-QA.md` — geometry can only be signed off by eye, and A7 (OFL font with
      Latin Extended-A) is deliberately **not** met yet; the CSS uses a system serif stack instead.
- [ ] **M4 Add/edit + lookup + photos** — OpenLibrary (Google Books behind optional key), candidate cover picker,
      upload + crop, EXIF strip, duplicate warning.
      _Verify:_ endpoint tests against a fixture image and a stubbed lookup.
- [ ] **M5 Auth + settings** — login/logout, bcrypt, DB sessions 30d sliding / 90d cap, rate limit (IP+user),
      CSRF, warning-only default-credentials banner, settings page, CLI unlock.
      _Verify:_ negative tests — lockout triggers, CSRF rejection, cookie flags, session expiry math.
- [ ] **M6 Shelves 2–4, search, stats, export** — borrowed / gone / wishlist, server-side filtering,
      author-across-genres filter, FTS search, dashboard, CSV + JSON export, rooms filter.
      _Verify:_ filter truth-table tests; export round-trips through a parser.
- [ ] **M7 Hardening + packaging** — nightly backup script (Python `sqlite3` backup API + photos, 30d retention),
      non-root Dockerfile, healthcheck, `FORWARDED_ALLOW_IPS` docs, CI (ruff+pytest), CONTRIBUTING, final README.
      _Verify:_ `docker build` succeeds; backup script produces a restorable file in a temp dir.

## Standing constraints for every agent

Model calls: at most one DeepSeek and one Qwen at a time, never two of the same model (T13). Never push to git. Never touch files outside this project directory.
`docs/DECISIONS.md` wins over `README.md` on any conflict.
Heartbeat + `scripts/doctor.sh` + `STATE.json` keep the run observable: see `docs/AUTOMATION.md`. Read `docs/ASSUMPTIONS.md` before assuming anything new.
