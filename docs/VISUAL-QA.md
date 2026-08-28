# Visual QA — M3 shelf

For Lazar. This is the part that cannot be verified headless: 131 tests prove the geometry
arithmetic and the markup, none of them prove the shelf *looks* like books.

## How to look at it

```
make venv && make dev      # http://127.0.0.1:8000
```

`make seed` first if the library looks empty. The dev bypass signs you in as the seeded `demo`
account, so 181 owned spines should appear in 37 labelled rows.

## Check these, in order

1. **Does it read as a bookshelf at all?** If the honest answer is no, say so — the whole scale
   model (A1–A8) is marked `[ASSUMPTION]` in `docs/DECISIONS.md` for exactly this review.
2. **Proportions at zoom L.** Pick the tallest book (an oversized folio) and the shortest (a board
   book). The folio should be roughly three times the board book's height. L is the preset where
   proportions are exactly true; S and M deliberately trade proportionality for clickability (A3).
3. **Thin spines.** Find the manga and pocket editions. At S they should be a colour sliver with a
   tooltip on hover — not unreadable micro-text, not a fat fake-wide spine.
4. **Cloth colours.** Same author should sit in the same colour family across the whole shelf.
   Reload the page: colours must not shuffle (they are hashed, not random). If several unrelated
   authors share a colour, that is acceptable; if one author changes colour, that is a bug.
5. **The flip.** Click any spine: it should turn to a paper cover card. Click another and the first
   should close. Tab to a spine and press Enter; press Escape to close. Set the OS to
   "reduce motion" and the flip should snap instead of animating (A6).
6. **Series badges.** In Action & Adventure, *Horus Rising → The First Heretic → False Gods → Fear
   to Trust → Galaxy in Flame* must read in that order, with the badge showing `1.5` and `2.5` —
   this is decision D9 and the single easiest thing to get silently wrong.
7. **Wood and rows.** Each genre is its own row with a pinned label while the row scrolls sideways
   (A8). Labels should read "Fantasy", not "Fiction / Fantasy / General".
8. **Dark theme.** Edit `data-theme="light"` to `"dark"` in devtools (the switcher itself is M5).
   Spine text must stay legible on cloth in both.
9. **Mobile.** Narrow the window to ~400 px. Rows should scroll sideways, not wrap into a grid.
10. **Diacritics.** Titles with č ć ž š đ must not show tofu boxes. No OFL webfont is vendored yet
    (A7); the CSS uses a serif stack chosen for Latin Extended-A coverage. If your browser shows
    boxes, tell me and I will vendor a subset.

## Known dead spots — not bugs in M3

- **No cover art.** The flipped face is a paper card. Real covers arrive with M4 (lookup/upload) and
  M7 (cached provider images). Nothing renders a broken `<img>`; a test enforces that.
- **No room filter, no search, no stats.** M6. Rooms are filters, not separate pages (D7).
- **No wishlist / borrowed / sold shelves.** M6. Right now non-owned books are simply absent — 29 of
  the 210 fixture books do not appear, which is correct (D2).
- **No detail panel.** Clicking flips the spine; it does not open a book page yet.
- **Zoom persists on a GET.** Cosmetic and scoped to your own view; M5 replaces it with a
  CSRF-protected settings form.

## What to reply with

Anything in 1–10 that looks wrong, in plain words ("spines too thin at S", "wood is too orange").
Geometry complaints are cheap to act on now and expensive after M4 paints over them.
