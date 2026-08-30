/* Login page: autofocus the username, toggle password visibility,
   and let the error banner be dismissed. */
(() => {
  const username = document.getElementById("login-user");
  const password = document.getElementById("login-pass");
  const toggle = document.querySelector("[data-login-toggle]");
  const dismiss = document.querySelector("[data-login-dismiss]");

  if (username && !document.activeElement?.matches("input")) username.focus();

  if (toggle && password) {
    let show = false;
    toggle.addEventListener("click", () => {
      show = !show;
      password.type = show ? "text" : "password";
      toggle.textContent = show ? "Hide" : "Show";
      toggle.setAttribute("aria-pressed", String(show));
    });
  }

  if (dismiss && dismiss.closest(".login-error")) {
    dismiss.addEventListener("click", () => dismiss.closest(".login-error").remove());
  }

  /* Dark/light toggle. The page defaults to dark (the server renders
     data-theme="dark" and the inline <head> script applies any saved choice);
     clicking flips it and remembers it for next time. */
  const themeBtn = document.querySelector("[data-login-theme]");
  function loginTheme() {
    return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  }
  function renderThemeToggle(theme) {
    if (!themeBtn) return;
    const light = theme === "light";
    themeBtn.textContent = light ? "🌙" : "☀️"; // the theme you would switch to
    themeBtn.setAttribute("aria-label", light ? "Switch to dark theme" : "Switch to light theme");
    themeBtn.setAttribute("aria-pressed", String(light));
  }
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const next = loginTheme() === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      renderThemeToggle(next);
      try { localStorage.setItem("colophon-theme", next); } catch (err) {}
    });
    renderThemeToggle(loginTheme());
  }
})();
