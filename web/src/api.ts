// Typed client for the Agora API. In demo mode (static build) it reads a recorded snapshot instead.

export const DEMO = import.meta.env.MODE === "demo";

export interface SessionSummary {
  day: number;
  trading_day?: number;
  summary: string;
  turns: number;
  trades: number;
  rejected: number;
  trace_id: string;
  llm_calls: number;
  tokens: number;
  cost_usd: number;
  ms: number;
}

export interface TraderRow {
  id: string;
  name: string;
  strategy: string;
  style: string;
  equity: number;
  cash: number;
  return_pct: number;
  positions: number;
  last_session: SessionSummary | null;
}

export interface FloorView {
  day: number;
  running: boolean;
  autopilot: { enabled: boolean; interval_s: number };
  brain: "llm" | "policy";
  model: string;
  transport: string;
  traders: TraderRow[];
  benchmark_return: number;
  limits: { max_position_pct: number; max_order_pct: number; max_trades_per_cycle: number };
}

export interface Point {
  day: number;
  equity: number;
}

export interface EquityView {
  traders: Record<string, Point[]>;
  benchmark: Point[];
}

export interface Position {
  symbol: string;
  qty: number;
  avg_cost: number;
  price: number;
  market_value: number;
  weight: number;
  unrealized_pnl: number;
}

export interface Transaction {
  id: number;
  trader_id: string;
  day: number;
  trading_day: number;
  side: "BUY" | "SELL";
  symbol: string;
  qty: number;
  price: number;
  fee: number;
  rationale: string;
}

export interface TraderDetail {
  trader_id: string;
  name: string;
  strategy: string;
  style: string;
  cash: number;
  equity: number;
  return_pct: number;
  positions: Position[];
  transactions: Transaction[];
  notes: { key: string; value: string; day: number }[];
  sessions: SessionSummary[];
}

export interface Security {
  symbol: string;
  name: string;
  sector: string;
  price: number;
  ret_1d: number | null;
  ret_5d: number | null;
  ret_20d: number | null;
  vol_20d: number | null;
  pe: number;
  dividend_yield: number;
  latest_news: { headline: string; sentiment: number; days_ago: number } | null;
}

export interface Metric {
  name: string;
  strategy: string;
  total_return: number;
  ann_vol: number;
  sharpe: number;
  max_drawdown: number;
  trades: number;
  turnover: number;
  fees: number;
}

export interface FloorEvent {
  id: number;
  trading_day: number;
  trader_id: string | null;
  kind: string;
  data: Record<string, unknown>;
  ts: number;
}

async function get<T>(path: string, demoFile?: string): Promise<T> {
  const url = DEMO ? `./demo/${demoFile ?? path.replace(/^\/api\//, "").replace(/\//g, "_")}.json` : path;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} for ${path}`);
  return r.json() as Promise<T>;
}

export const api = {
  floor: () => get<FloorView>("/api/floor"),
  equity: () => get<EquityView>("/api/equity"),
  trader: (id: string) => get<TraderDetail>(`/api/traders/${id}`),
  market: () => get<{ day: number; securities: Security[] }>("/api/market"),
  metrics: () => get<Record<string, Metric>>("/api/metrics"),
  events: (traderId: string) =>
    get<FloorEvent[]>(`/api/events?latest=true&limit=400&trader_id=${traderId}`, `events_${traderId}`),
  runDay: async () => {
    const r = await fetch("/api/cycle", { method: "POST" });
    if (!r.ok && r.status !== 409) throw new Error(`Could not start the trading day (${r.status})`);
  },
  autopilot: async (enabled: boolean, interval_s = 15) => {
    await fetch("/api/autopilot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled, interval_s }),
    });
  },
};
