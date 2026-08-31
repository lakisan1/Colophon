# Colophon — Full Feature List 📚

A comprehensive, categorized list of every feature in Colophon. Add to it as the app evolves!

## 📚 Library & Catalog
- **Complete book metadata**: title and subtitle, authors, illustrator, translator, editor, edition, ISBN
  (validated, auto-normalised), format, publisher, publication year, print year, page count, language,
  original title and original language.
- **Series support**: reuse an existing series or type a new one (created on save), plus a per-book series
  position so part 1.5 sits where it belongs.
- **Genre**: pick a curated BISAC genre or write your own free-text genre.
- **Tags**: attach any number of custom labels per book — book club, reference, "read with the kids" — with
  a pick-or-type-new field and a tag filter.
- **Condition & provenance**: 1–5 condition rating, price, where bought, gifted-to, signed flag,
  sprayed-edges flag, inscription, and free-form notes.
- **Physical dimensions**: height/width/thickness (mm) and a cloth spine hue, used to draw true-to-size spines.
- **Autofill everywhere**: Authors, Illustrator, Translator, Editor, Series, Tags and the free-text genre
  all offer a dropdown of existing values while still accepting a brand-new one.

## 🗄️ Shelves
- **Four lifecycle shelves**: My library (owned), Wishlist (wanted), Borrowed (on loan), Gone (sold/gifted).
- **Wooden-shelf rendering**: CSS-drawn shelves with a lip, bevel and end-caps; spines drawn at true relative
  width using each book's measured dimensions and a realistic cloth colour.
- **Genre sections** on the library shelf, each labelled with a wooden plaque.
- **Zoom presets** (S / M / L) that change book density without changing proportions.
- **Flip-to-cover**: a book that has a photo shows its cover when flipped.
- **Gone & Borrowed views**: tabular lists with who/where/price for sold, gifted and loaned books.

## 🔍 Search & Filters
- **Full-text search** (SQLite FTS5) across title, author, ISBN, notes and more.
- **Advanced filters**: author, tag, genre, year, reading state, room, and no-room.
- **Scope control**: the library search can switch to the gone shelf, borrowed shelf, or search **all**
  shelves (incl. gone & borrowed) with one checkbox.
- **Filter links that persist**: changing shelf keeps your filters; export honours them too.

## ➕ Add & Lookup
- **Provider lookup**: type an ISBN or a title + author and Colophon fetches metadata from **Open Library**,
  **Google Books** or **ISBNdb** to pre-fill the form — tick the keyed ones in Settings, and each
  reader may paste their own API key there. Nothing saves until you press Save.
- **Manual entry** with validation (ISBN format, year bounds, positive dimensions).
- **Duplicate warning**: an ISBN or title you already own raises a heads-up but still saves.
- **Oversize confirmation**: a dimension implausibly large for a book asks before saving.

## 📷 Photos
- **Upload** cover photos per book into multiple slots (front/side/back...), with a crop region.
- **EXIF stripped** on save (including GPS) — a privacy guarantee.
- **Provider cover caching** auto-downloads a cover on lookup; a manual upload always wins.
- **Broken-file detection**: a recorded slot with no file is reported instead of showing a broken image.

## 🏷️ Organization
- **Rooms**: assign a book to a room and filter by it; filter for books without a room.
- **Genres**: curated BISAC genre tree plus your own free-text genre.
- **Series**, **author** and **tag** entities reused and shared across books.

## 📊 Statistics, Export & Data
- **Statistics** page: owned / wishlist / borrowed / sold / gifted counts, reading-state breakdown,
  books recorded, pages owned, authors, series, currently-out count, and total value.
- **Export** to **CSV** and **JSON**, filtered by the current search/filter state.
- **Backup & restore**: `scripts/nightly-backup.sh` snapshots the SQLite database and photos; a real
  backup/restore round-trip is covered by tests.

## 🔐 Accounts & Settings
- **Real login** with password hashing, HTTP-only session cookies, a 30-day sliding expiry and a 90-day cap.
- **CSRF tokens** on every mutating form.
- **Lockout** on repeated failed logins keyed to the IP and username.
- **User management** for the admin account: create and delete users.
- **Settings**: light/dark theme, shelf zoom preset, date/time format and currency symbol.

## 💻 Tech & Foundation
- **Stack**: FastAPI + SQLAlchemy + Alembic + Jinja2 + SQLite (WAL) + FTS5. Vanilla JS; no SPA, no bundler,
  no front-end framework, no CDN dependency.
- **Migrations**: incremental Alembic revisions, applied automatically on `make serve`.
- **Tests**: 400+ pytest tests (render, auth, validation, filters, shelf geometry, export, uploads, stats).
- **Tooling**: `make lint` (ruff), `make fmt` (ruff format), `make seed` (210-book demo fixture).
- **Container**: single Docker image + `docker compose up --build`, with the database and uploads on a volume.
- **Single-worker uvicorn** by design — SQLite writes and the in-memory rate limiter assume one process.
