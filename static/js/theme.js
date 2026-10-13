// Light/Dark switch. The page's starting theme is already set by the tiny
// script in <head>; this file only handles clicks on the toggle buttons.
(function () {
  var root = document.documentElement;

  function markButtons() {
    var current = root.getAttribute("data-theme");
    document.querySelectorAll("[data-theme-option]").forEach(function (btn) {
      btn.setAttribute("aria-pressed",
        btn.getAttribute("data-theme-option") === current ? "true" : "false");
    });
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-theme-option]");
    if (!btn) return;
    var theme = btn.getAttribute("data-theme-option");
    root.setAttribute("data-theme", theme);
    try { localStorage.setItem("theme", theme); } catch (e) {}
    markButtons();
  });

  markButtons();
})();
