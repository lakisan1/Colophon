# Colophon 📚

**Colophon** is a self-hosted catalog for a personal library of **physical** books. What you own, what
you want, what you lent out, what you sold or gave away — kept on shelves that look like real wooden
shelves, with spines drawn at true proportions and full publishing metadata behind every book.

Open source, free to use and modify as you wish. Fully local.

![My library](README_Images/Shelf/Library.png)

## 🌟 Key Features

For a complete, categorized list of every feature, see the [Full Feature List](FEATURES.md).

Real-looking wooden shelves, search that covers everything, and publishing data you'd expect from a book
shop — all on your own machine.

### 📚 Library & Catalog
Catalog every aspect of a physical book — authors, series, publisher, ISBN, page count, language,
condition, and more.

- **Complete metadata**: title, subtitle, authors, illustrator, translator, editor, edition, ISBN,
  format, publisher, publication & print year, pages, language, original title & language.
- **Series**: pick an existing series or type a new one; a position lets you order books *within* the run.
- **Rich condition tracking**: a 1–5 condition rating, price, where bought, signed and sprayed-edge flags,
  and free-form notes/inscriptions.
- **Autofill everywhere**: Authors, Illustrator, Translator, Editor, Series, Tags and the free-text genre
  all offer a dropdown of what you've already used — but still accept a brand-new value.

### 🗄️ Shelves
Four shelves map the life of a book in your hands.

- **My library** — what you own, drawn on wooden shelves with true-to-mm spine widths and cloth colors.
- **Wishlist** — books you want.
- **Borrowed** — what's out on loan, to whom and since when.
- **Gone** — what you sold or gave away, with the to-whom and price.
- **Zoom presets** (S / M / L) and flip-to-cover on every spine.

![Wishlist](README_Images/Shelf/Wishlist.png)

### 🔍 Search & Filters
One search box plus a powerful Advanced panel.

- **Full-text search** across title, author, ISBN, notes and more (SQLite FTS).
- **Advanced filters**: author, tag, genre, year, reading state, room, and a *search scope* toggle that
  can also cover gone & borrowed books.

![Borrowed](README_Images/Shelf/Borrowed.png)

### ➕ Add & Lookup
Add a book in seconds.

- **Lookup**: type an ISBN or a title+author and Colophon fills the form from Open Library or Google Books.
- **Manual entry**: every detail is optional; the shelf still draws a realistic spine either way.
- **Photos**: upload, crop, and EXIF are stripped on save; a provider cover is cached automatically.

![Add a book](README_Images/Book/AddBook.png)

### 📊 Statistics, Export & Data
Know your library and take it with you.

- **Statistics** page — owned / wishlist / borrowed / sold / gifted counts, reading states, total value.
- **Export** to CSV or JSON, honouring the current filters.
- **Backup** — scripts/nightly-backup.sh snapshots the SQLite database and uploads.

![Statistics](README_Images/Stats/Stats.png)

### 🔐 Accounts & Settings
- **Real login**, HTTP-only sessions with a sliding expiry, CSRF on every mutation, and IP+user lockout.
- **Settings** for theme (light/dark), shelf zoom, date/time and currency formats.

## 🚀 Quickstart

Requires Python 3.12+ and SQLite.

```sh
make venv && make dev
```

Then open <http://localhost:8000>. `make dev` runs a single-worker uvicorn with reload and sets
`COLOPHON_DEV_AUTH_BYPASS=1`, so no login is required during development. `make serve` runs the same server
with **login required** — that's what you run to use the app for real. Both listen on `0.0.0.0`, so other
devices on your network can open it at `http://<this-machine's-lan-ip>:8000`.

The demo account is `demo` / `demo` until you change it in **Settings**.

## 🛠️ Commands

| Target | What it does |
|---|---|
| `make venv` | Create `.venv` and install pinned runtime + dev dependencies |
| `make dev` | Dev server on `0.0.0.0:8000`, reload on, auth bypassed |
| `make serve` | Same server with **login required** — run this to use the app |
| `make seed` | (Re)build the demo user's 210-book fixture library |
| `make test` | `pytest -q` |
| `make lint` | `ruff check .` |
| `make fmt` | `ruff format .` |
| `make migrate` | `alembic upgrade head` |

Everything the app stores lives under `data/` — the SQLite file, `data/bisac.json` (BISAC genre data) and
`data/photos/` (uploaded images). That directory is the container volume mount; it is gitignored, and
deleting it deletes your library, so back it up first.

Container alternative: `docker compose up --build` (SQLite file and uploads live in the `colophon-data`
volume; env vars are documented in `docker-compose.yml`).

## ⚙️ Environment

All variables are optional in development:

| Variable | Purpose |
|---|---|
| `COLOPHON_SECRET_KEY` | Cookie signing key; falls back to an obviously-insecure dev value |
| `COLOPHON_DB_PATH` | SQLite file path (default `data/colophon.sqlite3`) |
| `COLOPHON_UPLOAD_DIR` | Uploaded photos directory (default `data/photos/`) |
| `COLOPHON_DEV_AUTH_BYPASS` | `1` skips login — development only |
| `GOOGLE_BOOKS_KEY` | Optional Google Books lookup key; the app works fully without it |

## 🧱 Tech

- **Backend**: FastAPI + SQLAlchemy (`text()` SQL) + Alembic migrations
- **Frontend**: Jinja2 server-rendered templates + a little vanilla JS. No SPA, no bundler, no framework CSS.
- **Data**: SQLite (WAL), FTS5 full-text search
- **Tests**: pytest — 400+ tests, green

## ❌ What this is **not**

This is **not an e-book reader**. Colophon handles physical books only — no e-books, no audiobooks, no
reading functionality of any kind. It is also **not a social network**: the only publicly visible page is
the login screen.

## 📄 License

AGPL-3.0-only — see [LICENSE](LICENSE).
