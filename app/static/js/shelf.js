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

  function isLink(event) {
    // The spine's title text is a plain <a> to the book page. A click or an
    // Enter keypress on it belongs to the browser's navigation, not to the
    // flip — toggling here would fight the link and hide the cover mid-jump.
    return Boolean(event.target.closest && event.target.closest("a"));
  }

  document.addEventListener("click", function (event) {
    if (isLink(event)) return;
    var book = event.target.closest(".book");
    if (book) toggle(book);
  });

  // Spines are focusable (tabindex in shelf.html), so the keyboard gets the
  // same flip and Escape gives it back.
  document.addEventListener("keydown", function (event) {
    if (isLink(event)) return;
    var book = event.target.closest ? event.target.closest(".book") : null;
    if (!book) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle(book);
    } else if (event.key === "Escape") {
      book.classList.remove("is-open");
    }
  });

  // Show a fade + chevron on whichever side of a shelf still has hidden books.
  // At the scroll ends the chevron disappears and the solid wooden cap returns,
  // so you can see that you have reached the start/end of the shelf.
  function updateShelfIndicators() {
    var scrolls = document.querySelectorAll(".shelf-scroll");
    for (var i = 0; i < scrolls.length; i++) {
      var row = scrolls[i].querySelector(".shelf-row");
      if (!row) continue;
      var maxScroll = row.scrollWidth - row.clientWidth;
      // 1px tolerance for sub-pixel edge rounding.
      scrolls[i].classList.toggle("can-scroll-left", row.scrollLeft > 1);
      scrolls[i].classList.toggle("can-scroll-right", row.scrollLeft < maxScroll - 1);
    }
  }
  function initShelfIndicators() {
    updateShelfIndicators();
    var scrolls = document.querySelectorAll(".shelf-scroll");
    for (var i = 0; i < scrolls.length; i++) {
      var row = scrolls[i].querySelector(".shelf-row");
      if (row && !row.dataset.indicatorBound) {
        row.dataset.indicatorBound = "1";
        row.addEventListener("scroll", updateShelfIndicators);
      }
    }
  }
  initShelfIndicators();
  window.addEventListener("load", initShelfIndicators);
  window.addEventListener("resize", initShelfIndicators);
})();
