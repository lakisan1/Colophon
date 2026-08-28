// Shelf interaction (A6): selecting a book turns it to show the front cover.
// No framework, no build step — the page is fully usable without this file
// (zoom is plain links); it only adds the flip.
(function () {
  "use strict";

  /** Only one book lies open at a time: a wall of flipped covers reads as noise. */
  function closeOthers(openList, current) {
    for (const book of openList) {
      if (book !== current) book.classList.remove("is-open");
    }
  }

  function toggle(book) {
    var open = document.querySelectorAll(".book.is-open");
    var wasOpen = book.classList.contains("is-open");
    closeOthers(open, book);
    book.classList.toggle("is-open", !wasOpen);
  }

  document.addEventListener("click", function (event) {
    var book = event.target.closest(".book");
    if (book) toggle(book);
  });

  // Spines are focusable (tabindex in shelf.html), so the keyboard gets the
  // same flip and Escape gives it back.
  document.addEventListener("keydown", function (event) {
    var book = event.target.closest ? event.target.closest(".book") : null;
    if (!book) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle(book);
    } else if (event.key === "Escape") {
      book.classList.remove("is-open");
    }
  });
})();
