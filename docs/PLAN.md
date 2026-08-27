# Build plan

One milestone at a time, serial (single local model instance per global rule). A milestone is done only
when its verification command passes **and** the assistant has re-run that command itself. Two failures on
the same task → park it in `docs/QUESTIONS.md` and move to the next independent milestone.

- [x] **M0 Scaffold** — pinned `requirements.txt`, `.venv`, ruff, pytest, Alembic, `Makefile`, `docker-compose.yml`,
      `app/` package, `current_user` dependency with dev bypass, `/healthz`, `LICENSE` (AGPL-3.0),
      English `README.md` rewrite + `docs/SPEC.md` split, `docs/GITHUB.md`.
      _Verify:_ `make test` green, `make lint` clean, `uvicorn` boots and `curl /healthz` returns 200.
      _Done 2027-08-27, re-verified by the orchestrator:_ `pytest -q` → 5 passed, `ruff check .` → clean,
      `ruff format --check .` → 21 files already formatted, `import app.main` ok, `/healthz` → 200,
      pins confirmed installed (fastapi 0.141.1, uvicorn 0.52.4, SQLAlchemy 2.0.52, alembic 1.19.1,
      bcrypt 5.0.0, pillow 12.3.0, httpx 0.28.1).
- [ ] **M1 Schema** — tables `users, books, authors, series, genres, rooms, loans, photos, settings`,
      migrations, ISBN-10/13 normalise+validate util, hardcoded `genres.py` / `formats.py`.
      _Verify:_ `alembic upgrade head && alembic downgrade base` clean; unit tests on ISBN.
- [ ] **M2 Seed fixture** — ~150 books with real mm dimensions, Horus-Heresy-style decimal series numbers,
      boxed sets, manga, dictionaries, borrowed + sold + wishlist entries.
      _Verify:_ row-count and series-ordering assertions; `make seed` idempotent.
- [ ] **M3 Shelf** — scale model A1–A8, zoom presets, LOD, generated cloth spine, series badge, wooden rows
      with genre section labels, flip-to-cover, light/dark theme tokens.
      _Verify:_ rendered HTML snapshot tests + `docs/VISUAL-QA.md` checklist for Lazar.
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

Serial model calls only. Never push to git. Never touch files outside this project directory.
`docs/DECISIONS.md` wins over `README.md` on any conflict. Read `docs/ASSUMPTIONS.md` before assuming anything new.
