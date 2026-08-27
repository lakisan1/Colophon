# Colophon

Self-hosted inventory for a personal library of **physical** books: what you own, what you
want, what you lent out, what you sold or gave away — displayed on shelves that look like
real wooden shelves, with true-to-mm proportions and full publishing data.

- **Stack:** FastAPI + Jinja2 + HTMX + SQLite (WAL). No SPA, no bundler, no framework CSS.
- **Status:** M0 scaffold — plumbing only. The shelf arrives in M3, login in M5 (see `docs/PLAN.md`).
- **Docs:** full specification in [`docs/SPEC.md`](docs/SPEC.md); locked decisions in
  [`docs/DECISIONS.md`](docs/DECISIONS.md) (authoritative), plus `PLAN.md`, `ASSUMPTIONS.md`,
  `QUESTIONS.md`.

## Quickstart

```sh
make venv && make dev
```

Then open <http://localhost:8000>. `make dev` runs a single-worker uvicorn with reload and
sets `COLOPHON_DEV_AUTH_BYPASS=1`, so no login is required during development (real
authentication is milestone M5).

## Commands

| Target | What it does |
|---|---|
| `make venv` | Create `.venv` and install pinned runtime + dev dependencies |
| `make dev` | Dev server on `0.0.0.0:8000`, reload on, auth bypassed |
| `make test` | `pytest -q` |
| `make lint` | `ruff check .` |
| `make fmt` | `ruff format .` |
| `make migrate` | `alembic upgrade head` |

Container alternative: `docker compose up --build` (SQLite file and uploads live in the
`colophon-data` volume; env vars are documented in `docker-compose.yml`).

Environment variables (all optional in dev):

| Variable | Purpose |
|---|---|
| `COLOPHON_SECRET_KEY` | Cookie signing key; falls back to an obviously-insecure dev value |
| `COLOPHON_DB_PATH` | SQLite file path (default `colophon.sqlite3` at the project root) |
| `COLOPHON_UPLOAD_DIR` | Uploaded photos directory (default `uploads/`) |
| `COLOPHON_DEV_AUTH_BYPASS` | Set to `1` to skip login — development only |
| `GOOGLE_BOOKS_KEY` | Optional Google Books lookup key; the app works fully without it |

## What this is not

**This is not an e-book reader.** Colophon handles physical books only — no e-books, no
audiobooks, no reading functionality of any kind. It is also not a social network: the only
publicly visible page is the login screen.

## License

AGPL-3.0-only — see [LICENSE](LICENSE).
