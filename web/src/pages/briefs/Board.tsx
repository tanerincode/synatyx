import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import type { Brief, BriefStatus } from "../../api/types";
import { Agent, EmptyState, ErrorState, Icon, ProjTag, SkeletonBlock, SkeletonRows, StatusChip } from "../../components/ui";
import { ago } from "../../lib/format";
import { STATUS_LABEL } from "../../lib/labels";
import { useShell } from "../../lib/nav";
import { useScope } from "../../lib/scope";
import { useApi } from "../../lib/useApi";
import { NewBriefDialog } from "./NewBriefDialog";
import { BOARD_STATUSES, useBriefLink } from "./shared";

function BriefCard({ b }: { b: Brief }) {
  const href = useBriefLink();
  return (
    <Link className="bcard" to={href(b)} style={{ ["--sc" as string]: `var(--s-${b.status})` }} data-testid="brief-card">
      <div className="t">{b.title}</div>
      <div className="m">
        <ProjTag project={b.project} />
        <Agent name={b.assignee} />
        <span className="faint mono" style={{ fontSize: 11, marginLeft: "auto" }}>v{b.version} · {ago(b.updated_at)}</span>
      </div>
      <div className="last">{b.last_report ? <><b>{b.last_report.agent}</b> {b.last_report.text}</> : "No reports yet"}</div>
    </Link>
  );
}

function Column({ status, briefs, onNew }: { status: BriefStatus; briefs: Brief[]; onNew: () => void }) {
  return (
    <section className="col" aria-label={STATUS_LABEL[status]}>
      <div className="col-h">
        <span className="dotc" style={{ background: `var(--s-${status})` }} />
        {STATUS_LABEL[status]}
        <span className="n">{briefs.length}</span>
        {status === "draft" && (
          <button className="add" onClick={onNew} title="New brief" aria-label="New brief">+</button>
        )}
      </div>
      {briefs.map((b) => <BriefCard key={b.id} b={b} />)}
      {briefs.length === 0 && <div className="col-empty">{status === "blocked" ? "Nothing blocked" : "Empty"}</div>}
    </section>
  );
}

function ListView({ briefs }: { briefs: Brief[] }) {
  const navigate = useNavigate();
  const href = useBriefLink();
  return (
    <div className="card flush">
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Title</th><th>Status</th><th className="hide-md">Project</th><th>Assignee</th><th className="hide-md right">Version</th><th className="hide-md">Last report</th><th className="right">Updated</th></tr>
          </thead>
          <tbody>
            {briefs.map((b) => (
              <tr key={b.id} onClick={() => navigate(href(b))} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && navigate(href(b))}>
                <td style={{ color: "var(--ink)", fontWeight: 550 }}>{b.title}<div className="mono faint" style={{ fontSize: 11 }}>{b.slug}</div></td>
                <td><StatusChip status={b.status} /></td>
                <td className="hide-md"><ProjTag project={b.project} /></td>
                <td><Agent name={b.assignee} /></td>
                <td className="hide-md right num">v{b.version}</td>
                <td className="hide-md small muted" style={{ maxWidth: 340 }}>{b.last_report ? <><b className="mono">{b.last_report.agent}</b> {b.last_report.text}</> : "—"}</td>
                <td className="right num muted">{ago(b.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function BriefsBoard() {
  const { project, user, param, setParams } = useScope();
  const { projects } = useShell();
  const [sp, setSp] = useSearchParams();
  // typing in the filter replaces the history entry instead of pushing one per key
  const setQuery = (v: string): void =>
    setSp((prev) => {
      const next = new URLSearchParams(prev);
      if (v) next.set("q", v);
      else next.delete("q");
      return next;
    }, { replace: true });
  const view = param("view") === "list" ? "list" : "board";
  const q = param("q");
  const assignee = param("assignee");
  const showCancelled = param("cancelled") === "1";
  const creating = sp.get("new") === "1";
  const res = useApi(`briefs:${project}:${user}:${assignee}`, () =>
    api.briefs({ project: project || undefined, user: user || undefined, assignee: assignee || undefined }),
  );
  const all = res.data?.briefs;
  const briefs = (all ?? []).filter((b) => !q || b.title.toLowerCase().includes(q.toLowerCase()) || b.slug.includes(q.toLowerCase()));
  const cols = showCancelled ? [...BOARD_STATUSES, "cancelled" as const] : BOARD_STATUSES;
  const open = briefs.filter((b) => b.status !== "done" && b.status !== "cancelled").length;
  const agents = [...new Set([...(all ?? []).map((b) => b.assignee).filter((a): a is string => !!a), ...(assignee ? [assignee] : [])])].sort();

  let body;
  if (res.error) body = <div className="card"><ErrorState error={res.error} onRetry={res.reload} what="briefs" /></div>;
  else if (!res.data) {
    body = view === "board"
      ? <div className="kanban">{BOARD_STATUSES.map((s) => <div key={s} className="col"><SkeletonBlock lines={3} /></div>)}</div>
      : <div className="card flush"><table><SkeletonRows cols={6} /></table></div>;
  } else if (all?.length === 0) {
    body = (
      <div className="card">
        <EmptyState icon="briefs" title={project ? `No briefs in ${project} yet` : "No briefs yet"} action={<button className="btn primary" onClick={() => setParams({ new: "1" })}><Icon name="plus" />New brief</button>}>
          Orchestrators create them with <span className="mono">context_handoff_create</span>, or start one here.
        </EmptyState>
      </div>
    );
  } else if (view === "board") {
    body = (
      <div className="kanban" style={{ gridTemplateColumns: `repeat(${cols.length}, minmax(220px, 1fr))` }}>
        {cols.map((s) => <Column key={s} status={s} briefs={briefs.filter((b) => b.status === s)} onNew={() => setParams({ new: "1" })} />)}
      </div>
    );
  } else {
    const rows = briefs.filter((b) => showCancelled || b.status !== "cancelled");
    body = rows.length ? <ListView briefs={rows} /> : <div className="card"><EmptyState icon="search" title="No briefs match these filters" /></div>;
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Briefs</h1>
          <p>Handoffs an orchestrator writes for an agent{res.data && ` · ${open} open`}</p>
        </div>
        <div className="grow" />
        <button className="btn primary" onClick={() => setParams({ new: "1" })}><Icon name="plus" />New brief</button>
      </div>
      <div className="toolbar">
        <div className="seg" role="group" aria-label="View">
          <button className={view === "board" ? "on" : ""} aria-pressed={view === "board"} onClick={() => setParams({ view: null })}>Board</button>
          <button className={view === "list" ? "on" : ""} aria-pressed={view === "list"} onClick={() => setParams({ view: "list" })}>List</button>
        </div>
        <span className="input-icon">
          <Icon name="search" />
          <input className="input" aria-label="Filter by title" placeholder="Filter by title…" value={q} onChange={(e) => setQuery(e.target.value)} style={{ width: 220 }} />
        </span>
        <select className="select" aria-label="Assignee" value={assignee} onChange={(e) => setParams({ assignee: e.target.value })}>
          <option value="">Any assignee</option>
          {agents.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <label className="check"><input type="checkbox" checked={showCancelled} onChange={(e) => setParams({ cancelled: e.target.checked ? "1" : null })} />Show cancelled</label>
      </div>
      {body}
      {creating && <NewBriefDialog open onClose={() => setParams({ new: null })} projects={projects} briefs={all} />}
    </>
  );
}
