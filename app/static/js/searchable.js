/*
 * Searchable select: progressive enhancement for <select class="searchable">.
 * Keeps the native <select> in the DOM (forms still submit its value), and
 * adds a type-to-search input + filtered option list.
 */
(function () {
  "use strict";

  function enhance(sel) {
    var wrap = document.createElement("div");
    wrap.className = "searchable-select";

    var input = document.createElement("input");
    input.type = "search";
    input.className = "searchable-input";
    input.setAttribute("autocomplete", "off");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-haspopup", "listbox");
    var label = sel.getAttribute("aria-label") || sel.name || "choose";
    input.setAttribute("aria-label", label);
    input.placeholder = "Search " + label + "\u2026";

    var list = document.createElement("ul");
    list.className = "searchable-options";
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", label);

    function currentLabel() {
      var o = sel.options[sel.selectedIndex];
      return o ? o.textContent.trim() : "";
    }

    function buildList() {
      list.textContent = "";
      Array.prototype.forEach.call(sel.options, function (opt) {
        if (opt.disabled) return;
        var li = document.createElement("li");
        li.textContent = opt.textContent.trim();
        li.dataset.value = opt.value;
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", opt.selected ? "true" : "false");
        if (opt.selected) li.classList.add("selected");
        list.appendChild(li);
      });
    }

    function filter(q) {
      var ql = q.toLowerCase().trim();
      Array.prototype.forEach.call(list.children, function (li) {
        li.style.display = li.textContent.toLowerCase().indexOf(ql) >= 0 ? "" : "none";
      });
    }

    function open() {
      list.classList.add("open");
      input.setAttribute("aria-expanded", "true");
      filter(input.value);
    }

    function close() {
      list.classList.remove("open");
      input.setAttribute("aria-expanded", "false");
    }

    function selectLi(li) {
      Array.prototype.forEach.call(sel.options, function (opt) {
        opt.selected = opt.value === li.dataset.value;
      });
      input.value = li.textContent.trim();
      buildList();
      close();
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    }

    buildList();
    input.value = currentLabel();

    input.addEventListener("focus", function () {
      input.value = "";
      open();
    });
    input.addEventListener("input", function () {
      list.classList.add("open");
      filter(input.value);
    });

    list.addEventListener("mousedown", function (ev) {
      ev.preventDefault();
      var li = ev.target.closest("li");
      if (li) selectLi(li);
    });

    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { close(); input.blur(); }
    });

    input.addEventListener("blur", function () {
      var q = input.value.trim().toLowerCase();
      var found = null;
      Array.prototype.forEach.call(sel.options, function (opt) {
        if (!found && opt.textContent.trim().toLowerCase() === q) found = opt;
      });
      if (found) {
        Array.prototype.forEach.call(sel.options, function (opt) {
          opt.selected = opt === found;
        });
      }
      input.value = currentLabel();
      close();
    });

    document.addEventListener("click", function (ev) {
      if (ev.target !== wrap && !wrap.contains(ev.target)) close();
    });

    wrap.appendChild(input);
    wrap.appendChild(list);

    sel.classList.add("searchable-native");
    sel.parentNode.insertBefore(wrap, sel);
  }

  function init() {
    Array.prototype.forEach.call(
      document.querySelectorAll("select.searchable"),
      enhance
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
