import { useEffect, useMemo, useRef, useState } from "react";
import type { EquityView } from "../api";
import { fmtPct } from "../format";

interface Props {
  data: EquityView;
  names: Record<string, string>;
  selected: string;
  onSelect: (id: string) => void;
  startingCash?: number;
}

const H = 300;
const PAD_WIDE = { top: 16, right: 132, bottom: 28, left: 48 };
const PAD_NARROW = { top: 16, right: 112, bottom: 28, left: 40 };

function niceTicks(min: number, max: number, count = 5): number[] {
  const span = max - min || 0.01;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) ?? raw;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(Number(v.toFixed(6)));
  return ticks;
}

export function EquityChart({ data, names, selected, onSelect, startingCash = 100_000 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(320, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  const series = useMemo(() => {
    const toRet = (pts: { day: number; equity: number }[]) => pts.map((p) => ({ day: p.day, r: p.equity / startingCash - 1 }));
    const traders = Object.entries(data.traders).map(([id, pts]) => ({ id, pts: toRet(pts) }));
    return { traders, bench: toRet(data.benchmark) };
  }, [data, startingCash]);

  const PAD = width < 560 ? PAD_NARROW : PAD_WIDE;
  const all = [...series.traders.flatMap((s) => s.pts), ...series.bench];
  const maxDay = Math.max(1, ...all.map((p) => p.day));
  const lo = Math.min(0, ...all.map((p) => p.r));
  const hi = Math.max(0, ...all.map((p) => p.r));
  const span = Math.max(hi - lo, 0.02); // never zoom in tighter than a 2% range
  const mid = (hi + lo) / 2;
  const y0 = Math.min(lo, mid - span / 2) - span * 0.12;
  const y1 = Math.max(hi, mid + span / 2) + span * 0.12;
  const iw = width - PAD.left - PAD.right;
  const ih = H - PAD.top - PAD.bottom;
  const x = (d: number) => PAD.left + (d / maxDay) * iw;
  const y = (r: number) => PAD.top + (1 - (r - y0) / (y1 - y0)) * ih;
  const path = (pts: { day: number; r: number }[]) =>
    pts.map((p, i) => `${i ? "L" : "M"}${x(p.day).toFixed(1)},${y(p.r).toFixed(1)}`).join("");

  // end-of-line labels, nudged apart so they never overlap
  const labels = [
    ...series.traders.map((s) => ({ id: s.id, name: names[s.id] ?? s.id, r: s.pts.at(-1)?.r ?? 0 })),
    { id: "benchmark", name: "Benchmark", r: series.bench.at(-1)?.r ?? 0 },
  ]
    .map((l) => ({ ...l, py: y(l.r) }))
    .sort((a, b) => a.py - b.py);
  for (let i = 1; i < labels.length; i++) labels[i].py = Math.max(labels[i].py, labels[i - 1].py + 16);

  const yTicks = niceTicks(y0, y1);
  const tickDigits = yTicks.length > 1 && yTicks[1] - yTicks[0] < 0.01 ? 1 : 0;
  const xTicks = niceTicks(0, maxDay, Math.max(2, Math.min(8, maxDay, Math.floor(iw / 72)))).filter((d) => Number.isInteger(d));
  const ordered = [...series.traders].sort((a) => (a.id === selected ? 1 : -1)); // selected drawn last, on top

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const d = Math.round(((e.clientX - rect.left - PAD.left) / iw) * maxDay);
    setHover(d >= 0 && d <= maxDay ? d : null);
  };
  const at = (pts: { day: number; r: number }[], d: number) => pts.find((p) => p.day === d)?.r;

  return (
    <div className="chart" ref={ref}>
      <svg width={width} height={H} onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img"
        aria-label="Return since the first trading day for each trader and the equal-weight benchmark">
        {yTicks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={PAD.left + iw} y1={y(t)} y2={y(t)} className={t === 0 ? "grid zero" : "grid"} />
            <text x={PAD.left - 8} y={y(t) + 4} className="axis" textAnchor="end">{fmtPct(t, tickDigits)}</text>
          </g>
        ))}
        {xTicks.map((d) => (
          <text key={d} x={x(d)} y={H - 8} className="axis" textAnchor="middle">{d === 0 ? "Start" : `Day ${d}`}</text>
        ))}
        <path d={path(series.bench)} className="line bench" />
        {ordered.map((s) => (
          <path key={s.id} d={path(s.pts)} className={s.id === selected ? "line selected" : "line"}
            onClick={() => onSelect(s.id)} />
        ))}
        {labels.map((l) => (
          <g key={l.id} className={`end-label ${l.id === selected ? "selected" : ""}`}
            onClick={() => l.id !== "benchmark" && onSelect(l.id)}>
            <text x={PAD.left + iw + 10} y={l.py + 4}>
              <tspan className="end-name">{l.name}</tspan>
              <tspan dx="6" className="end-value">{fmtPct(l.r)}</tspan>
            </text>
          </g>
        ))}
        {hover != null && (
          <g pointerEvents="none">
            <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + ih} className="crosshair" />
            {series.traders.map((s) => {
              const r = at(s.pts, hover);
              return r == null ? null : (
                <circle key={s.id} cx={x(hover)} cy={y(r)} r={s.id === selected ? 4 : 3}
                  className={s.id === selected ? "dot selected" : "dot"} />
              );
            })}
          </g>
        )}
      </svg>
      {hover != null && (
        <div className="tooltip" style={{ left: x(hover) > PAD.left + iw / 2 ? Math.max(0, x(hover) - 188) : x(hover) + 12 }}>
          <div className="tooltip-title">{hover === 0 ? "Start" : `Day ${hover}`}</div>
          {[...series.traders.map((s) => ({ id: s.id, name: names[s.id], r: at(s.pts, hover) })),
            { id: "benchmark", name: "Benchmark", r: at(series.bench, hover) }]
            .filter((v) => v.r != null)
            .sort((a, b) => (b.r ?? 0) - (a.r ?? 0))
            .map((v) => (
              <div key={v.id} className={`tooltip-row ${v.id === selected ? "selected" : ""}`}>
                <span>{v.name}</span>
                <span className="num">{fmtPct(v.r)}</span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
