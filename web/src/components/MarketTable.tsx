import type { Security } from "../api";
import { fmtPct, fmtPrice, tone } from "../format";

export function MarketTable({ rows, held }: { rows: Security[]; held: Set<string> }) {
  return (
    <div className="table-wrap">
      <table className="market">
        <thead>
          <tr>
            <th scope="col">Symbol</th>
            <th scope="col">Company</th>
            <th scope="col" className="r">Price</th>
            <th scope="col" className="r">1 day</th>
            <th scope="col" className="r">20 days</th>
            <th scope="col" className="r">Volatility</th>
            <th scope="col" className="r">P/E</th>
            <th scope="col">Latest headline</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.symbol} className={held.has(s.symbol) ? "held" : ""}>
              <td className="sym">{s.symbol}</td>
              <td>
                <div>{s.name}</div>
                <div className="muted small">{s.sector}</div>
              </td>
              <td className="num r">{fmtPrice(s.price)}</td>
              <td className={`num r ${tone(s.ret_1d)}`}>{fmtPct(s.ret_1d)}</td>
              <td className={`num r ${tone(s.ret_20d)}`}>{fmtPct(s.ret_20d)}</td>
              <td className="num r">{s.vol_20d == null ? "–" : `${Math.round(s.vol_20d * 100)}%`}</td>
              <td className="num r">{s.pe.toFixed(0)}</td>
              <td className="headline">
                {s.latest_news ? (
                  <>
                    <span className={`dot ${s.latest_news.sentiment > 0 ? "up" : "down"}`} aria-hidden="true" />
                    {s.latest_news.headline}
                    <span className="muted small"> {s.latest_news.days_ago === 0 ? "today" : `${s.latest_news.days_ago}d ago`}</span>
                  </>
                ) : (
                  <span className="muted">No recent news</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
