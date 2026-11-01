# The curated genre list (D4/D5): ~45 BISAC entries keyed by *notation*, because
# the vendored data/bisac.json is keyed by label and the code lives in each
# entry's "notation" field (T12). Extending the list is a code change here, plus
# a migration that mirrors the new rows into the `genres` table; a test fails if
# any notation below stops existing in data/bisac.json. Fiction is expanded to
# its second level because Fantasy / Science Fiction / Mystery / Suspense /
# Horror are not top-level BISAC categories.
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Genre:
    """One shelf genre.

    notation: BISAC code and the join key (books.primary_genre_notation).
    label:    full BISAC label, used to match provider subjects.
    section:  short shelf-section heading; GENRES list order is the
              section order on the shelf.
    """

    notation: str
    label: str
    section: str


# The deliberate deviation from BISAC (T12): no label in data/bisac.json
# contains "Young Adult", so YA is a local entry with a local notation.
LOCAL_YA_NOTATION = "LOCAL/YA"

# Order = shelf order: fiction first, YA right after it, then nonfiction A-Z.
GENRES: tuple[Genre, ...] = (
    # Fiction, expanded to the second BISAC level (T12).
    Genre("FIC000000", "Fiction / General", "Fiction"),
    Genre("FIC002000", "Fiction / Action & Adventure", "Action & Adventure"),
    Genre("FIC004000", "Fiction / Classics", "Classics"),
    Genre("FIC008000", "Fiction / Sagas", "Sagas"),
    Genre("FIC009000", "Fiction / Fantasy / General", "Fantasy"),
    Genre(
        "FIC010000",
        "Fiction / Fairy Tales, Folk Tales, Legends & Mythology",
        "Fairy Tales & Mythology",
    ),
    Genre("FIC014000", "Fiction / Historical", "Historical Fiction"),
    Genre("FIC015000", "Fiction / Horror", "Horror"),
    Genre("FIC019000", "Fiction / Literary", "Literary Fiction"),
    Genre("FIC022000", "Fiction / Mystery & Detective / General", "Mystery & Detective"),
    Genre("FIC024000", "Fiction / Occult & Supernatural", "Occult & Supernatural"),
    Genre("FIC027000", "Fiction / Romance / General", "Romance"),
    Genre("FIC028000", "Fiction / Science Fiction / General", "Science Fiction"),
    Genre("FIC030000", "Fiction / Suspense", "Suspense"),
    Genre("FIC031000", "Fiction / Thrillers", "Thrillers"),
    Genre("FIC032000", "Fiction / War & Military", "War & Military Fiction"),
    Genre(LOCAL_YA_NOTATION, "Young Adult (local entry, not BISAC)", "Young Adult"),
    # Nonfiction: curated top-level BISAC categories.
    Genre("ART000000", "Art / General", "Art"),
    Genre("BIO000000", "Biography & Autobiography / General", "Biography & Autobiography"),
    Genre("BUS000000", "Business & Economics / General", "Business & Economics"),
    Genre("CGN000000", "Comics & Graphic Novels / General", "Comics & Graphic Novels"),
    Genre("COM000000", "Computers / General", "Computers"),
    Genre("CKB000000", "Cooking / General", "Cooking"),
    Genre("GAM000000", "Games / General", "Games"),
    Genre("GAR000000", "Gardening / General", "Gardening"),
    Genre("HEA000000", "Health & Fitness / General", "Health & Fitness"),
    Genre("HIS000000", "History / General", "History"),
    Genre("JUV000000", "Juvenile Fiction / General", "Juvenile Fiction"),
    Genre("LCO000000", "Literary Collections / General", "Literary Collections"),
    Genre("MAT000000", "Mathematics / General", "Mathematics"),
    Genre("MUS000000", "Music / General", "Music"),
    Genre("NAT000000", "Nature / General", "Nature"),
    Genre("PHI000000", "Philosophy / General", "Philosophy"),
    Genre("PHO000000", "Photography / General", "Photography"),
    Genre("POE000000", "Poetry / General", "Poetry"),
    Genre("POL000000", "Political Science / General", "Political Science"),
    Genre("PSY000000", "Psychology / General", "Psychology"),
    Genre("REF000000", "Reference / General", "Reference"),
    Genre("REL000000", "Religion / General", "Religion"),
    Genre("SCI000000", "Science / General", "Science"),
    Genre("SEL000000", "Self-help / General", "Self-help"),
    Genre("SPO000000", "Sports & Recreation / General", "Sports & Recreation"),
    Genre("TEC000000", "Technology & Engineering / General", "Technology & Engineering"),
    Genre("TRV000000", "Travel / General", "Travel"),
    Genre("TRU000000", "True Crime / General", "True Crime"),
)

GENRE_BY_NOTATION: dict[str, Genre] = {genre.notation: genre for genre in GENRES}
