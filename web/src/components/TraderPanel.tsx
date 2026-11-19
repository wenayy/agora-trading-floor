import type { Metric, TraderDetail } from "../api";
import { fmtMoney, fmtPct, fmtWeight, STRATEGY_LABEL, tone } from "../format";
import { AnimatedNumber, Hint } from "./ui";

export function TraderPanel({ t, metric, benchmark }: { t: TraderDetail; metric?: Metric; benchmark: number }) {
  const excess = t.return_pct - benchmark;
  const invested = t.positions.reduce((s, p) => s + p.market_value, 0);
  return (
    <section className="panel trader-panel" aria-labelledby="trader-name">
      <header className="trader-head">
        <h2 id="trader-name">{t.name}</h2>
        <span className="strategy">{STRATEGY_LABEL[t.strategy] ?? t.strategy}</span>
      </header>
      <p className="style">{t.style}</p>

      <dl className="figures">
        <div>
          <dt>Equity</dt>
          <dd className="num big"><AnimatedNumber value={t.equity} format={fmtMoney} /></dd>
        </div>
        <div>
          <dt>Return</dt>
          <dd className={`num big ${tone(t.return_pct)}`}>{fmtPct(t.return_pct)}</dd>
          <dd className="sub"><Hint text="Return minus the equal-weight buy-and-hold benchmark over the same days">{fmtPct(excess)} vs benchmark</Hint></dd>
        </div>
      </dl>
      <dl className="stats">
        <div>
          <dt><Hint text="Mean daily return divided by its standard deviation, annualised. Shown after 10 trading days, when it starts to mean something">Sharpe</Hint></dt>
          <dd className="num">{metric && metric.days >= 10 ? metric.sharpe.toFixed(2) : "–"}</dd>
        </div>
        <div><dt><Hint text="Worst peak-to-trough fall in equity so far">Drawdown</Hint></dt><dd className="num">{metric ? fmtPct(metric.max_drawdown) : "–"}</dd></div>
        <div><dt>Trades</dt><dd className="num">{metric?.trades ?? t.transactions.length}</dd></div>
        <div><dt><Hint text="Share of equity held as cash">Cash</Hint></dt><dd className="num">{fmtWeight(t.equity ? t.cash / t.equity : 1)}</dd></div>
      </dl>

      <h3 className="subhead">Holdings</h3>
      {t.positions.length ? (
        <ul className="holdings">
          {[...t.positions].sort((a, b) => b.weight - a.weight).map((p) => (
            <li key={p.symbol}>
              <span className="sym">{p.symbol}</span>
              <span className="bar" aria-hidden="true"><span style={{ width: `${Math.min(100, (p.weight / 0.3) * 100)}%` }} /></span>
              <span className="num">{fmtWeight(p.weight)}</span>
              <span className={`num pnl ${p.unrealized_pnl > 0.5 ? "up" : p.unrealized_pnl < -0.5 ? "down" : ""}`}>{fmtMoney(p.unrealized_pnl)}</span>
            </li>
          ))}
          <li className="holdings-foot">
            <span>Invested {fmtMoney(invested)}</span>
            <span>Bars scale to the 30% position limit</span>
          </li>
        </ul>
      ) : (
        <p className="empty">All cash. Nothing fits the strategy yet.</p>
      )}


    </section>
  );
}
