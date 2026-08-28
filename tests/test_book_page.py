# GET /book/{book_id} (the book page). These exist because the page is the one
# place a reader sees the whole record, and three of its promises are easy to
# break silently: (a) scoping — a foreign id must answer exactly like a missing
# one, or the status line itself leaks that someone else owns that book;
# (b) money — SPEC §10 formatting (symbol first, thousands separated) is only
# visible in the rendered digits, so the test writes a price it chooses rather
# than trusting the fixture's; (c) read-only — one stray POST form turns a
# look-at page into a mutation surface again.
from __future__ import annotations

import html
import re

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text


def text_of(response) -> str:
    """Titles and inscriptions carry apostrophes; Jinja escapes them, so raw
    comparisons against response.text fail on "Kids' room" for no real reason."""
    return html.unescape(response.text)


def owned_book(engine: Engine) -> tuple[int, str, str]:
    """(id, title, authors) of one owned book with an author — the spine links
    only reach books on a shelf, and the library shelf is 'owned'."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT b.id, b.title,
                       (SELECT group_concat(a.name, ', ')
                          FROM book_authors ba JOIN authors a ON a.id = ba.author_id
                         WHERE ba.book_id = b.id) AS authors
                FROM books b
                WHERE b.ownership_status = 'owned' AND b.title NOT LIKE '%:%'
                ORDER BY b.id
                LIMIT 1
                """
            )
        ).first()
    assert row is not None and row[2]
    return int(row[0]), row[1], row[2]


def test_the_page_renders_for_a_seeded_book(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    book_id, _title, _authors = owned_book(engine)

    response = client.get(f"/book/{book_id}")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_the_record_shows_the_title_and_the_authors(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    client, engine = seeded_db
    book_id, title, authors = owned_book(engine)

    body = text_of(client.get(f"/book/{book_id}"))

    # The h1 is the title; the authors line is the one thing a borrower or a
    # buyer reads first, so both must be present verbatim.
    assert title in body
    assert authors in body


def test_the_price_carries_the_settings_symbol_and_a_thousands_separator(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    client, engine = seeded_db
    book_id, _title, _authors = owned_book(engine)
    # Chosen, not inherited: the fixture's prices are random-ish and would pass
    # for the wrong reason if the separator logic disappeared.
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE books SET price = :p WHERE id = :i"), {"p": 1234567.5, "i": book_id}
        )
        conn.commit()

    body = text_of(client.get(f"/book/{book_id}"))

    # SPEC §10: symbol before the number; grouped thousands for legibility.
    assert "€1,234,567.50" in body


def test_a_book_out_on_loan_names_the_holder_from_the_open_loan(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    client, engine = seeded_db
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT b.id, l.borrowed_to, l.loaned_at
                FROM books b
                JOIN loans l ON l.book_id = b.id AND l.returned_at IS NULL
                WHERE b.ownership_status = 'borrowed'
                LIMIT 1
                """
            )
        ).first()
    assert row is not None

    body = text_of(client.get(f"/book/{row[0]}"))

    # SPEC §3 shelf 2: to whom, since when — read from the loans row, not
    # inferred from the status flag alone.
    assert row[1] in body
    assert row[2] in body


def test_another_owners_book_answers_404(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    # Bypass signs in as 'demo', so a second user's book must read as "no such
    # book", never as "not yours": a 403 or a custom page would confirm the id.
    with engine.connect() as conn:
        conn.execute(
            text("INSERT INTO users (username, password_hash) VALUES ('someone-else', 'x')")
        )
        other_id = int(conn.execute(text("SELECT last_insert_rowid()")).scalar_one())
        conn.execute(
            text("INSERT INTO books (owner_id, title) VALUES (:o, 'A Diary Not Yours')"),
            {"o": other_id},
        )
        conn.commit()
        foreign_id = int(conn.execute(text("SELECT last_insert_rowid()")).scalar_one())

    response = client.get(f"/book/{foreign_id}")

    assert response.status_code == 404
    assert "A Diary Not Yours" not in text_of(response)


def test_an_unknown_id_answers_404(seeded_db: tuple[TestClient, Engine]) -> None:
    client, _engine = seeded_db

    assert client.get("/book/999999").status_code == 404


def test_the_shelf_links_its_spines_to_the_book_pages(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    client, engine = seeded_db
    book_id, _title, _authors = owned_book(engine)

    body = client.get("/").text

    # No JavaScript may be needed to open a book: the spine itself carries a
    # real anchor to the page, for mouse, keyboard and script-less browsers.
    assert f'class="spine-link" href="/book/{book_id}"' in body
    # Every rendered spine links somewhere.
    assert body.count('class="spine-link"') == body.count("data-book-id=")


def test_the_page_is_read_only(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    book_id, _title, _authors = owned_book(engine)

    body = client.get(f"/book/{book_id}").text

    # The sign-out form belongs to base.html's header chrome. Inside <main> —
    # the book page itself — there must be no form at all, POST or otherwise.
    main = re.search(r"<main\b[^>]*>(.*)</main>", body, re.DOTALL)
    assert main is not None
    assert "<form" not in main.group(1)
    # The two actions the page is allowed to offer, both plain links.
    assert f'href="/book/{book_id}/edit"' in body
    assert 'href="/"' in body


def test_a_photo_row_without_a_file_never_renders_a_broken_image(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """The M2 fixture writes `photos` rows that only name a provider file; the bytes
    are downloaded by the lookup, and a download can fail after the row exists. An
    `<img>` pointing at such a row is a broken icon in the middle of the record, so
    the page asks the same question the serving route asks: are there bytes?"""
    client, engine = seeded_db
    with engine.connect() as conn:
        book_with_row = int(
            conn.execute(
                text("SELECT book_id FROM photos WHERE slot = 'front' ORDER BY book_id LIMIT 1")
            ).scalar_one()
        )

    body = text_of(client.get(f"/book/{book_with_row}"))

    assert f'src="/book/{book_with_row}/photos/front"' not in body
    assert "without a stored file" in body


def test_a_real_upload_is_rendered_and_serves_bytes(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """The other half: once the bytes exist, the page must show them."""
    client, engine = seeded_db
    from app.settings import settings
    from app.uploads import save_upload

    with engine.connect() as conn:
        book_id = int(conn.execute(text("SELECT min(id) FROM books")).scalar_one())
        owner_id = int(
            conn.execute(
                text("SELECT owner_id FROM books WHERE id = :i"), {"i": book_id}
            ).scalar_one()
        )

    saved = save_upload(
        _one_pixel_png(), upload_dir=settings.upload_dir, book_id=book_id, slot="front"
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO photos (owner_id, book_id, slot, upload_filename)
                VALUES (:o, :b, 'front', :f)
                ON CONFLICT (book_id, slot) DO UPDATE SET upload_filename = excluded.upload_filename
                """
            ),
            {"o": owner_id, "b": book_id, "f": saved.filename},
        )

    body = text_of(client.get(f"/book/{book_id}"))
    assert f'src="/book/{book_id}/photos/front"' in body

    served = client.get(f"/book/{book_id}/photos/front")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/")


def test_no_image_on_a_shelf_or_record_page_is_broken(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """The invariant behind both tests above, checked by fetching what the page
    actually points at. A page full of 404 images is how a photo feature looks
    finished while being empty."""
    import re

    client, engine = seeded_db
    with engine.connect() as conn:
        book_ids = [
            row[0] for row in conn.execute(text("SELECT id FROM books ORDER BY id LIMIT 12"))
        ]

    for path in ["/", "/wishlist", *[f"/book/{book_id}" for book_id in book_ids]]:
        body = client.get(path).text
        for src in re.findall(r'<img[^>]+src="([^"]+)"', body):
            assert client.get(src).status_code == 200, f"{path} points at {src}, which 404s"


def _one_pixel_png() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (40, 60), (120, 40, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()
