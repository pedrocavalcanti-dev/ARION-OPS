export const CAUSE_ORDER = [
  "Rompimento",
  "Energia",
  "Massiva",
  "Equipamento",
  "Configuração",
  "Reset remoto",
  "Pendência Financeira",
  "Demais motivos técnicos",
  "Sem classificação",
];

export const CAUSE_META = {
  "Rompimento": { key: "rompimento", color: "#d96b43", darkColor: "#e67f58", badge: "RMP" },
  "Energia": { key: "energia", color: "#c9971a", darkColor: "#eab640", badge: "ENG" },
  "Massiva": { key: "massiva", color: "#7654d4", darkColor: "#9c80ec", badge: "MSV" },
  "Equipamento": { key: "equipamento", color: "#267fb2", darkColor: "#54a8d8", badge: "EQP" },
  "Configuração": { key: "configuracao", color: "#229182", darkColor: "#46bca9", badge: "CFG" },
  "Reset remoto": { key: "reset_remoto", color: "#5271d6", darkColor: "#7e9bf0", badge: "RST" },
  "Pendência Financeira": { key: "financeiro", color: "#b84b73", darkColor: "#df7098", badge: "FIN" },
  "Demais motivos técnicos": { key: "demais", color: "#6c788a", darkColor: "#9aa5b5", badge: "TEC" },
  "Sem classificação": { key: "sem_classificacao", color: "#787f85", darkColor: "#7a8288", badge: "S/C" },
};

export function causeColor(label, mode = "dark") {
  const item = CAUSE_META[label];
  if (!item) return mode === "light" ? "#787f85" : "#7a8288";
  return mode === "light" ? item.color : item.darkColor;
}

export function causeBadge(label) {
  return CAUSE_META[label]?.badge || "MOT";
}

export function causeFilterKey(label) {
  return CAUSE_META[label]?.key || "";
}
