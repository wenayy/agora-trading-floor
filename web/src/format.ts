const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const price = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const fmtMoney = (v: number) => money.format(v);
export const fmtPrice = (v: number) => price.format(v);
export const fmtPct = (v: number | null | undefined, digits = 1) => {
  if (v == null) return "–";
  const r = Number((v * 100).toFixed(digits));
  return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r).toFixed(digits)}%`;
};
export const fmtWeight = (v: number) => `${(v * 100).toFixed(0)}%`;
export const tone = (v: number | null | undefined) => (v == null || v === 0 ? "" : v > 0 ? "up" : "down");

export const STRATEGY_LABEL: Record<string, string> = {
  value: "Value",
  momentum: "Momentum",
  macro: "Sector rotation",
  contrarian: "Contrarian",
};
