/* Shared app chrome: dismissable banner and mobile nav toggle. */
(() => {
  const dismiss = document.querySelector("[data-banner-dismiss]");
  if (dismiss && dismiss.closest(".banner")) {
    dismiss.addEventListener("click", () => dismiss.closest(".banner").remove());
  }

  const toggle = document.querySelector("[data-nav-toggle]");
  const nav = document.querySelector(".site-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", () => {
      const open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = open ? "Close" : "Menu";
    });
  }
})();
