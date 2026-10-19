import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../../api/client";
import { BRIEF_STATUSES, type BriefDetail, type BriefPatch, type BriefStatus } from "../../api/types";
import { Agent, ConfirmDialog, EmptyState, ErrorState, Icon, ProjTag, SkeletonBlock, StatusChip } from "../../components/ui";
import { lineDiff } from "../../lib/diff";
import { ago } from "../../lib/format";
import { agentColor, agentInitials, STATUS_LABEL } from "../../lib/labels";
import { Markdown } from "../../lib/markdown";
import { scoped, useScope } from "../../lib/scope";
import { useToast } from "../../lib/toast";
import { useApi } from "../../lib/useApi";
import { knownAgents, threadWithTransitions } from "./shared";

type Pending = { patch: Omit<BriefPatch, "user">; title: string; body: string } | null;

function ReportThread({ brief }: { brief: BriefDetail }) {
  const entries = threadWithTransitions(brief.reports);
  return (
    <div className="card" data-testid="report-thread">
      <div className="card-h">
        <h2>Report thread</h2>
        <div className="grow" />
        <span className="muted small">{brief.reports.length} {brief.reports.length === 1 ? "report" : "reports"} · newest first</span>
      </div>
      {entries.length === 0 ? (
        <EmptyState icon="msg" title="No reports yet">
          The assignee posts them with <span className="mono">context_handoff_report</span>; each can move the status.
        </EmptyState>
      ) : (
        <ol className="thread">
          {entries.map((r, i) => (
            <li key={`${r.at}-${i}`} className="rep">
              <span className="av" style={{ background: agentColor(r.agent) }} aria-hidden="true">{agentInitials(r.agent)}</span>
              <div style={{ minWidth: 0 }}>
                <div className="hd">
                  <b>{r.agent}</b>
                  <time className="when" dateTime={r.at ?? undefined} title={r.at ?? ""}>{ago(r.at)}</time>
                  {r.status && (
                    <span className="trans" aria-label={r.from ? `status ${STATUS_LABEL[r.from]} to ${STATUS_LABEL[r.status]}` : `status ${STATUS_LABEL[r.status]}`}>
                      {r.from && <><StatusChip status={r.from} /><span className="arrow">→</span></>}
                      <StatusChip status={r.status} />
                    </span>
                  )}
                </div>
                <div className="bd">{r.text}</div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Versions({ brief, owner }: { brief: BriefDetail; owner: string }) {
  const older = brief.versions.filter((v) => v.version !== brief.version);
  const [base, setBase] = useState<number | null>(older[0]?.version ?? null);
  const diff = useApi(base !== null ? `brief-ver:${brief.id}:${owner}:${base}:${brief.version}` : null, () => api.briefVersionBody(brief.id, owner, base as number));
  return (
    <>
      {brief.versions.map((v) => (
        <div key={v.version} className="ver-row">
          <span className="v">v{v.version}</span>
          <span><Agent name={v.author} /> <span className="muted small">· {v.at?.slice(0, 16).replace("T", " ")} · {ago(v.at)}</span></span>
          {v.version === brief.version ? (
            <span className="tag">current</span>
          ) : (
            <button className={`btn sm${base === v.version ? " on" : ""}`} aria-pressed={base === v.version} onClick={() => setBase(v.version)}>Compare</button>
          )}
        </div>
      ))}
      {base === null ? (
        <p className="muted small" style={{ marginTop: 12 }}>Only one version. Saving a changed body creates the next one.</p>
      ) : (
        <>
          <div className="section-t">Diff v{base} → v{brief.version}</div>
          {diff.error ? (
            <ErrorState error={diff.error} onRetry={diff.reload} what={`v${base}`} />
          ) : diff.data === undefined ? (
            <SkeletonBlock lines={4} />
          ) : diff.data === null ? (
            <p className="muted small">The server has no body for v{base}.</p>
          ) : (
            <div className="diff" data-testid="version-diff">
              {lineDiff(diff.data, brief.body).map((l, i) => (
                <div key={i} className={l.op}>{l.op === "add" ? "+ " : l.op === "del" ? "- " : "  "}{l.text || " "}</div>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

export function BriefDetailView() {
  const { id = "" } = useParams();
  const [sp] = useSearchParams();
  const { user, param, setParams } = useScope();
  const toast = useToast();
  // brief ids resolve inside one user's scope; the board passes ?owner=
  const ownerParam = param("owner") || user;
  const list = useApi("briefs:all", () => api.briefs());
  const owner = ownerParam || list.data?.briefs.find((b) => b.id === id)?.user_id || "";
  const res = useApi(owner ? `brief:${id}:${owner}` : null, () => api.brief(id, owner));
  const tab = param("tab") === "versions" ? "versions" : "body";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ title: "", body: "" });
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);

  const back = <Link className="icon-btn" to={scoped("/briefs", sp)} title="Back to briefs" aria-label="Back to briefs"><Icon name="chevron" /></Link>;
  if (!owner && list.data) {
    return <EmptyState icon="briefs" title="Brief not found" action={<Link className="btn" to={scoped("/briefs", sp)}>Back to briefs</Link>}>No brief with this id is visible to the dashboard.</EmptyState>;
  }
  if (res.error) {
    const notFound = res.error instanceof ApiError && res.error.status === 404;
    return notFound
      ? <EmptyState icon="briefs" title="Brief not found" action={<Link className="btn" to={scoped("/briefs", sp)}>Back to briefs</Link>}>It may belong to another user than <span className="mono">{owner}</span>.</EmptyState>
      : <div className="card"><ErrorState error={res.error} onRetry={res.reload} what="this brief" /></div>;
  }
  const b = res.data;
  if (!b) return <div className="bd-head">{back}<div style={{ flex: 1 }}><SkeletonBlock lines={2} /></div></div>;

  const apply = async (patch: Omit<BriefPatch, "user">, done: string): Promise<boolean> => {
    setBusy(true);
    try {
      await api.patchBrief(b.id, { user: owner, ...patch });
      toast(done);
      res.reload();
      list.reload();
      return true;
    } catch (err) {
      toast(`Couldn't save: ${err instanceof Error ? err.message : String(err)}`);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const startEdit = (): void => {
    setDraft({ title: b.title, body: b.body });
    setEditing(true);
  };
  const bodyChanged = editing && draft.body !== b.body;
  const titleChanged = editing && draft.title.trim() !== b.title && draft.title.trim() !== "";
  const save = async (): Promise<void> => {
    const patch: Omit<BriefPatch, "user"> = {};
    if (bodyChanged) patch.body = draft.body;
    if (titleChanged) patch.title = draft.title.trim();
    if (await apply(patch, bodyChanged ? `Saved as v${b.version + 1}` : "Saved")) setEditing(false);
  };

  return (
    <>
      <div className="bd-head">
        {back}
        <div style={{ flex: 1, minWidth: 0 }}>
          {editing ? (
            <input className="input bd-title-input" aria-label="Title" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
          ) : (
            <h1>{b.title}</h1>
          )}
          <div className="bd-meta">
            <StatusChip status={b.status} />
            <ProjTag project={b.project} />
            <span className="mono faint small">{b.slug}</span>
            <span className="tag ghost">v{b.version}</span>
            <span className="muted small">owner {b.user_id} · updated {ago(b.updated_at)}</span>
          </div>
        </div>
        <div className="row" style={{ gap: 10, alignItems: "flex-end" }}>
          <label className="field">
            <span>Status</span>
            <select
              className="select"
              aria-label="Status"
              value={b.status}
              disabled={busy}
              onChange={(e) => {
                const s = e.target.value as BriefStatus;
                setPending({ patch: { status: s }, title: "Change status?", body: `${STATUS_LABEL[b.status]} → ${STATUS_LABEL[s]}. Agents see the new status on their next context_handoff_get.` });
              }}
            >
              {BRIEF_STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Assignee</span>
            <select
              className="select"
              aria-label="Assignee"
              value={b.assignee ?? ""}
              disabled={busy}
              onChange={(e) => setPending({ patch: { assignee: e.target.value }, title: "Reassign brief?", body: `${b.assignee ?? "unassigned"} → ${e.target.value}.` })}
            >
              {!b.assignee && <option value="">— unassigned —</option>}
              {knownAgents(list.data?.briefs, b.assignee).map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </label>
        </div>
      </div>

      <div className="bd-grid">
        <div className="card">
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "body"} className={tab === "body" ? "on" : ""} onClick={() => setParams({ tab: null })}>Brief</button>
            <button role="tab" aria-selected={tab === "versions"} className={tab === "versions" ? "on" : ""} onClick={() => setParams({ tab: "versions" })}>
              Versions <span className="faint">{b.versions.length}</span>
            </button>
            <div style={{ flex: 1 }} />
            {tab === "body" && !editing && (
              <button className="btn sm" style={{ margin: "4px 0" }} onClick={startEdit}><Icon name="edit" />Edit</button>
            )}
          </div>
          {tab === "versions" ? (
            <Versions brief={b} owner={owner} />
          ) : editing ? (
            <>
              <div className="editor">
                <textarea aria-label="Body" spellCheck={false} value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
                <Markdown source={draft.body} />
              </div>
              <div className="row wrap" style={{ marginTop: 10 }}>
                <span className="muted small">{bodyChanged ? <>Saving creates <b>v{b.version + 1}</b>.</> : <>Body unchanged: stays v{b.version}.</>}</span>
                <div style={{ flex: 1 }} />
                <button className="btn" onClick={() => setEditing(false)} disabled={busy}>Cancel</button>
                <button className="btn primary" onClick={save} disabled={busy || (!bodyChanged && !titleChanged)}>
                  {busy ? "Saving…" : bodyChanged ? `Save as v${b.version + 1}` : "Save"}
                </button>
              </div>
            </>
          ) : b.body.trim() ? (
            <Markdown source={b.body} />
          ) : (
            <EmptyState icon="edit" title="Empty brief" action={<button className="btn" onClick={startEdit}>Write it</button>} />
          )}
        </div>
        <ReportThread brief={b} />
      </div>

      <ConfirmDialog
        open={pending !== null}
        title={pending?.title ?? ""}
        body={pending?.body ?? ""}
        confirm="Save"
        busy={busy}
        onClose={() => setPending(null)}
        onConfirm={async () => {
          if (!pending) return;
          const p = pending.patch;
          const ok = await apply(p, p.status ? `Status: ${STATUS_LABEL[p.status]}` : `Assigned to ${p.assignee}`);
          if (ok) setPending(null);
        }}
      />
    </>
  );
}
