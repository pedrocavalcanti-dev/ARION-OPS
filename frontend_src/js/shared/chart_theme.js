export function token(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

export function palette() {
  return {
    text: token("--text-secondary", "#69716d"),
    muted: token("--text-muted", "#8b928e"),
    faint: token("--text-faint", "#a9aea9"),
    border: token("--border-primary", "rgba(40,50,45,.12)"),
    surface: token("--surface-primary", "#fff"),
    teal: token("--accent-teal", "#2f8579"),
    green: token("--accent-green", "#3d845a"),
    orange: token("--accent-orange", "#b26f30"),
    rose: token("--accent-rose", "#b04f59"),
    violet: token("--accent-violet", "#6b56a3"),
    blue: token("--accent-blue", "#4c6f9e"),
    danger: token("--danger", "#a94f48"),
  };
}

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

export function common(p) {
  return {
    animationDuration: 320,
    textStyle: { fontFamily: 'Inter, "Segoe UI", sans-serif', color: p.text },
    aria: { enabled: true, decal: { show: true } },
    tooltip: {
      trigger: "axis",
      backgroundColor: p.surface,
      borderColor: p.border,
      borderWidth: 1,
      textStyle: { color: p.text, fontSize: 10 },
      extraCssText: "box-shadow:0 8px 24px rgba(0,0,0,.12);border-radius:7px;",
    },
  };
}
