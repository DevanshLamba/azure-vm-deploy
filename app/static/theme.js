// Runs before first paint so the saved theme never flashes. External file because the CSP blocks inline scripts.
(function () {
  var theme = "light";
  try { theme = localStorage.getItem("ct-theme") === "dark" ? "dark" : "light"; } catch (e) {}
  document.documentElement.dataset.theme = theme;
})();
