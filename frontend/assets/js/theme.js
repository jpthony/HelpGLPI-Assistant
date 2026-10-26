// Thème : automatique (système) → clair → sombre. Chargé dans <head> pour éviter le flash.
(() => {
  const root = document.documentElement;
  const labels = { auto: "Thème : automatique", light: "Thème : clair", dark: "Thème : sombre" };
  const order = Object.keys(labels);
  let theme = "auto";
  try { theme = localStorage.getItem("theme") || "auto"; } catch {}
  if (!labels[theme]) theme = "auto";

  const apply = () => { if (theme === "auto") root.removeAttribute("data-theme"); else root.dataset.theme = theme; };
  apply();

  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("themeBtn");
    if (!btn) return;
    const sync = () => {
      btn.title = labels[theme];
      btn.setAttribute("aria-label", labels[theme]);
      btn.querySelector("use").setAttribute("href", "#i-theme-" + theme);
    };
    sync();
    btn.addEventListener("click", () => {
      theme = order[(order.indexOf(theme) + 1) % order.length];
      try { theme === "auto" ? localStorage.removeItem("theme") : localStorage.setItem("theme", theme); } catch {}
      apply();
      sync();
    });
  });
})();
