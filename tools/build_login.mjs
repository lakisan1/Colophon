#!/usr/bin/env node
/**
 * Builds the login page's static assets (CSS + JS).
 *
 * The source of truth for the login page styling lives here as strings so the
 * whole frontend is generated with Node.js — no extra package.json needed.
 * Run with:  node tools/build_login.mjs
 *
 * It writes:
 *   app/static/css/login.css
 *   app/static/js/login.js
 */
import { writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

// ---------------------------------------------------------------------------
// login.css — the card, fields, button and error, using the app's theme tokens
// ---------------------------------------------------------------------------
const loginCss = `
/* Login page. Uses the app theme tokens from theme.css, so it tracks
   light/dark via the <html data-theme="..."> attribute. */

.login-page {
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: 1.5rem;
  /* A soft wash so the card floats, instead of a flat grey void. */
  background:
    radial-gradient(120% 90% at 20% 10%, var(--paper-raised) 0%, var(--paper) 55%);
}

.login-shell { width: 100%; max-width: 26rem; }

.login-card {
  background: var(--paper-raised);
  border: 1px solid var(--wood-shelf);
  border-radius: 16px;
  box-shadow: 0 24px 48px rgba(0, 0, 0, 0.16), 0 6px 16px rgba(0, 0, 0, 0.10);
  padding: clamp(1.5rem, 4vw, 2.25rem);
  animation: login-rise 480ms var(--motion-ease, ease-out) both;
}

.login-title {
  margin: 0 0 0.25rem;
  font-size: 1.5rem;
  letter-spacing: 0.02em;
}

.login-sub { margin: 0 0 1.25rem; font-size: 0.9rem; color: var(--ink-muted); }

.login-error {
  margin: 0 0 1rem;
  padding: 0.65rem 0.9rem;
  border: 1px solid var(--focus-ring, #b3541e);
  border-radius: 10px;
  background: color-mix(in srgb, var(--focus-ring, #b3541e) 10%, var(--paper-raised));
  color: var(--ink);
  font-size: 0.85rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.login-error button {
  border: 0;
  background: transparent;
  color: var(--ink-muted);
  font-size: 1.1rem;
  line-height: 1;
  cursor: pointer;
}

.login-field {
  margin: 0 0 0.85rem;
  display: block;
}

.login-label {
  display: block;
  margin: 0 0 0.3rem;
  font-size: 0.82rem;
  color: var(--ink-muted);
}

.login-input-wrap { position: relative; }

.login-input {
  width: 100%;
  box-sizing: border-box;
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--wood-shelf);
  border-radius: 10px;
  background: var(--paper);
  color: var(--ink);
  font: inherit;
}

.login-input:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

.login-toggle {
  position: absolute;
  top: 50%;
  right: 0.5rem;
  transform: translateY(-50%);
  border: 0;
  background: transparent;
  color: var(--ink-muted);
  font-size: 0.9rem;
  cursor: pointer;
  padding: 0.25rem 0.35rem;
}

.login-toggle:hover { color: var(--ink); }

.login-submit {
  margin-top: 0.6rem;
  width: 100%;
  padding: 0.7rem 0.9rem;
  border: 0;
  border-radius: 10px;
  background: var(--focus-ring, #b3541e);
  color: #fff;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
  transition: filter 160ms ease, transform 160ms ease;
}

.login-submit:hover { filter: brightness(1.08); }
.login-submit:active { transform: translateY(1px); }
.login-submit:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

.login-hint {
  margin: 1rem 0 0;
  font-size: 0.78rem;
  line-height: 1.5;
  color: var(--ink-muted);
  text-align: center;
}

.login-hint code {
  font: inherit;
  padding: 0.1rem 0.35rem;
  border-radius: 6px;
  background: var(--paper);
  border: 1px solid var(--wood-shelf);
}

@media (prefers-reduced-motion: reduce) {
  .login-card { animation: none; }
}

@keyframes login-rise {
  from { opacity: 0; transform: translateY(10px); }
  to   { opacity: 1; transform: translateY(0); }
}
`;

// ---------------------------------------------------------------------------
// login.js — password toggle, autofocus, dismissable error
// ---------------------------------------------------------------------------
const loginJs = `
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
})();
`;

const targets = {
  "app/static/css/login.css": loginCss.trim() + "\n",
  "app/static/js/login.js": loginJs.trim() + "\n",
};

for (const [rel, content] of Object.entries(targets)) {
  const out = join(root, rel);
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, content);
  console.log(`wrote ${rel}`);
}
