# Colophon — full specification (English)

Colophon is a self-hosted inventory application for a personal library of **physical** books:
what you own, what you want to buy, what you lent out, what you sold or gave away — presented
as shelves that look like real wooden shelves, with proportions true to millimetres and full
publishing data.

This document consolidates the original Serbian README (preserved in git history) and every
locked decision from `docs/DECISIONS.md`. Where the two ever disagree, `docs/DECISIONS.md`
wins, and this file should be corrected to match it.

## 1. Scope and priorities

- Physical books only — no e-books, no audiobooks, no reading features (D1).
- What this is not: an e-book reader, a social network, or a public catalog. The only
  publicly visible page is the login screen.
- Primary design priority: shelves that look like a real cozy library — wooden boards,
  spines facing out, light and dark themes.
- Everything is in English: UI, code, comments, docs, commit messages (D14).
- Casual and pleasant to browse, but filtering and truthfulness of data come first: all
  filtering happens on the server, never only in the UI.

## 2. Data model

- **One row = one physical book** (D2). There is no Work / Edition / Copy hierarchy. Two
  identical books with different annotations are two independent rows.
- An optional `same_edition_as_id` self-link exists so "how many copies of this edition do
  I have" is answerable later (D2, D3).
- Book fields:
  - title; one or more authors (multiple authors per book)
  - ISBN, normalised from ISBN-10/13, with or without hyphens
  - publisher, publication year, edition
  - format (hardcoded enum, book-shaped physical items only, D8)
  - page count, language
  - genre(s) — see §4: exactly one primary genre, extras for filtering only (D5)
  - series: name + number; decimals allowed (1.5 for omnibuses/novellas) (D9)
  - price + where bought (optional; price is a plain number, §10)
  - collectible details: sprayed edge, signed, inscription/dedication or previous-owner
    note, free-form notes
  - photos: front cover, back cover, spine
  - physical dimensions: height x width x thickness in **integer millimetres**; either
    picked from the format or entered by hand
- **Rooms:** a `rooms` table and a nullable `room_id` on a book answer "office or
  living-room cabinet". A room is a filter, not a grouping level (D7).
- **Multi-user from day one:** every row-owning table carries `owner_id`. v1 ships a single
  `admin` account and no registration UI (D10).
- **Lifecycle:** every book has a permanent ID and is never auto-deleted. Deletion exists
  but is rare and always behind an explicit confirmation. Loans keep full history (to whom,
  when, why, expected return date).

## 3. Statuses and shelves

Two independent axes:

- **Ownership status:** owned / wanted (wishlist) / borrowed / sold / gifted.
- **Reading state:** unread / reading / finished / DNF.

Four shelves:

1. **My library** — copies in possession (the main shelf).
2. **Borrowed** — to whom (name), date, reason/note, expected return; history is kept.
3. **Gone** — sold (price + where) and gifted (to whom + note) share one shelf; a filter
   distinguishes them.
4. **Wishlist** — books you want to buy.

Search includes shelves 2 and 3 only via an explicit toggle (off by default).

Shelf organisation: sections **by genre**; within a section, sort by **series number
(nulls last), then author last name, then title** (D6).

## 4. Genres and the BISAC source (corrected per T12)

- Genres are a **hardcoded curated list keyed by BISAC notation**. No user-defined genres in
  the UI; extending the list is a code change in `app/genres.py` (D4). Note: `DECISIONS.md`
  writes `app/regenres.py` — a typo; the actual file is `app/genres.py`.
- **Source:** `edsu/bisac` @ master `bisac.json`, re-verified against the downloaded file:
  **3,737 entries**, 700,893 bytes, `sha256 0a2a6f2807d7af16649714d1ab5f6d37e68b6ddc42775d072bc8c100276ccf9a`.
  BISAC is the industry standard (BISG); `edsu/bisac` is its open, machine-readable rendition.
- The file is **vendored into `data/bisac.json`** in this repository — no runtime fetch (T12).
- **File shape:** the top-level JSON object is keyed by *label*, not by code.
  `"Fiction / Mystery & Detective / Women Sleuths"` maps to
  `{"notation": "FIC021060", "pref_label": …, "alt_label": […], "related": […]}`. There are
  **51 distinct first path segments** and **53 distinct three-letter notation prefixes**.
  `app/genres.py` is keyed on `notation` — D4 says "keyed by BISAC notation", and the JSON key
  would have given labels instead — and stores each label alongside it so provider subjects,
  which arrive as labels, can be matched.
- **Corrections to the original Serbian README:** Fantasy, Science Fiction, Suspense, Horror and
  Mystery are *not* top-level categories in `edsu/bisac`; Fiction (`FIC`, 98 entries under 54
  second-level labels) is therefore **expanded to the second level**. **No label contains
  `Young Adult` at all**; a `LOCAL/YA` entry is added as a deliberate, documented deviation (T12).
- The shipped list is curated to ~45 entries (see `docs/ASSUMPTIONS.md`, 2027-08-27) because
  the raw 51 top-levels bury all fiction in one bucket.
- OpenLibrary subjects and Google Books categories are **mapped** onto these entries where
  possible. Anything that does not fit falls back to free text with autocomplete from the
  existing library.
- A book has exactly **one primary genre → one shelf section**; extra genres exist for
  filtering only (D5).

## 5. Series

- A series is a name plus a position number; decimals are allowed (1.5) for omnibuses and
  novellas (D9).
- A series badge is drawn on the spine; series position is the primary sort within a genre
  section (D6). This matters a lot for large sequences (e.g. Warhammer / Horus Heresy).

## 6. Shelf rendering (A1–A8; all flagged `[ASSUMPTION]`, vetoable)

- **A1 Scale:** height is always strictly linear in millimetres. Reference height 240 mm;
  `scale = shelf_row_px / 240`.
- **A2 Zoom presets:** S = 220 px, M = 300 px, L = 400 px row height, persisted per user.
  Proportions are exactly true at L.
- **A3 Spine width:** `spine_px = clamp(thickness_mm * scale, MIN_SPINE[zoom], 90)`.
  Clamping is a deliberate, zoom-dependent deviation from strict proportionality so thin
  spines stay clickable.
- **A4 Level of detail:** >= 26 px: rotated title + author + series badge. 14–25 px: title
  only. < 14 px: colour sliver + tooltip.
- **A5 Spine art:** real uploaded spine photo when present; otherwise a generated cloth
  spine (CSS gradient + SVG noise) whose colour is a stable hash of the author name into a
  theme-safe HSL band.
- **A6 Cover view:** selecting a book flips the spine (CSS 3D transform, disabled under
  `prefers-reduced-motion`) to the front cover; the front cover also shows in the detail
  panel.
- **A7 Font:** must include Latin Extended-A (č ć ž š đ) for titles; SIL OFL licences only.
- **A8 Mobile:** horizontal scroll per genre section with a sticky section label. **No grid
  fallback exists** — confirmed requirement; search and filters work within the shelf.

## 7. Covers, metadata lookup, images

- Lookup by **ISBN or title**: OpenLibrary is the default and needs no key; Google Books and
  ISBNdb are keyed **per reader** — each reader pastes their own key in Settings, and a keyed
  provider cannot be enabled without one (no server-side key env var exists). Lookup
  results are cached server-side together with the raw provider payload (T5).
- The best cover is picked automatically; fully manual entry of every field is always
  possible.
- Covers are **downloaded and stored locally — never hotlinked**. Uploaded photos are stored
  per copy/slot under generated filenames. Provider cover and user photo live in **separate
  columns**, so a re-lookup never overwrites a user's photo (T6).
- Uploads (front cover, back cover, spine) support crop and must work straight from a phone
  camera in the browser.
- **EXIF is stripped server-side** with Pillow on upload; images are re-encoded to WebP with
  a 1600 px maximum dimension and served with `X-Content-Type-Options: nosniff` (T7).
- With no image available: generated cloth spine per A5.

## 8. Duplicates

- On add: warn when the ISBN matches, then when title + author match (D3).
- Suggested actions: "add as another copy of the same edition" (sets `same_edition_as_id`),
  "they are different", "cancel".
- The warning **never blocks** adding (D3).

## 9. Search and filters

- Search by title, author, year, genre, ownership status, reading state.
- A dynamic **filter by author** shows that author's books across all genre sections.
- All filtering is server-side (see §1).

## 10. Prices and settings

- Price is a **plain number**; Settings chooses the displayed currency symbol (default `€`).
  No currency conversion.
- Settings: date format (dd.mm.yyyy / mm.dd.yyyy / others) and time format; UI language is
  English; currency symbol; theme light/dark via a theme-selector architecture built for
  adding themes easily; zoom preset persisted per user (A2).

## 11. Statistics, export, import

- **Dashboard:** counts of owned, wishlist, borrowed, sold, gifted books.
- **Export:** CSV and JSON. **No import in v1** — the library is entered by hand (D13).

## 12. Reminders

- No overdue reminders. The due date is displayed; that is the whole feature (D12).

## 13. Security and access

- The public surface is **only the login screen** — no guest view, no public library.
- bcrypt password hashing; DB-backed HTTP-only session cookie; **30-day sliding expiry with
  a 90-day hard cap** (T8).
- CSRF tokens on every mutation. Login rate limiting keyed on **IP and username**, with
  lockout; CLI unlock script `python -m app.tools.unlock` (T8).
- Bootstrap account `admin` / `admin`; while default credentials are in use a **persistent,
  non-hideable warning banner** links to Settings. It warns only — data entry is never
  blocked (D10, D11).
- No 2FA for now; the schema leaves room for it later.
- Final deployment sits behind a reverse proxy: uvicorn must receive `FORWARDED_ALLOW_IPS`
  or rate limiting will lock out the proxy's IP (T9).

## 14. Technology and operations

- **Stack:** FastAPI + Jinja2 + HTMX. No SPA, no bundler, no React. Vanilla CSS with theme
  tokens (T1).
- **Database:** SQLite, one file, WAL mode. **Single uvicorn worker** — the in-memory rate
  limiter and SQLite writes both require it (T2).
- **Migrations:** Alembic; `upgrade head` runs at container start (T3).
- **Dependencies:** pinned `requirements.txt`, `.venv` inside the project, Python 3.12 (T4).
- **Backups:** nightly, 30-day retention, including uploaded photos. There is **no `sqlite3`
  CLI on the build machine**, so backups use Python's `sqlite3` module backup API. The backup
  directory must never be web-served (T10).
- **Deploy:** Docker, Komodo stack. The domain and machine are chosen by Lazar at deploy
  time and are out of spec (T11).

## 15. Engineering culture

Boring, obvious code written for a lazy reader: no `temp`/`utils2`/`new_final` files, every
module opens with 2–3 lines saying *why* it exists, three similar lines beat a helper nobody
else can follow, no framework magic or generic base classes, plain SQL over ORM tricks. Fix
technical debt the moment it is noticed instead of leaving a TODO.

Licensed AGPL-3.0. Local git only, one commit per milestone, never push (no remote — see
`docs/GITHUB.md`).

## 16. Build order

Milestones M0–M7 are defined in `docs/PLAN.md`: scaffold → schema → seed fixture → shelf →
add/edit + lookup + photos → auth + settings → shelves 2–4/search/stats/export → hardening +
packaging.
