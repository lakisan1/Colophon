/* Settings page: switching every search provider off is accepted by the server,
   so the page asks once before submitting a form with no provider ticked —
   unticking the last box is more likely a slip than a decision, and the
   consequence (a New book lookup that answers nothing) is invisible here.
   Lives in a file rather than an inline script so the CSP can hold at
   script-src 'self' with no unsafe-inline. */
(() => {
  const form = document.querySelector('form.card[method="post"][action="/settings"]');
  if (!form) {
    return;
  }
  form.addEventListener("submit", (event) => {
    if (form.querySelector('input[name^="provider_"]:checked')) {
      return;
    }
    const sure = window.confirm(
      "Are you sure you want to disable all search providers? The New book lookup won't work."
    );
    if (!sure) {
      event.preventDefault();
    }
  });
})();
