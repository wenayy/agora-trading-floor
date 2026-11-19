import type { Metric, TraderDetail } from "../api";
import { fmtMoney, fmtPct, fmtWeight, STRATEGY_LABEL, tone } from "../format";

export function TraderPanel({ t, metric, benchmark }: { t: TraderDetail; metric?: Metric; benchmark: number }) {
  const excess = t.return_pct - benchmark;
  const note = t.notes.find((n) => n.key === "last_session");
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
          <dd className="num big">{fmtMoney(t.equity)}</dd>
        </div>
        <div>
          <dt>Return</dt>
          <dd className={`num big ${tone(t.return_pct)}`}>{fmtPct(t.return_pct)}</dd>
          <dd className="sub">{fmtPct(excess)} vs benchmark</dd>
        </div>
      </dl>
      <dl className="stats">
        <div title="Annualised, risk-free rate 0. Shown after 10 trading days, when it starts to mean something">
          <dt>Sharpe</dt>
          <dd className="num">{metric && metric.days >= 10 ? metric.sharpe.toFixed(2) : "–"}</dd>
        </div>
        <div title="Largest peak-to-trough fall in equity"><dt>Drawdown</dt><dd className="num">{metric ? fmtPct(metric.max_drawdown) : "–"}</dd></div>
        <div><dt>Trades</dt><dd className="num">{metric?.trades ?? t.transactions.length}</dd></div>
        <div><dt>Cash</dt><dd className="num">{fmtWeight(t.equity ? t.cash / t.equity : 1)}</dd></div>
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

      {note && (
        <>
          <h3 className="subhead">Notebook</h3>
          <p className="note">{note.value}</p>
        </>
      )}
    </section>
  );
}
