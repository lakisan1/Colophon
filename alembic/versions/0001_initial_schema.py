"""M1 initial schema: the nine PLAN tables plus the two link tables the many-to-many
relations need (book_authors, book_genres).

Conventions baked in here, all documented in docs/ASSUMPTIONS.md:
- One row = one physical book (D2); no Work/Edition/Copy hierarchy.
- owner_id on every row-owning table (D10), including link tables. Only `users`
  and the curated `genres` have none.
- dates are ISO-8601 TEXT ('2027-08-27' / '2027-08-27T09:30:00Z'); display
  formatting is an M5 settings concern.
- books.isbn holds the normalised ISBN-13 (CHECK length = 13); app/isbn.py does
  the normalising, duplicates are allowed (D3 warns, never blocks).
- books.series_position is REAL so 1.5 sorts between 1 and 2 (D9); NULLs are put
  last by the query, not the column (D6).
- A book's primary genre is the single column books.primary_genre_notation
  (D5: exactly one by construction); book_genres holds extra, filter-only genres.
- photos keep provider and user filenames in separate columns (T6) so a
  re-lookup can never overwrite a user's photo; display uses the upload first.
- `settings` is one global row (owner_id NULL) plus optional per-user override
  rows; the global row is seeded with the SPEC §10 defaults. The admin account
  is NOT seeded here — M5 owns auth and bootstrapping.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP_DEFAULT = sa.text("(strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))")

OWNERSHIP_STATUSES = ("owned", "wanted", "borrowed", "sold", "gifted")
READING_STATES = ("unread", "reading", "finished", "dnf")
BOOK_FORMATS = (
    "mass_market_paperback",
    "pocket_a6",
    "paperback",
    "trade_paperback",
    "manga",
    "hardcover",
    "large_print",
    "dictionary",
    "oversized_folio",
    "boxed_set",
    "leather_bound",
    "spiral_bound",
    "board_book",
)
PHOTO_SLOTS = ("front", "back", "spine")


def _sql_list(values: tuple[str, ...]) -> str:
    """Render ('a', 'b', 'c') for the CHECK constraints below."""
    return ", ".join(f"'{value}'" for value in values)


# Inline copy of app/genres.py (curated, D4). Migrations are historical records
# and must not import app code; test_genres.py fails if the two ever drift.
CURATED_GENRES: list[tuple[str, str, str]] = [
    ("FIC000000", "Fiction / General", "Fiction"),
    ("FIC002000", "Fiction / Action & Adventure", "Action & Adventure"),
    ("FIC004000", "Fiction / Classics", "Classics"),
    ("FIC008000", "Fiction / Sagas", "Sagas"),
    ("FIC009000", "Fiction / Fantasy / General", "Fantasy"),
    (
        "FIC010000",
        "Fiction / Fairy Tales, Folk Tales, Legends & Mythology",
        "Fairy Tales & Mythology",
    ),
    ("FIC014000", "Fiction / Historical", "Historical Fiction"),
    ("FIC015000", "Fiction / Horror", "Horror"),
    ("FIC019000", "Fiction / Literary", "Literary Fiction"),
    ("FIC022000", "Fiction / Mystery & Detective / General", "Mystery & Detective"),
    ("FIC024000", "Fiction / Occult & Supernatural", "Occult & Supernatural"),
    ("FIC027000", "Fiction / Romance / General", "Romance"),
    ("FIC028000", "Fiction / Science Fiction / General", "Science Fiction"),
    ("FIC030000", "Fiction / Suspense", "Suspense"),
    ("FIC031000", "Fiction / Thrillers", "Thrillers"),
    ("FIC032000", "Fiction / War & Military", "War & Military Fiction"),
    ("LOCAL/YA", "Young Adult (local entry, not BISAC)", "Young Adult"),
    ("ART000000", "Art / General", "Art"),
    ("BIO000000", "Biography & Autobiography / General", "Biography & Autobiography"),
    ("BUS000000", "Business & Economics / General", "Business & Economics"),
    ("CGN000000", "Comics & Graphic Novels / General", "Comics & Graphic Novels"),
    ("COM000000", "Computers / General", "Computers"),
    ("CKB000000", "Cooking / General", "Cooking"),
    ("GAM000000", "Games / General", "Games"),
    ("GAR000000", "Gardening / General", "Gardening"),
    ("HEA000000", "Health & Fitness / General", "Health & Fitness"),
    ("HIS000000", "History / General", "History"),
    ("JUV000000", "Juvenile Fiction / General", "Juvenile Fiction"),
    ("LCO000000", "Literary Collections / General", "Literary Collections"),
    ("MAT000000", "Mathematics / General", "Mathematics"),
    ("MUS000000", "Music / General", "Music"),
    ("NAT000000", "Nature / General", "Nature"),
    ("PHI000000", "Philosophy / General", "Philosophy"),
    ("PHO000000", "Photography / General", "Photography"),
    ("POE000000", "Poetry / General", "Poetry"),
    ("POL000000", "Political Science / General", "Political Science"),
    ("PSY000000", "Psychology / General", "Psychology"),
    ("REF000000", "Reference / General", "Reference"),
    ("REL000000", "Religion / General", "Religion"),
    ("SCI000000", "Science / General", "Science"),
    ("SEL000000", "Self-help / General", "Self-help"),
    ("SPO000000", "Sports & Recreation / General", "Sports & Recreation"),
    ("TEC000000", "Technology & Engineering / General", "Technology & Engineering"),
    ("TRV000000", "Travel / General", "Travel"),
    ("TRU000000", "True Crime / General", "True Crime"),
]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.Text(collation="NOCASE"), nullable=False, unique=True),
        # bcrypt hash, filled by M5. No admin row is seeded here on purpose.
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False, server_default=TIMESTAMP_DEFAULT),
    )

    genres_table = sa.table(
        "genres",
        sa.column("notation", sa.Text),
        sa.column("label", sa.Text),
        sa.column("section", sa.Text),
        sa.column("sort_order", sa.Integer),
    )

    def genre_row(index: int, notation: str, label: str, section: str) -> dict[str, object]:
        return {"notation": notation, "label": label, "section": section, "sort_order": index}

    op.create_table(
        "genres",
        # Curated reference data (D4): global, no owner_id, no user CRUD.
        sa.Column("notation", sa.Text, primary_key=True),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("section", sa.Text, nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False),
    )
    op.bulk_insert(
        genres_table,
        [
            genre_row(i, notation, label, section)
            for i, (notation, label, section) in enumerate(CURATED_GENRES)
        ],
    )

    op.create_table(
        "rooms",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(collation="NOCASE"), nullable=False),
        sa.Column("created_at", sa.Text, nullable=False, server_default=TIMESTAMP_DEFAULT),
        sa.UniqueConstraint("owner_id", "name", name="uq_rooms_owner_name"),
    )

    op.create_table(
        "authors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(collation="NOCASE"), nullable=False),
        # "Banks, Iain M." — the last-name sort key D6 orders shelves by.
        sa.Column("sort_key", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False, server_default=TIMESTAMP_DEFAULT),
        sa.UniqueConstraint("owner_id", "name", name="uq_authors_owner_name"),
    )
    op.create_index("ix_authors_owner_sort", "authors", ["owner_id", "sort_key"])

    op.create_table(
        "series",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(collation="NOCASE"), nullable=False),
        sa.Column("created_at", sa.Text, nullable=False, server_default=TIMESTAMP_DEFAULT),
        sa.UniqueConstraint("owner_id", "name", name="uq_series_owner_name"),
    )

    op.create_table(
        "books",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("subtitle", sa.Text),
        # Normalised ISBN-13 only; not unique, because D3 warns but never blocks.
        sa.Column("isbn", sa.Text),
        sa.Column("publisher", sa.Text),
        sa.Column("publication_year", sa.Integer),
        sa.Column("edition", sa.Text),
        sa.Column("format", sa.Text),
        sa.Column("page_count", sa.Integer),
        sa.Column("language", sa.Text),
        sa.Column(
            "room_id",
            sa.Integer,
            sa.ForeignKey("rooms.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "series_id",
            sa.Integer,
            sa.ForeignKey("series.id", ondelete="SET NULL"),
        ),
        # REAL sorts 1.5 between 1 and 2 (D9); NULL = not in a series, sorted last by D6 queries.
        sa.Column("series_position", sa.Float),
        sa.Column(
            "primary_genre_notation",
            sa.Text,
            sa.ForeignKey("genres.notation"),
        ),
        # SPEC §4 fallback for subjects with no curated entry.
        sa.Column("genre_free_text", sa.Text),
        sa.Column("price", sa.Float),
        sa.Column("where_bought", sa.Text),
        # The two independent status axes (SPEC §3). "Gone" = sold OR gifted;
        # this column is the filter that distinguishes them on that shelf.
        sa.Column("ownership_status", sa.Text, nullable=False, server_default=sa.text("'owned'")),
        sa.Column("reading_state", sa.Text, nullable=False, server_default=sa.text("'unread'")),
        # Gone-shelf fields: sold reuses price + where_bought, gifted stores these.
        sa.Column("gifted_to", sa.Text),
        sa.Column("gift_note", sa.Text),
        sa.Column("is_sprayed_edge", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("is_signed", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("inscription", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("height_mm", sa.Integer),
        sa.Column("width_mm", sa.Integer),
        sa.Column("thickness_mm", sa.Integer),
        # D2/D3: "this is another copy of that edition", set from the duplicate warning.
        sa.Column(
            "same_edition_as_id",
            sa.Integer,
            sa.ForeignKey("books.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.Text, nullable=False, server_default=TIMESTAMP_DEFAULT),
        sa.CheckConstraint("isbn IS NULL OR length(isbn) = 13", name="ck_books_isbn_normalised"),
        sa.CheckConstraint(
            "publication_year IS NULL OR (publication_year > 0 AND publication_year < 10000)",
            name="ck_books_publication_year",
        ),
        sa.CheckConstraint(
            f"format IS NULL OR format IN ({_sql_list(BOOK_FORMATS)})",
            name="ck_books_format",
        ),
        sa.CheckConstraint("page_count IS NULL OR page_count > 0", name="ck_books_page_count"),
        sa.CheckConstraint("height_mm IS NULL OR height_mm > 0", name="ck_books_height_mm"),
        sa.CheckConstraint("width_mm IS NULL OR width_mm > 0", name="ck_books_width_mm"),
        sa.CheckConstraint(
            "thickness_mm IS NULL OR thickness_mm > 0", name="ck_books_thickness_mm"
        ),
        sa.CheckConstraint("price IS NULL OR price >= 0", name="ck_books_price"),
        sa.CheckConstraint(
            f"ownership_status IN ({_sql_list(OWNERSHIP_STATUSES)})",
            name="ck_books_ownership_status",
        ),
        sa.CheckConstraint(
            f"reading_state IN ({_sql_list(READING_STATES)})",
            name="ck_books_reading_state",
        ),
        sa.CheckConstraint(
            "series_position IS NULL OR series_position >= 0", name="ck_books_series_position"
        ),
        sa.CheckConstraint(
            "series_position IS NULL OR series_id IS NOT NULL",
            name="ck_books_series_position_needs_series",
        ),
        sa.CheckConstraint("is_sprayed_edge IN (0, 1)", name="ck_books_is_sprayed_edge"),
        sa.CheckConstraint("is_signed IN (0, 1)", name="ck_books_is_signed"),
    )
    op.create_index("ix_books_owner_ownership", "books", ["owner_id", "ownership_status"])
    op.create_index("ix_books_owner_reading", "books", ["owner_id", "reading_state"])
    op.create_index("ix_books_primary_genre", "books", ["primary_genre_notation"])
    op.create_index("ix_books_series", "books", ["series_id", "series_position"])
    op.create_index("ix_books_isbn", "books", ["isbn"])
    op.create_index("ix_books_room", "books", ["room_id"])
    op.create_index("ix_books_same_edition", "books", ["same_edition_as_id"])

    op.create_table(
        "book_authors",
        sa.Column(
            "book_id",
            sa.Integer,
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "author_id",
            sa.Integer,
            sa.ForeignKey("authors.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 1 = first-listed author; the shelf's author sort uses the sort_key of
        # position 1 (D6).
        sa.Column("position", sa.Integer, nullable=False),
        sa.CheckConstraint("position > 0", name="ck_book_authors_position"),
        sa.UniqueConstraint("book_id", "position", name="uq_book_authors_book_position"),
    )
    op.create_index("ix_book_authors_author", "book_authors", ["author_id"])

    op.create_table(
        "book_genres",
        # Extra, filter-only genres (D5). The primary genre is the column on
        # books, never repeated here as a special row.
        sa.Column(
            "book_id",
            sa.Integer,
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "genre_notation",
            sa.Text,
            sa.ForeignKey("genres.notation"),
            primary_key=True,
        ),
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_book_genres_genre", "book_genres", ["genre_notation"])

    op.create_table(
        "loans",
        # Full lending history (SPEC §2/§3). D12: the due date is the whole
        # feature — no reminder flags, no notifications.
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "book_id",
            sa.Integer,
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("borrowed_to", sa.Text, nullable=False),
        sa.Column("loaned_at", sa.Text, nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("expected_return_date", sa.Text),
        sa.Column("returned_at", sa.Text),
        sa.Column("created_at", sa.Text, nullable=False, server_default=TIMESTAMP_DEFAULT),
    )
    op.create_index("ix_loans_book", "loans", ["book_id"])
    op.create_index("ix_loans_owner_open", "loans", ["owner_id", "returned_at"])

    op.create_table(
        "photos",
        # One row per book per slot (SPEC §2/§7). Generated filenames only,
        # never blobs. provider_filename is the only column a re-lookup writes
        # (T6); display order is upload_filename, then provider_filename.
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "book_id",
            sa.Integer,
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot", sa.Text, nullable=False),
        sa.Column("provider_filename", sa.Text, unique=True),
        sa.Column("upload_filename", sa.Text, unique=True),
        sa.Column("created_at", sa.Text, nullable=False, server_default=TIMESTAMP_DEFAULT),
        sa.CheckConstraint(f"slot IN ({_sql_list(PHOTO_SLOTS)})", name="ck_photos_slot"),
        sa.CheckConstraint(
            "provider_filename IS NOT NULL OR upload_filename IS NOT NULL",
            name="ck_photos_has_a_file",
        ),
        sa.UniqueConstraint("book_id", "slot", name="uq_photos_book_slot"),
    )

    op.create_table(
        "settings",
        # One global row (owner_id NULL, seeded below) plus optional per-user
        # override rows; resolution is user value, then global value, then the
        # app's hardcoded defaults (SPEC §10, A2).
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
        ),
        sa.Column("currency_symbol", sa.Text),
        sa.Column("date_format", sa.Text),
        sa.Column("time_format", sa.Text),
        sa.Column("theme", sa.Text),
        sa.Column("zoom_preset", sa.Text),
        sa.CheckConstraint(
            "currency_symbol IS NULL OR length(currency_symbol) BETWEEN 1 AND 4",
            name="ck_settings_currency_symbol",
        ),
        sa.CheckConstraint(
            "date_format IS NULL OR date_format IN ('dd.mm.yyyy', 'mm.dd.yyyy', 'yyyy-mm-dd')",
            name="ck_settings_date_format",
        ),
        sa.CheckConstraint(
            "time_format IS NULL OR time_format IN ('24h', '12h')",
            name="ck_settings_time_format",
        ),
        sa.CheckConstraint("theme IS NULL OR theme IN ('light', 'dark')", name="ck_settings_theme"),
        sa.CheckConstraint(
            "zoom_preset IS NULL OR zoom_preset IN ('S', 'M', 'L')", name="ck_settings_zoom_preset"
        ),
    )
    # One row per user AND at most one global row. A plain UNIQUE(owner_id)
    # would not do it: SQLite treats NULLs as distinct, so any number of
    # global rows would pass. ifnull(owner_id, -1) maps every global row to
    # -1 — an id that cannot exist — so they collide and user rows never do.
    op.execute("CREATE UNIQUE INDEX uq_settings_owner ON settings (ifnull(owner_id, -1))")

    settings_table = sa.table(
        "settings",
        sa.column("owner_id", sa.Integer),
        sa.column("currency_symbol", sa.Text),
        sa.column("date_format", sa.Text),
        sa.column("time_format", sa.Text),
        sa.column("theme", sa.Text),
        sa.column("zoom_preset", sa.Text),
    )
    op.bulk_insert(
        settings_table,
        [
            {
                "owner_id": None,
                "currency_symbol": "\u20ac",  # SPEC §10 default: EUR symbol
                "date_format": "dd.mm.yyyy",
                "time_format": "24h",
                "theme": "light",
                "zoom_preset": "M",
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_table("photos")
    op.drop_table("loans")
    op.drop_table("book_genres")
    op.drop_table("book_authors")
    op.drop_table("books")
    op.drop_table("series")
    op.drop_table("authors")
    op.drop_table("rooms")
    op.drop_table("genres")
    op.drop_table("users")
