import { api } from "../api/client";
import type { Usage } from "../api/types";
import { EmptyState, ErrorState, SkeletonBlock } from "../components/ui";
import { fmt, usd } from "../lib/format";
import { useScope } from "../lib/scope";
import { useApi } from "../lib/useApi";
import { AreaChart } from "./usage/AreaChart";
import { barPercent, parseWindow, projectCost, SERIES, toolLabel, WINDOWS } from "./usage/chart";
import "./usage/usage.css";

function Kpi({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      <div className="note">{note}</div>
    </div>
  );
}

function Kpis({ u }: { u: Usage }) {
  const t = u.totals;
  return (
    <div className="us-kpis">
      <Kpi label="Calls" value={fmt(t.calls)} note="tool calls" />
      <Kpi label="Context out" value={fmt(t.output_tokens)} note="tokens injected" />
      <Kpi label="Arguments in" value={fmt(t.input_tokens)} note="tokens" />
      <Kpi label="Embeddings" value={fmt(t.embedding_tokens)} note="tokens" />
      <Kpi label="Cost" value={usd(t.embedding_cost_usd)} note="embedding USD" />
    </div>
  );
}

function ByTool({ rows }: { rows: Usage["by_tool"] }) {
  const sorted = [...rows].sort((a, b) => b.output_tokens - a.output_tokens);
  const max = sorted[0]?.output_tokens ?? 0;
  if (sorted.length === 0) return <p className="muted small">No tool calls in this window.</p>;
  return (
    <div data-testid="by-tool">
      {sorted.map((r) => (
        <div className="us-tool" key={r.tool}>
          <span className="mono" title={r.tool}>{toolLabel(r.tool)}</span>
          <span className="us-track" aria-hidden="true"><div className="us-fill" style={{ width: `${barPercent(r.output_tokens, max)}%` }} /></span>
          <span className="num right">{fmt(r.output_tokens)}</span>
        </div>
      ))}
    </div>
  );
}

function ByProject({ u }: { u: Usage }) {
  const rows = [...u.by_project].sort((a, b) => b.output_tokens - a.output_tokens);
  if (rows.length === 0) return <p className="muted small">No project has any usage in this window.</p>;
  return (
    <div className="table-wrap">
      <table aria-label="Spend by project">
        <thead>
          <tr>
            <th>Project</th>
            <th className="right">Calls</th>
            <th className="right">Context out</th>
            <th className="right">Embedding tokens</th>
            <th className="right">Cost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.project} style={{ cursor: "default" }}>
              <td><span className="tag">{p.project || "(none)"}</span></td>
              <td className="right num">{fmt(p.calls)}</td>
              <td className="right num">{fmt(p.output_tokens)}</td>
              <td className="right num">{fmt(p.embedding_tokens)}</td>
              <td className="right num">{usd(projectCost(u.totals, p.embedding_tokens))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function UsagePage() {
  const { project, user, param, setParams } = useScope();
  const days = parseWindow(param("days"));
  const usage = useApi(`usage:${days}:${project}:${user}`, () => api.usage(days, { project: project || undefined, user: user || undefined }));
  const u = usage.data;
  const empty = u !== undefined && u.totals.calls === 0 && u.by_day.length === 0;

  return (
    <>
      <div className="page-head" style={{ display: "flex", alignItems: "flex-start" }}>
        <div>
          <h1>Usage</h1>
          <p>What each tool injects into agent context, and what embeddings cost.</p>
        </div>
        <div className="grow" />
        <div className="seg" role="group" aria-label="Window">
          {WINDOWS.map((d) => (
            <button key={d} className={d === days ? "on" : ""} aria-pressed={d === days} onClick={() => setParams({ days: d === 30 ? null : String(d) })}>
              {d} d
            </button>
          ))}
        </div>
      </div>
      {usage.error ? (
        <div className="card"><ErrorState error={usage.error} onRetry={usage.reload} what="usage" /></div>
      ) : !u ? (
        <>
          <div className="us-kpis">{Array.from({ length: 5 }, (_, i) => <div key={i} className="card"><SkeletonBlock lines={2} /></div>)}</div>
          <div className="card"><SkeletonBlock lines={6} /></div>
        </>
      ) : empty ? (
        <div className="card">
          <EmptyState icon="usage" title="No usage in this window">
            Every tool call is metered. Calls made by agents against this server show up here
            {project ? <> (filtered to {project})</> : null}.
          </EmptyState>
        </div>
      ) : (
        <>
          <Kpis u={u} />
          <div className="us-mid">
            <div className="card">
              <div className="card-h">
                <h2>Tokens per day</h2>
                <div className="grow" />
                <div className="us-legend legend">
                  {SERIES.slice().reverse().map((s) => (
                    <span key={s.key} className="key"><span className="swatch" style={{ background: s.color }} />{s.label}</span>
                  ))}
                </div>
              </div>
              <AreaChart days={u.by_day} />
            </div>
            <div className="card">
              <div className="card-h"><h2>Context tokens by tool</h2></div>
              <ByTool rows={u.by_tool} />
            </div>
          </div>
          <div className="card flush">
            <div className="card-h"><h2>Spend by project</h2></div>
            <ByProject u={u} />
          </div>
        </>
      )}
    </>
  );
}
