import { Pause, Play, StepForward } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, DEMO, type EquityView, type FloorEvent, type FloorView, type Metric, type Security, type TraderDetail } from "./api";
import { Activity } from "./components/Activity";
import { EquityChart } from "./components/EquityChart";
import { MarketTable } from "./components/MarketTable";
import { rejectedOrders, Tickets } from "./components/Tickets";
import { TraderPanel } from "./components/TraderPanel";
import { fmtMoney, fmtPct, STRATEGY_LABEL, tone } from "./format";

const LIVE_KINDS = new Set(["session_start", "tool_call", "tool_error", "thought", "research_request", "research_brief", "session_end"]);

export default function App() {
  const [floor, setFloor] = useState<FloorView | null>(null);
  const [equity, setEquity] = useState<EquityView | null>(null);
  const [metrics, setMetrics] = useState<Record<string, Metric>>({});
  const [market, setMarket] = useState<Security[]>([]);
  const [selected, setSelected] = useState<string>("kite");
  const [detail, setDetail] = useState<TraderDetail | null>(null);
  const [events, setEvents] = useState<FloorEvent[]>([]);
  const [status, setStatus] = useState<string>("");
  const [error, setError] = useState<string>("");
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  const loadFloor = useCallback(async () => {
    try {
      const [f, e, m, mk] = await Promise.all([api.floor(), api.equity(), api.metrics(), api.market()]);
      setFloor(f);
      setEquity(e);
      setMetrics(m);
      setMarket(mk.securities);
      setError("");
    } catch (err) {
      setError(DEMO ? "The demo snapshot could not be loaded." : `Cannot reach the Agora API. Start it with "agora serve". (${err})`);
    }
  }, []);

  const loadTrader = useCallback(async (id: string) => {
    const [d, ev] = await Promise.all([api.trader(id), api.events(id)]);
    if (selectedRef.current !== id) return;
    setDetail(d);
    setEvents(ev);
  }, []);

  useEffect(() => { loadFloor(); }, [loadFloor]);
  useEffect(() => { loadTrader(selected).catch(() => {}); }, [selected, loadTrader]);

  // Live updates: tail the event log over SSE.
  useEffect(() => {
    if (DEMO) return;
    const es = new EventSource("/api/stream");
    const onEvent = (msg: MessageEvent) => {
      const ev = JSON.parse(msg.data) as FloorEvent;
      if (ev.kind === "market_open") setStatus(`Trading day ${ev.trading_day} is open`);
      if (LIVE_KINDS.has(ev.kind) && ev.trader_id) {
        const name = ev.trader_id[0].toUpperCase() + ev.trader_id.slice(1);
        if (ev.kind === "tool_call" && String(ev.data.tool).endsWith("_shares")) setStatus(`${name} is placing orders`);
        else if (ev.kind === "research_request") setStatus(`${name} is consulting the research analyst`);
        if (ev.trader_id === selectedRef.current) setEvents((prev) => [...prev, ev]);
      }
      if (ev.kind === "session_end" && ev.trader_id === selectedRef.current) loadTrader(ev.trader_id);
      if (ev.kind === "cycle_done") {
        setStatus("");
        loadFloor();
        loadTrader(selectedRef.current);
      }
    };
    ["market_open", "cycle_done", ...LIVE_KINDS].forEach((k) => es.addEventListener(k, onEvent));
    return () => es.close();
  }, [loadFloor, loadTrader]);

  const names = useMemo(() => Object.fromEntries((floor?.traders ?? []).map((t) => [t.id, t.name])), [floor]);
  const ranked = useMemo(() => [...(floor?.traders ?? [])].sort((a, b) => b.equity - a.equity), [floor]);
  const held = useMemo(() => new Set(detail?.positions.map((p) => p.symbol) ?? []), [detail]);
  const rejected = useMemo(() => rejectedOrders(events), [events]);
  const running = !!floor?.running || !!status;

  const runDay = async () => {
    setStatus("Opening the next trading day");
    try { await api.runDay(); } catch (e) { setError(String(e)); setStatus(""); }
  };
  const toggleAuto = async () => {
    if (!floor) return;
    await api.autopilot(!floor.autopilot.enabled);
    loadFloor();
  };

  if (!floor || !equity) {
    return <div className="boot">{error || "Loading the floor"}</div>;
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="side-inner">
        <div className="brand">
          <span className="mark" aria-hidden="true" />
          <span>Agora</span>
        </div>

        <h2 className="side-label" id="board-label">Leaderboard</h2>
        <ul className="board" aria-labelledby="board-label">
          {ranked.map((t, i) => (
            <li key={t.id}>
              <button className={`board-row ${t.id === selected ? "active" : ""}`} onClick={() => setSelected(t.id)}
                aria-current={t.id === selected}>
                <span className="rank num">{i + 1}</span>
                <span className="who">
                  <span className="name">{t.name}</span>
                  <span className="muted small">{STRATEGY_LABEL[t.strategy] ?? t.strategy}</span>
                </span>
                <span className="val">
                  <span className="num">{fmtMoney(t.equity)}</span>
                  <span className={`num small ${tone(t.return_pct)}`}>{fmtPct(t.return_pct)}</span>
                </span>
              </button>
            </li>
          ))}
          <li className="board-row bench">
            <span className="rank" />
            <span className="who">
              <span className="name">Benchmark</span>
              <span className="muted small">Equal-weight buy and hold</span>
            </span>
            <span className="val"><span className={`num small ${tone(floor.benchmark_return)}`}>{fmtPct(floor.benchmark_return)}</span></span>
          </li>
        </ul>

        <div className="side-foot">
          <p><span className="muted">Brain</span> {floor.brain === "llm" ? floor.model : "Rule-based policies"}</p>
          <p><span className="muted">MCP transport</span> {floor.transport === "stdio" ? "Separate processes" : "In-process"}</p>
          <p><span className="muted">Risk limits</span> {Math.round(floor.limits.max_position_pct * 100)}% per position,{" "}
            {Math.round(floor.limits.max_order_pct * 100)}% per order, {floor.limits.max_trades_per_cycle} trades a day</p>
        </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <h1>Trading day {floor.day}</h1>
            <p className="subtitle" aria-live="polite">
              {DEMO ? "Recorded run. Clone the repo and run agora serve to trade live."
                : status || `${floor.traders.length} traders, each working through MCP tools with its own account`}
            </p>
          </div>
          <div className="actions">
            <button className="btn secondary" onClick={toggleAuto} disabled={DEMO}
              title={DEMO ? "Available when running locally" : undefined}>
              {floor.autopilot.enabled ? <Pause size={16} strokeWidth={1.5} /> : <Play size={16} strokeWidth={1.5} />}
              {floor.autopilot.enabled ? "Pause autopilot" : "Autopilot"}
            </button>
            <button className="btn primary" onClick={runDay} disabled={DEMO || running}
              title={DEMO ? "Available when running locally" : undefined}>
              <StepForward size={16} strokeWidth={1.5} />
              {running ? "Trading…" : "Run next day"}
            </button>
          </div>
        </header>
        {error && <p className="error" role="alert">{error}</p>}

        <div className="grid">
          <div className="col">
          <section className="panel chart-panel" aria-labelledby="chart-title">
            <div className="panel-head">
              <h2 id="chart-title">Return since day 0</h2>
              <span className="muted small">Select a line or a trader to inspect it</span>
            </div>
            <EquityChart data={equity} names={names} selected={selected} onSelect={setSelected} />
          </section>

          <section className="panel" aria-labelledby="tickets-title">
            <div className="panel-head">
              <h2 id="tickets-title">Order tickets</h2>
              <span className="muted small">Every fill carries the agent's own rationale</span>
            </div>
            <Tickets transactions={detail?.transactions ?? []} rejected={rejected} />
          </section>
          </div>

          <div className="col">
          {detail && <TraderPanel t={detail} metric={metrics[selected]} benchmark={floor.benchmark_return} />}

          <section className="panel" aria-labelledby="activity-title">
            <div className="panel-head">
              <h2 id="activity-title">Latest session</h2>
              {detail?.sessions[0] && (
                <span className="muted small num">
                  {detail.sessions[0].turns} turns, {detail.sessions[0].ms} ms
                  {detail.sessions[0].cost_usd ? `, $${detail.sessions[0].cost_usd.toFixed(4)}` : ""}
                </span>
              )}
            </div>
            <Activity events={events} live={running} />
          </section>
          </div>

          <section className="panel wide" aria-labelledby="market-title">
            <div className="panel-head">
              <h2 id="market-title">Market</h2>
              <span className="muted small">Simulated exchange, fictional companies. Highlighted rows are held by {detail?.name}</span>
            </div>
            <MarketTable rows={market} held={held} />
          </section>
        </div>
      </main>
    </div>
  );
}
