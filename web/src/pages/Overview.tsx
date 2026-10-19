import { Link, useSearchParams } from "react-router-dom";
import { api, collectionFor } from "../api/client";
import { LAYER_NAMES, LAYERS } from "../api/types";
import { Agent, ErrorState, Icon, SkeletonBlock, StatusChip, type IconName } from "../components/ui";
import { ago, fmt, usd } from "../lib/format";
import { useShell } from "../lib/nav";
import { scoped, useScope } from "../lib/scope";
import { useApi } from "../lib/useApi";

function Spark({ values, color }: { values: number[]; color: string }) {
  if (values.length < 2) return null;
  const w = 120;
  const h = 22;
  const mx = Math.max(...values);
  const mn = Math.min(...values);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - 2 - ((v - mn) / (mx - mn || 1)) * (h - 4)}`).join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" width="100%" aria-hidden="true">
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Kpi({ label, value, note, hero, children }: { label: string; value: React.ReactNode; note?: React.ReactNode; hero?: boolean; children?: React.ReactNode }) {
  return (
    <div className={`card kpi${hero ? " hero" : ""}`}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {note && <div className="note">{note}</div>}
      {children}
    </div>
  );
}

export function OverviewPage() {
  const { overview } = useShell();
  const [sp] = useSearchParams();
  const { project, user } = useScope();
  const briefs = useApi(`ov:briefs:${project}`, () => api.briefs({ project: project || undefined }));
  const tasks = useApi("ov:tasks", () => api.tasks("all"));
  const usage = useApi(`ov:usage:${project}:${user}`, () => api.usage(30, { project: project || undefined, user: user || undefined }));
  const skills = useApi(`ov:skills:${user}`, () => api.skills({ user: user || undefined }));
  const hygiene = useApi(project ? `ov:hyg:${project}:${user}` : null, () => api.hygiene(collectionFor(project), user || undefined));

  if (!overview) return <SkeletonBlock lines={8} />;
  const cols = overview.collections.filter((c) => !project || c.slug === project || c.is_user_global);
  const projCol = overview.collections.find((c) => c.slug === project);
  const t = project && projCol ? projCol : overview.totals;
  const bs = briefs.data?.briefs ?? [];
  const ts = (tasks.data?.tasks ?? []).filter((x) => (!project || x.project === project) && (!user || x.user_id === user));
  const openTasks = ts.filter((x) => x.status === "pending" || x.status === "in_progress");
  const mx = Math.max(1, ...cols.map((c) => c.active));
  const blocked = bs.filter((b) => b.status === "blocked");

  const attention: { icon: IconName; color: string; title: string; detail: string; to: string }[] = [
    ...blocked.map((b) => ({ icon: "alert" as const, color: "var(--s-blocked)", title: `Blocked brief · ${b.title}`, detail: `${b.assignee ?? "unassigned"} · ${b.last_report?.text ?? "no report"}`, to: scoped(`/briefs/${b.id}`, sp, { owner: b.user_id }) })),
  ];
  if (hygiene.data) {
    const h = hygiene.data;
    if (h.stale.length) attention.push({ icon: "file", color: "var(--warn)", title: `${h.stale.length} memories possibly stale`, detail: "Their source files changed since they were stored", to: scoped("/memories", sp, { stale: "1" }) });
    if (h.expiring_7d) attention.push({ icon: "clock", color: "var(--rose)", title: `${h.expiring_7d} items expire in 7 days`, detail: "Unpinned items past their decay window", to: scoped("/hygiene", sp) });
    if (h.duplicates.length) attention.push({ icon: "dup", color: "var(--r-alternative_to)", title: `${h.duplicates.length} duplicate suggestions`, detail: "alternative_to pairs waiting for keep / deprecate", to: scoped("/hygiene", sp) });
  }

  // activity: brief reports and task changes (no server-side feed yet)
  const feed = [
    ...bs.filter((b) => b.last_report).map((b) => ({ at: b.last_report?.at ?? b.updated_at, icon: "msg" as const, color: "var(--cyan)", node: <><b>{b.last_report?.agent}</b> on <Link to={scoped(`/briefs/${b.id}`, sp, { owner: b.user_id })}><b>{b.title}</b></Link>: {b.last_report?.text}</>, project: b.project })),
    ...ts.map((x) => ({ at: x.updated_at ?? x.created_at, icon: "tasks" as const, color: "var(--violet)", node: <>Task <b>{x.title}</b> · {x.status.replace("_", " ")}</>, project: x.project ?? "" })),
  ]
    .filter((f) => f.at)
    .sort((a, b) => (b.at ?? "").localeCompare(a.at ?? ""))
    .slice(0, 9);

  const days = usage.data?.by_day ?? [];
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p>{project ? <>Project <b>{project}</b></> : "All projects"}{user && <> · user <b>{user}</b></>} · L4 preferences are shared across projects</p>
        </div>
      </div>
      <div className="kpis">
        <Kpi hero label="Memories active" value={fmt(t.active)} note={`of ${fmt(t.total)} total`}>
          <div className="layer-mini">{LAYERS.map((L) => <span key={L} title={`${L}: ${t.by_layer[L]}`} style={{ flex: t.by_layer[L] || 0, background: `var(--${L.toLowerCase()})` }} />)}</div>
        </Kpi>
        <Kpi label="Deprecated" value={t.deprecated} note="GC removes them later" />
        <Kpi label="Pinned" value={t.pinned} note="checkpoints, protected" />
        <Kpi label="Open tasks" value={tasks.data ? openTasks.length : "…"} note={`${ts.filter((x) => x.status === "in_progress").length} in progress`} />
        <Kpi label="Active briefs" value={briefs.data ? bs.filter((b) => ["assigned", "in_progress", "blocked"].includes(b.status)).length : "…"} note={<><span style={{ color: "var(--s-blocked)" }}>{blocked.length} blocked</span> · {bs.filter((b) => b.status === "draft").length} draft</>} />
        <Kpi label="Skills" value={skills.data ? skills.data.skills.length : "…"} note={user ? `for ${user}` : "all users"} />
        <Kpi label="Cost · 30 d" value={usage.data ? usd(usage.data.totals.embedding_cost_usd) : "…"} note="embeddings">
          <Spark values={days.map((d) => d.embedding_tokens)} color="var(--violet)" />
        </Kpi>
      </div>
      <div className="ov-grid">
        <div className="grid">
          <div className="card">
            <div className="card-h">
              <h2>Active memories by {project ? "collection" : "project"}</h2>
              <div className="grow" />
              <div className="legend">{LAYERS.map((L) => <span key={L} className="key"><span className="swatch" style={{ background: `var(--${L.toLowerCase()})` }} />{L} {LAYER_NAMES[L]}</span>)}</div>
            </div>
            <div className="bars">
              {cols.map((c) => (
                <Link key={c.collection} className="br" to={scoped("/memories", c.is_user_global ? sp : new URLSearchParams({ ...Object.fromEntries(sp), project: c.slug }), c.is_user_global ? { collection: c.collection } : {})}>
                  <span className="mono small" style={{ color: "var(--ink-2)" }}>{c.is_user_global ? "global · L4" : c.slug}</span>
                  <span className="stack" style={{ width: `${Math.max(4, (c.active / mx) * 100)}%` }}>
                    {LAYERS.filter((L) => c.by_layer[L]).map((L) => <span key={L} title={`${L}: ${c.by_layer[L]}`} style={{ width: `${(c.by_layer[L] / Math.max(1, c.active)) * 100}%`, background: `var(--${L.toLowerCase()})` }} />)}
                  </span>
                  <span className="num right">{c.active}</span>
                </Link>
              ))}
            </div>
          </div>
          <div className="card">
            <div className="card-h"><h2>Recent activity</h2><div className="grow" /><span className="muted small">brief reports · task changes</span></div>
            {briefs.error ? <ErrorState error={briefs.error} onRetry={briefs.reload} what="activity" /> : !briefs.data || !tasks.data ? <SkeletonBlock /> : feed.length === 0 ? <p className="muted small">Nothing yet. Brief reports and task changes show up here.</p> : (
              <div className="feed">
                {feed.map((f, i) => (
                  <div key={i} className="fi">
                    <span className="ic" style={{ color: f.color }}><Icon name={f.icon} /></span>
                    <span className="what">{f.node} {f.project && <span className="tag ghost" style={{ marginLeft: 4 }}>{f.project}</span>}</span>
                    <span className="when">{ago(f.at)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="grid" style={{ alignContent: "start" }}>
          <div className="card" data-testid="attention">
            <div className="card-h"><h2>Needs attention</h2><div className="grow" />{!project && <span className="muted small">pick a project for hygiene</span>}<span className="tag warn">{attention.length}</span></div>
            {attention.length === 0 ? <p className="muted small">Nothing blocked or stale.</p> : attention.map((a, i) => (
              <Link key={i} className="attn" to={a.to}>
                <span className="ic" style={{ color: a.color, borderColor: `color-mix(in srgb, ${a.color} 40%, transparent)` }}><Icon name={a.icon} /></span>
                <span style={{ minWidth: 0 }}><div className="t">{a.title}</div><div className="d">{a.detail}</div></span>
                <span className="go muted"><Icon name="arrow" size={14} /></span>
              </Link>
            ))}
          </div>
          <div className="card">
            <div className="card-h"><h2>Briefs in flight</h2><div className="grow" /><Link to={scoped("/briefs", sp)} className="small" style={{ color: "var(--accent-ink)" }}>Open board →</Link></div>
            {!briefs.data ? <SkeletonBlock lines={3} /> : bs.filter((b) => ["in_progress", "blocked", "assigned"].includes(b.status)).slice(0, 5).map((b) => (
              <Link key={b.id} className="attn" style={{ alignItems: "center" }} to={scoped(`/briefs/${b.id}`, sp, { owner: b.user_id })}>
                <span style={{ minWidth: 0, flex: 1 }}><div className="t">{b.title}</div><div className="d row" style={{ marginTop: 3 }}><Agent name={b.assignee} /><span className="faint">·</span>{ago(b.updated_at)}</div></span>
                <StatusChip status={b.status} />
              </Link>
            ))}
          </div>
          <div className="card">
            <div className="card-h"><h2>Server</h2></div>
            <dl className="kv">
              <dt>Version</dt><dd className="mono">v{overview.server.version} · {overview.server.commit}</dd>
              <dt>MCP tools</dt><dd>{overview.server.tools}</dd>
              <dt>Collections</dt><dd>{overview.totals.collections} (incl. global L4)</dd>
            </dl>
          </div>
        </div>
      </div>
    </>
  );
}
