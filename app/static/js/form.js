// Progressive enhancement over the server-side oversize check. It asks the
// same question a submit earlier, so nobody waits for a round trip to be
// asked about a 700 mm paperback. The rule itself lives in Python
// (MAX_PLAUSIBLE_MM in app/routers/books.py) and travels to this file as the
// form's data-max-plausible-mm attribute — the number is not defined twice.
//
// Nothing here is load-bearing, and that is the point. With JavaScript the
// question is asked before the round trip and answering it saves in one press.
// With JavaScript completely disabled the identical flow runs server-side: a
// post with an implausible dimension saves nothing and comes back with the
// warning and a hidden confirm_oversize=1, so pressing the same Save button
// again saves.
(function () {
  "use strict";

  var DIMENSIONS = [
    ["width_mm", "Width"],
    ["height_mm", "Height"],
    ["thickness_mm", "Thickness"]
  ];

  document.addEventListener("submit", function (event) {
    var form = event.target;
    // Only the book form opts in; the lookup and photo forms have no such
    // attribute and submit untouched.
    if (!form.dataset || !form.dataset.maxPlausibleMm) return;
    // The server already asked and this form is the answer to that question:
    // the hidden field is the confirmation, so asking again would be a loop.
    if (form.querySelector('input[name="confirm_oversize"]')) return;

    var limit = parseInt(form.dataset.maxPlausibleMm, 10);
    var parts = [];
    DIMENSIONS.forEach(function (dimension) {
      var field = form.querySelector('input[name="' + dimension[0] + '"]');
      var value = field ? parseInt(field.value, 10) : NaN;
      if (!isNaN(value) && value > limit) parts.push(dimension[1] + " " + value + " mm");
    });
    if (!parts.length) return;

    var verb = parts.length === 1 ? "is" : "are";
    var question =
      parts.join(" and ") + " " + verb + " larger than a book usually is. Save it anyway?";
    if (window.confirm(question)) {
      // Answering here is the confirmation, so say so in the post: without this
      // field the server would ask the same question a second time and the
      // person who just pressed "Save it anyway?" would be told to press Save.
      var answer = document.createElement("input");
      answer.type = "hidden";
      answer.name = "confirm_oversize";
      answer.value = "1";
      form.appendChild(answer);
      return;
    }
    event.preventDefault();
  });
})();


// Open Library ISBN lookup: typing an ISBN prefills the book fields.
// Best-effort enhancement — if Open Library is unreachable, nothing is filled
// and the person still types by hand. Never overwrites what is already there.
(function () {
  "use strict";

  var ISBN_FIELDS = [
    "title", "authors", "publisher", "publication_year", "page_count", "language"
  ];

  document.addEventListener("change", function (event) {
    var field = event.target;
    // Only the book form's ISBN input opts in; the lookup/photo forms do not.
    if (!field || field.name !== "isbn") return;
    var isbn = field.value.trim();
    if (!isbn) return;

    fetch("/book/lookup?isbn=" + encodeURIComponent(isbn))
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data.found) return;
        ISBN_FIELDS.forEach(function (name) {
          var target = document.querySelector('input[name="' + name + '"]');
          if (!target || target.value.trim()) return;
          target.value = name === "authors"
            ? (Array.isArray(data.authors) ? data.authors.join(", ") : data.authors)
            : (data[name] || "");
        });
      })
      .catch(function () { /* lookup is best-effort */ });
  });
})();
