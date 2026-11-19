import type { FloorEvent } from "../api";

const LABEL: Record<string, string> = {
  get_account: "Checked the account",
  recall: "Read notes from earlier sessions",
  get_market_snapshot: "Read the market",
  get_price: "Looked up a price",
  get_price_history: "Pulled price history",
  get_transactions: "Reviewed recent trades",
  list_securities: "Listed securities",
  remember: "Wrote a note",
  forget: "Deleted a note",
  buy_shares: "Bought",
  sell_shares: "Sold",
  search_news: "Searched the news wire",
  get_company_profile: "Read a company profile",
};

function brainLabel(b: string): string {
  return b.startsWith("policy:") ? `Rule-based ${b.slice(7)} policy` : `Model: ${b}`;
}

/** Events of the most recent session for one trader, in order. */
export function lastSession(events: FloorEvent[]): FloorEvent[] {
  const start = events.map((e) => e.kind).lastIndexOf("session_start");
  return start >= 0 ? events.slice(start) : events;
}

function describe(e: FloorEvent): { title: string; detail?: string; tool?: string; tone?: "warn" | "quiet" } | null {
  const d = e.data as Record<string, unknown>;
  const args = (d.args ?? {}) as Record<string, unknown>;
  const isResearcher = d.agent === "researcher";
  switch (e.kind) {
    case "session_start":
      return { title: `Session opened on day ${e.trading_day}`, detail: brainLabel(String(d.brain)), tone: "quiet" };
    case "thought":
      return isResearcher ? null : { title: "Thinking", detail: String(d.text) };
    case "tool_call": {
      if (isResearcher) return { title: `Analyst: ${LABEL[String(d.tool)] ?? d.tool}`, tool: String(d.tool), tone: "quiet" };
      if (d.tool === "buy_shares" || d.tool === "sell_shares")
        return { title: `${LABEL[String(d.tool)]} ${args.quantity} ${args.symbol}`, detail: String(args.rationale ?? ""), tool: String(d.tool) };
      if (d.tool === "research") return null; // shown as research_request / research_brief
      if (d.tool === "remember") return { title: LABEL.remember, detail: String(args.value ?? ""), tool: "remember" };
      return { title: LABEL[String(d.tool)] ?? String(d.tool), tool: String(d.tool) };
    }
    case "tool_error":
      return {
        title: d.tool === "buy_shares" || d.tool === "sell_shares" ? "Order rejected by the risk engine" : `Tool error: ${d.tool}`,
        detail: String(d.result ?? "").replace(/^Error executing tool \w+: /, ""),
        tool: String(d.tool),
        tone: "warn",
      };
    case "research_request":
      return { title: "Asked the research analyst", detail: String(d.question), tool: "research" };
    case "research_brief":
      return { title: "Research brief", detail: String(d.brief) };
    case "session_end":
      return { title: "Session closed", detail: String(d.summary) };
    case "session_error":
      return { title: "Session failed", detail: String(d.error), tone: "warn" };
    default:
      return null;
  }
}

export function Activity({ events, live }: { events: FloorEvent[]; live: boolean }) {
  const steps = lastSession(events).map((e) => ({ e, v: describe(e) })).filter((x) => x.v);
  if (!steps.length) return <p className="empty">The agent's tool calls for its latest session show up here.</p>;
  return (
    <ol className={`activity ${live ? "live" : ""}`} aria-live="polite">
      {steps.map(({ e, v }) => (
        <li key={e.id} className={`step ${v!.tone ?? ""}`}>
          <div className="step-head">
            <span className="step-title">{v!.title}</span>
            {v!.tool && <code className="tool">{v!.tool}</code>}
          </div>
          {v!.detail && <p className="step-detail">{v!.detail}</p>}
        </li>
      ))}
    </ol>
  );
}
