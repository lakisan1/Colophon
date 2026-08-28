# Locked decisions

Authoritative delta over `README.md`. Where this file and `README.md` disagree, **this file wins**.
Every entry was decided by Lazar (2027-08-27) or is a documented assumption by the assistant marked `[ASSUMPTION]`.

## Product

| # | Topic | Decision |
|---|---|---|
| D1 | Scope | Physical books only. No e-books, no audiobooks. |
| D2 | Identity model | **One row = one physical book.** No Work / Edition / Copy hierarchy. Two identical books with different annotations are two independent rows. Optional `same_edition_as_id` self-link exists so "how many of this do I have" is possible later. |
| D3 | Duplicate handling | Warning only, never blocks, on ISBN match then title+author match. Suggested action: "add as another copy of the same edition" sets `same_edition_as_id`. |
| D4 | Genres | Hardcoded curated list, keyed by BISAC notation. No user-defined genres in the UI. Extending the list = a code change in `app/regenres.py`. See curated list below. |
| D5 | Shelf placement | A book has exactly **one** primary genre → one shelf section. Extra genres for filtering only. |
| D6 | Sort order | Section by genre → within section: series number (nulls last) → author last name → title. |
| D7 | Rooms | `rooms` table, nullable `room_id` on a book (answer to "office / living-room cabinet"). Filter, not a grouping level. |
| D8 | Formats | Hardcoded enum, book-shaped physical items only. See list below. |
| D9 | Series | Name + number, decimals allowed (1.5). Series badge on the spine. |
| D10 | Multi-user | Schema is multi-user from day one (`owner_id` on every row-owning table). v1 ships one `admin` account, **no registration UI**. |
| D11 | Default credentials | `admin` / `admin` + persistent non-hideable warning banner. **Warn only** — data entry is not blocked. |
| D12 | Reminders | No overdue reminders. The due date is displayed; that is the whole feature. |
| D13 | Import | No import in v1. Library is entered by hand. Export = CSV + JSON. |
| D14 | Language | Everything in English: UI, code, comments, docs, commit messages. |

## Shelf rendering (the risky part — all `[ASSUMPTION]`, veto in the morning)

| # | Topic | Decision |
|---|---|---|
| A1 | Scale | Height is always strictly linear in mm. Reference height 240 mm. `scale = shelf_row_px / 240`. |
| A2 | Zoom presets | S=160px, M=220px, L=300px row height. Persisted per user. Proportions are exactly true at L. |
| A3 | Spine width | `spine_px = clamp(thickness_mm * scale, MIN_SPINE[zoom], 90)`. Clamping is a deliberate, zoom-dependent deviation from strict proportionality so thin spines stay clickable. |
| A4 | Level of detail | `>=26px`: rotated title + author + series badge. `14–25px`: title only. `<14px`: colour sliver + tooltip. |
| A5 | Spine art | Real uploaded spine photo when present, otherwise generated cloth spine (CSS gradient + SVG noise), colour = stable hash of author name into a theme-safe HSL band. |
| A6 | Cover view | Selecting a book flips the spine (CSS 3D transform, disabled under `prefers-reduced-motion`) to the front cover. Front cover also shown in the detail panel. |
| A7 | Font | Must include Latin Extended-A (č ć ž š đ) for titles. SIL OFL only. |
| A8 | Mobile | Horizontal scroll per genre section, sticky section label. No grid fallback exists — confirmed requirement. |

## Technology

| # | Topic | Decision |
|---|---|---|
| T1 | Stack | FastAPI + Jinja2 + HTMX. No SPA, no bundler, no React. Vanilla CSS with theme tokens. |
| T2 | DB | SQLite, one file, WAL mode. Single uvicorn worker (in-memory rate limiter + SQLite writes both need this). |
| T3 | Migrations | Alembic, `upgrade head` at container start. |
| T4 | Deps | Pinned `requirements.txt`, `.venv` in the project, Python 3.12. |
| T5 | Lookup | OpenLibrary default. Google Books behind an optional `GOOGLE_BOOKS_KEY` env var — the app works fully without it. Lookup results cached server-side with the raw provider payload. |
| T6 | Images | Downloaded covers stored locally (never hotlinked). Uploaded photos stored per copy/slot with generated filenames. Provider cover and user photo are **separate columns** — a re-lookup never overwrites a user's photo. |
| T7 | EXIF | Stripped server-side on upload via Pillow. Re-encoded to WebP, max 1600px, `X-Content-Type-Options: nosniff` when served. |
| T8 | Auth | bcrypt, DB-backed sessions, 30-day sliding expiry with a 90-day hard cap, CSRF tokens on every mutation, rate limit keyed on IP **and** username, CLI unlock script (`python -m app.tools.unlock`). |
| T9 | Reverse proxy | Final deployment is behind a proxy. uvicorn must get `FORWARDED_ALLOW_IPS` or rate limiting will lock the proxy IP. |
| T10 | Backups | Nightly, 30-day retention, includes uploaded photos. **No `sqlite3` CLI on this machine** → use Python's `sqlite3` module backup API. Backup dir must never be web-served. |
| T11 | Deploy | Docker, Komodo stack. Domain and machine chosen by Lazar at deploy time, out of spec. |
| T12 | BISAC source | `edsu/bisac` @ master `bisac.json`, re-verified against the downloaded file: **3,737 entries**, 700,893 bytes, `sha256 0a2a6f28…3512ed` → full: `0a2a6f2807d7af16649714d1ab5f6d37e68b6ddc42775d072bc8c100276ccf9a`. **Vendored into `data/bisac.json`** — no runtime fetch. **File shape (this is the part that was wrong before):** the JSON object is keyed by *label* (`"Fiction / Mystery & Detective / Women Sleuths"`), and the BISAC code is in the `notation` field (`FIC021060`). Earlier text said "51 top-level": that counts distinct **first path segments**; there are **53 distinct three-letter notation prefixes**. Fiction (`FIC`) holds 98 entries under 54 second-level labels, which is why Fiction is expanded to the second level to surface Mystery & Detective / Suspense / Horror. `Young Adult` appears in **zero** labels, so the `LOCAL/YA` entry stays a deliberate deviation. Consequence for code: `app/genres.py` keys on `notation` and stores the label alongside it for matching provider subjects — keying the list on the JSON key would give labels, not codes. |

## Engineering culture (Lazar, #14)

Write for a lazy reader. Boring code, obvious names, no `temp`/`utils2`/`new_final`. Every module opens with 2–3 lines saying *why* it exists.
No premature abstraction — three similar lines beat a helper nobody else can follow. No framework magic, no metaprogramming, no generic base classes.
Prefer plain SQL the reader can reason about over ORM tricks that hide the query. Fix technical debt the moment it is noticed rather than leaving a TODO.
AGPL-3.0 licensed. Local git only, one commit per milestone, **never push** (no remote configured; see `docs/GITHUB.md`).
| T13 | Agent concurrency | At most **one DeepSeek and one Qwen at the same time**; never two of the same model. Parallel workers must own **disjoint file sets** (see `docs/AUTOMATION.md`); `app/main.py` and `app/deps.py` stay with the orchestrator. Supersedes the earlier "serial model calls only" line. Decided by Lazar 2026-08-28. |
