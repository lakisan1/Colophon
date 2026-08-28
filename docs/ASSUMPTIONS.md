# Assumptions log

Every decision the assistant made without asking, newest first. Lazar's morning job is to veto entries,
not to answer questions. Format: date, what was decided, one-line reason, what to change if vetoed.

- 2027-08-27 (M1) — **`settings` = one global row + per-user override rows in one table.** `owner_id NULL`
  is the global row; a `CREATE UNIQUE INDEX ... ON settings (ifnull(owner_id, -1))` pins exactly one of them,
  because SQLite treats NULLs as *distinct* inside unique indexes — the first attempt with a partial unique
  index passed the migration but failed its test. Migration seeds the global row: `€`, `dd.mm.yyyy`, `24h`,
  `light`, zoom `M`. Resolution: user row → global row → hardcoded default. Reason: A2 mandates per-user zoom;
  SPEC §10 keeps the rest site-wide, but per-user overrides cost one nullable column. Veto = the index in the
  migration plus the resolution query in M5.
- 2027-08-27 (M1) — **Primary genre is the single FK column `books.primary_genre_notation`; `book_genres`
  holds only extra, filter-only genres (D5).** "Exactly one primary per book" holds by construction — a second
  primary cannot exist — so no flag column or partial index trick is needed. Veto = `book_genres.is_primary` +
  partial unique index instead; data migration is mechanical.
- 2027-08-27 (M1) — **`photos` = one row per book × slot (`front`/`back`/`spine`) with separate
  `provider_filename` and `upload_filename` columns.** T6's "separate columns" taken literally: a re-lookup only
  ever writes the provider column, display uses the upload first (`COALESCE`). Generated filenames, never blobs.
  Veto = one row per source instead (`source` column, unique per book+slot+source).
- 2027-08-27 (M1) — **`books.series_position` is REAL; "nulls last" (D6) is a query expression**, not a column
  property: `ORDER BY series_position IS NULL, series_position`. Verified by test and live demo: 1.0, 1.5, 2.0,
  NULL. Veto = change the ORDER BY in every shelf query.
- 2027-08-27 (M1) — **Curated genre list is exactly 45 entries**: 16 second-level `FIC`, 28 top-level
  nonfiction, plus `LOCAL/YA` (T12). Dropped top-levels (Bibles, Law, Medical, Education, Juvenile Nonfiction,
  Transportation, …) are one code change away in `app/genres.py`; the test pins the count to 40–55. **Codes were
  taken from the vendored file, and it does not match the DECISIONS example**: in `data/bisac.json` Women Sleuths
  is `FIC022040` (there is no `FIC021060`), so examples in DECISIONS/SPEC are illustrative only. Veto = edit the
  list; nothing else depends on which 45.
- 2027-08-27 (M1) — **The migration seeds `genres` from an inline copy of the curated list; it does not import
  `app/genres.py`.** Reason: migrations are historical records and must not change when app code changes.
  `test_genres.py` compares the table against `app/genres.py` row for row, so drift fails loudly. Veto = a
  runtime seed command (breaks the round-trip guarantee).
- 2027-08-27 (M1) — **`books.isbn` stores only the normalised ISBN-13** (`CHECK length(isbn) = 13`) and is
  deliberately **not unique** — D3 warns, never blocks. Normalisation lives in `app/isbn.py`; a 10-digit insert
  is a bug the DB catches. Veto = drop the CHECK (not advised) or add uniqueness (blocks D3).
- 2027-08-27 (M1) — **Author ordering: `authors.sort_key` ("Banks, Iain M.") lives on the author;
  `book_authors.position` (1 = first-listed) orders multiple authors; D6 sorts by the position-1 author's
  `sort_key`.** Veto = denormalise the sort key into `book_authors` (rename drift risk) or sort on another author.
- 2027-08-27 (M1) — **Link tables `book_authors` / `book_genres` also carry `owner_id NOT NULL`**, making D10
  mechanical — "every table except `users` and the curated `genres`" — and letting M6 filter without joining
  through books. Veto = drop the columns and scope via `books`.
- 2027-08-27 (M1) — **`books.format` CHECK list is hardcoded in the migration and mirrors `app/formats.py`
  (13 keys, each with typical mm dimensions).** The test inserts one book per key so the CHECK and the enum
  cannot drift. Veto = edit both plus rerun the test; M2's fixture picks dimensions from these.
- 2027-08-27 (M1) — **Storage conventions:** dates are ISO-8601 TEXT (`'2027-08-27'`; timestamps
  `strftime('%Y-%m-%dT%H:%M:%SZ','now')`, i.e. UTC); `price` is a REAL plain number (§10, no conversion);
  booleans are INTEGER 0/1 with explicit CHECKs; deleting a user cascades, deleting a room/series nulls book
  references, deleting a book cascades to links/loans/photos, `same_edition_as_id` nulls. Veto = targeted
  migration tweak.
- 2027-08-27 (M1) — **`users` is minimal** (`username` unique NOCASE, `password_hash`, `created_at`); session,
  login-attempt and rate-limit tables are deliberately deferred to M5 where PLAN scopes them; no admin row is
  seeded (M5 bootstraps). Veto = pull the session schema into M1.
- 2027-08-27 (M1→M2) — **`docs/QUESTIONS.md` stays empty**: nothing blocked. Three failures happened, once each, all fixed:
  an f-string nesting SQL quotes in the migration (SyntaxError, fixed with a `_sql_list()` helper), `IntegrityError`
  imported from `sqlalchemy.engine` instead of `sqlalchemy.exc` (collection ImportError), and the settings
  unique-index NULL quirk described above (found by its own test, fixed with `ifnull`).
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
