import type { FloorEvent, Transaction } from "../api";
import { fmtMoney, fmtPrice } from "../format";

interface Rejected {
  id: number;
  trading_day: number;
  side: "BUY" | "SELL";
  symbol: string;
  qty: number;
  reason: string;
}

export function rejectedOrders(events: FloorEvent[]): Rejected[] {
  return events
    .filter((e) => e.kind === "tool_error" && ["buy_shares", "sell_shares"].includes(String(e.data.tool)))
    .map((e) => {
      const args = (e.data.args ?? {}) as { symbol?: string; quantity?: number };
      return {
        id: e.id,
        trading_day: e.trading_day,
        side: e.data.tool === "buy_shares" ? "BUY" : "SELL",
        symbol: String(args.symbol ?? "").toUpperCase(),
        qty: Number(args.quantity ?? 0),
        reason: String(e.data.result ?? "").replace(/^Error executing tool \w+: /, "").replace(/^Order rejected: /, ""),
      };
    });
}

export function Tickets({ transactions, rejected }: { transactions: Transaction[]; rejected: Rejected[] }) {
  type Item = { kind: "fill"; t: Transaction; key: string; day: number } | { kind: "void"; r: Rejected; key: string; day: number };
  const items: Item[] = [
    ...transactions.map((t) => ({ kind: "fill" as const, t, key: `t${t.id}`, day: t.trading_day })),
    ...rejected.map((r) => ({ kind: "void" as const, r, key: `r${r.id}`, day: r.trading_day })),
  ].sort((a, b) => b.day - a.day);

  if (!items.length) {
    return <p className="empty">No orders yet. Run a trading day to see what this trader decides and why.</p>;
  }
  return (
    <ol className="tickets">
      {items.slice(0, 40).map((it) =>
        it.kind === "fill" ? (
          <li key={it.key} className="ticket">
            <div className={`stamp ${it.t.side === "BUY" ? "buy" : "sell"}`}>{it.t.side === "BUY" ? "Buy" : "Sell"}</div>
            <div className="ticket-body">
              <div className="ticket-line">
                <strong>{it.t.qty.toLocaleString()} {it.t.symbol}</strong>
                <span className="muted"> at {fmtPrice(it.t.price)}</span>
                <span className="ticket-meta"><span>Day {it.t.trading_day}</span><span>Fee {fmtMoney(it.t.fee)}</span></span>
              </div>
              <p className="rationale">{it.t.rationale}</p>
            </div>
          </li>
        ) : (
          <li key={it.key} className="ticket void">
            <div className="stamp void">Rejected</div>
            <div className="ticket-body">
              <div className="ticket-line">
                <strong>{it.r.side === "BUY" ? "Buy" : "Sell"} {it.r.qty.toLocaleString()} {it.r.symbol}</strong>
                <span className="ticket-meta"><span>Day {it.r.trading_day}</span><span>Stopped by the risk engine</span></span>
              </div>
              <p className="rationale">{it.r.reason}</p>
            </div>
          </li>
        ),
      )}
    </ol>
  );
}
