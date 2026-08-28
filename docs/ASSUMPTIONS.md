# Assumptions log

Every decision the assistant made without asking, newest first. Lazar's morning job is to veto entries,
not to answer questions. Format: date, what was decided, one-line reason, what to change if vetoed.

- 2026-08-28 (**M3**) — **Dates in this log follow the machine clock, which reads 2026-08-28.** Entries
  above say `2027-08-27`. Reason: the clock is the only authority available at write time and the seed's
  loan dates already follow it. Veto = rename the older entries or fix the clock; nothing in code depends
  on either year.
- 2026-08-28 (**M3**) — **A7 is not met yet: no OFL webfont is vendored.** The shelf uses a serif stack
  ("DejaVu Serif", "Liberation Serif", "Noto Serif", …) chosen for Latin Extended-A coverage, so č ć ž š đ
  render on Linux/macOS/Windows. Reason: no font file could be fetched into the repo during this run, and
  shipping a shelf with tofu boxes is worse than shipping one on system fonts. Veto = drop a subset into
  `app/static/fonts/` and point `--shelf-font` at it; `docs/VISUAL-QA.md` item 10 tells Lazar what to check.
- 2026-08-28 (**M3**) — **Shelf rows grow past the zoom preset to hold the tallest book.** A1 says height
  is strictly linear and A2 says a row is 160/220/300 px, which collide: a 300 mm folio is 375 px at L and
  would be clipped by `overflow-y: hidden`. Chose to keep A1 strict and let `--row-height` rise to
  `max(preset, tallest spine)`. Veto = clamp tall books instead, which breaks A1 on exactly the books the
  fixture includes (atases, art books).
- 2026-08-28 (**M3**) — **A3's clamp numbers: `MIN_SPINE_PX` = S 12 / M 14 / L 16, ceiling 90 px.** The
  decision fixes the formula but not the floor per preset. Chosen so every preset yields at least two LOD
  tiers (asserted in `test_every_zoom_produces_more_than_one_lod_tier`) instead of collapsing the whole
  shelf into one tier. Veto = three numbers in `app/shelf.py`.
- 2026-08-28 (**M3**) — **Section labels show the BISAC leaf, and a leaf of "General" collapses to its
  parent.** "Fiction / Fantasy / General" as a wooden-row label is unreadable; the row reads "Fantasy".
  Veto = one function, `app/shelf.section_label`.
- 2026-08-28 (**M3**) — **Genre rows are ordered by `genres.sort_order`, not alphabetically.** The curated
  list already encodes an order (fiction first); alphabetical would contradict it. Veto = the sort key in
  `app/shelf.build_shelf`.
- 2026-08-28 (**M3**) — **The zoom preset is persisted by a GET (`/?zoom=L`), with no CSRF token.** A2
  demands the choice be remembered per user and M3 has no settings page. The write is scoped to the signed-in
  reader's own view and its worst case is a cosmetic change to their shelf. M5 replaces it with a
  CSRF-protected settings form. A `?zoom=` value that is not a preset is ignored rather than stored, so a
  typo in a shared link cannot overwrite the choice. Veto = drop persistence until M5.
- 2026-08-28 (**M3**) — **The M2 fixture's `isbn` comment and one test docstring said M6; the lookup
  milestone is M4.** Corrected in code and in `STATE.json`. Reason: `docs/PLAN.md` puts Open Library lookup
  in M4 and M6 is shelves/search/stats/export.
- 2026-08-28 (**M3**) — **`"dev" in response.text` was a vacuous test and has been replaced.** It passed
  only because the page contains `width=device-width`. The assertion now checks the shelf actually rendered.
  Reason: found while wiring M3, logged because a test that passes for the wrong reason is worse than none.

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
