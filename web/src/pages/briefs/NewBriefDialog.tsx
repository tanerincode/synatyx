import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../../api/client";
import type { Brief } from "../../api/types";
import { Dialog } from "../../components/ui";
import { scoped, useScope } from "../../lib/scope";
import { useToast } from "../../lib/toast";
import { BRIEF_TEMPLATES, defaultOwner, knownAgents } from "./shared";

export function NewBriefDialog({ open, onClose, projects, briefs }: { open: boolean; onClose: () => void; projects: string[]; briefs: Brief[] | undefined }) {
  const { project, user } = useScope();
  const navigate = useNavigate();
  const [sp] = useSearchParams();
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [proj, setProj] = useState(project || projects[0] || "");
  const [assignee, setAssignee] = useState("");
  const [owner, setOwner] = useState(user);
  // falls back to the most common owner once the list has loaded
  const ownerFallback = defaultOwner(briefs);
  const [template, setTemplate] = useState("Brief");
  const [body, setBody] = useState(BRIEF_TEMPLATES.Brief ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    const who = owner.trim() || ownerFallback;
    const missing = [!title.trim() && "title", !proj.trim() && "project", !who && "owner"].filter(Boolean);
    if (missing.length) {
      setError(`Missing ${missing.join(", ")}.`);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const created = await api.createBrief({ user: who, project: proj.trim(), title: title.trim(), body, assignee: assignee.trim() || null });
      toast(`Created “${created.title}” (${created.status})`);
      onClose();
      navigate(scoped(`/briefs/${created.id}`, sp, { owner: created.user_id }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} wide label="New brief">
      <form onSubmit={submit}>
        <h3>New brief</h3>
        <p>Creates a handoff at v1. Its status is <b>assigned</b> when an assignee is set, otherwise <b>draft</b>.</p>
        <div className="nb-grid">
          <label className="field nb-title">
            <span>Title</span>
            <input className="input" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What should get done" />
          </label>
          <label className="field">
            <span>Project</span>
            <input className="input" list="nb-projects" value={proj} onChange={(e) => setProj(e.target.value)} />
            <datalist id="nb-projects">{projects.map((p) => <option key={p} value={p} />)}</datalist>
          </label>
          <label className="field">
            <span>Assignee</span>
            <input className="input" list="nb-agents" value={assignee} onChange={(e) => setAssignee(e.target.value)} placeholder="— draft —" />
            <datalist id="nb-agents">{knownAgents(briefs).map((a) => <option key={a} value={a} />)}</datalist>
          </label>
          <label className="field">
            <span>Owner (user)</span>
            <input className="input" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder={ownerFallback || "user id"} />
          </label>
        </div>
        <div className="field" style={{ marginTop: 12 }}>
          <span>Template</span>
          <div className="seg" role="group" aria-label="Template">
            {Object.keys(BRIEF_TEMPLATES).map((t) => (
              <button
                type="button"
                key={t}
                className={template === t ? "on" : ""}
                onClick={() => {
                  setTemplate(t);
                  setBody(BRIEF_TEMPLATES[t] ?? "");
                }}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        <label className="field" style={{ marginTop: 12 }}>
          <span>Body · markdown</span>
          <textarea className="input mono" rows={11} value={body} onChange={(e) => setBody(e.target.value)} style={{ fontSize: 12, resize: "vertical" }} />
        </label>
        {error && <p role="alert" className="small" style={{ color: "var(--critical)", marginTop: 10 }}>{error}</p>}
        <div className="acts">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy || (!owner.trim() && briefs === undefined)} title={!owner.trim() && briefs === undefined ? "Loading the default owner…" : undefined}>{busy ? "Creating…" : "Create brief"}</button>
        </div>
      </form>
    </Dialog>
  );
}
