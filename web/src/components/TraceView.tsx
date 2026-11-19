import { useEffect, useState } from "react";
import { api, type Trace, type TraceSpan } from "../api";

interface Row { span: TraceSpan; depth: number }

function flatten(s: TraceSpan, depth = 0, out: Row[] = []): Row[] {
  out.push({ span: s, depth });
  s.children.forEach((c) => flatten(c, depth + 1, out));
  return out;
}

function label(name: string): string {
  if (name.startsWith("trader.")) return "Trader session";
  if (name === "agent.researcher") return "Research analyst (sub-agent)";
  if (name.startsWith("agent.")) return "Agent loop";
  if (name === "model.turn") return "Model turn";
  if (name === "llm.respond") return "LLM API call";
  if (name.startsWith("tool.")) return name.slice(5);
  return name;
}

/** Waterfall of every span in one trader session: where the time went, turn by turn. */
export function TraceView({ traceId }: { traceId?: string }) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setTrace(null);
    setError("");
    if (traceId) api.trace(traceId).then(setTrace).catch(() => setError("This trace is no longer in memory. Run a new day to record one."));
  }, [traceId]);

  if (!traceId) return <p className="empty">Run a trading day to record a trace.</p>;
  if (error) return <p className="empty">{error}</p>;
  if (!trace) return <p className="empty">Loading trace</p>;

  const rows = flatten(trace.root);
  const turns = rows.filter((r) => r.span.name === "model.turn").length;
  const total = Math.max(trace.root.duration_ms, 0.001);
  return (
    <div className="trace">
      <dl className="trace-summary">
        <div><dt>Duration</dt><dd className="num">{trace.root.duration_ms.toFixed(0)} ms</dd></div>
        <div><dt>Model turns</dt><dd className="num">{turns}</dd></div>
        <div><dt>Tokens</dt><dd className="num">{(trace.usage.prompt_tokens + trace.usage.completion_tokens).toLocaleString()}</dd></div>
        <div><dt>Cost</dt><dd className="num">${trace.usage.cost_usd.toFixed(4)}</dd></div>
      </dl>
      <ol className="waterfall" aria-label="Spans in this session, with start time and duration">
        {rows.map(({ span, depth }, i) => (
          <li key={i} className={span.name.startsWith("tool.") ? "is-tool" : span.name === "model.turn" ? "is-llm" : ""}>
            <span className="wf-name" style={{ paddingLeft: depth * 12 }} title={span.name}>{label(span.name)}</span>
            <span className="wf-track" aria-hidden="true">
              <span className="wf-bar" style={{
                left: `${(span.offset_ms / total) * 100}%`,
                width: `max(2px, ${(span.duration_ms / total) * 100}%)`,
              }} />
            </span>
            <span className="wf-ms num">{span.duration_ms < 1 ? "<1" : span.duration_ms.toFixed(0)} ms</span>
          </li>
        ))}
      </ol>
      <p className="muted small">Trace {trace.id}. Rule-based brains run in milliseconds; with an LLM brain, model turns dominate.</p>
    </div>
  );
}
