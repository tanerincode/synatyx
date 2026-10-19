import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ApiError } from "../api/client";
import { LAYER_NAMES, type BriefStatus, type Layer, type TaskStatus } from "../api/types";
import { agentColor, agentInitials, STATUS_LABEL } from "../lib/labels";
import { ToastContext } from "../lib/toast";

// ── icons (lucide-style strokes) ────────────────────────────────
const PATHS = {
  overview: "M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z",
  memories: "M4 5c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6",
  graph: "M6 8.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM18 10.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM9 20.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM8.2 7.2l7.5.4M7 8.4l1.5 7.2M16.4 10l-5.7 6.4",
  briefs: "M3 4h5v16H3zM10 4h5v11h-5zM17 4h4v7h-4z",
  tasks: "M9 11l3 3 8-8M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9",
  skills: "M12 3l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.4 6.8 19.1l1-5.8L3.5 9.2l5.9-.9z",
  code: "M8 6l-6 6 6 6M16 6l6 6-6 6",
  usage: "M3 3v18h18M7 15l4-5 3 3 5-7",
  hygiene: "M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7zM9 12l2 2 4-4",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-3.5-3.5",
  refresh: "M20 11a8 8 0 0 0-14.9-3M4 4v4h4M4 13a8 8 0 0 0 14.9 3M20 20v-4h-4",
  sun: "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  chevron: "M15 18l-6-6 6-6",
  x: "M18 6L6 18M6 6l12 12",
  pin: "M12 17v5M9 10.8V4h6v6.8l3 3.2v2H6v-2z",
  trash: "M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14",
  restore: "M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5",
  copy: "M9 9h11v11H9zM5 15V5a2 2 0 0 1 2-2h10",
  alert: "M12 3l10 18H2zM12 10v4M12 17.5v.5",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  file: "M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  dup: "M3 3h12v12H3zM9 9h12v12H9z",
  plus: "M12 5v14M5 12h14",
  msg: "M21 12a8 8 0 0 1-11.6 7.1L3 21l1.9-6.4A8 8 0 1 1 21 12z",
  arrow: "M5 12h14M13 6l6 6-6 6",
  edit: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
  zoomin: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-3.5-3.5M11 8v6M8 11h6",
  zoomout: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-3.5-3.5M8 11h6",
  fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  key: "M15 7a4 4 0 1 1-3.5 6l-7.5 7H2v-3l7-7.5A4 4 0 0 1 15 7zM16 8h.01",
} as const;
export type IconName = keyof typeof PATHS;
export function Icon({ name, size, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}

// ── pills & chips ───────────────────────────────────────────────
export function LayerPill({ layer }: { layer: Layer }) {
  return (
    <span className={`pill ${layer}`} style={{ ["--dotc" as string]: `var(--${layer.toLowerCase()})` }}>
      <i />
      {layer} · {LAYER_NAMES[layer]}
    </span>
  );
}
export function StatusChip({ status }: { status: BriefStatus | TaskStatus }) {
  return (
    <span className="status" data-status={status}>
      <i style={{ background: `var(--s-${status})` }} />
      {STATUS_LABEL[status]}
    </span>
  );
}
export function Agent({ name }: { name: string | null | undefined }) {
  if (!name) return <span className="agent muted">unassigned</span>;
  return (
    <span className="agent">
      <span className="av" style={{ background: agentColor(name) }}>{agentInitials(name)}</span>
      {name}
    </span>
  );
}
export function ProjTag({ project }: { project: string | null | undefined }) {
  if (!project) return <span className="tag ghost">—</span>;
  if (project === "users") return <span className="tag ghost" title="L4 is shared across projects">global</span>;
  return <span className="tag">{project}</span>;
}
// ── states ──────────────────────────────────────────────────────
export function SkeletonRows({ rows = 8, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <tbody aria-busy="true">
      {Array.from({ length: rows }, (_, r) => (
        <tr key={r}>
          {Array.from({ length: cols }, (_, c) => (
            <td key={c}>
              <div className="sk" style={{ width: c === 1 ? "85%" : 70 }} />
              {c === 1 && <div className="sk" style={{ width: "55%", marginTop: 6 }} />}
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  );
}
export function SkeletonBlock({ lines = 4 }: { lines?: number }) {
  return (
    <div aria-busy="true" style={{ display: "grid", gap: 10 }}>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="sk" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}
export function EmptyState({ icon = "memories", title, children, action }: { icon?: IconName; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <div className="ill"><Icon name={icon} /></div>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}
export function ErrorState({ error, onRetry, what = "data" }: { error: Error; onRetry?: () => void; what?: string }) {
  const status = error instanceof ApiError && error.status ? ` (HTTP ${error.status})` : "";
  return (
    <div className="error-state" role="alert">
      <div className="ill"><Icon name="alert" /></div>
      <h3>Couldn't load {what}</h3>
      <code>{error.message}{status}</code>
      <br />
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          <Icon name="refresh" />
          Retry
        </button>
      )}
    </div>
  );
}

// ── dialogs & toasts ────────────────────────────────────────────
export function Dialog({ open, onClose, children, wide, label }: { open: boolean; onClose: () => void; children: ReactNode; wide?: boolean; label: string }) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="dialog-bg open" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dialog${wide ? " wide" : ""}`} role="dialog" aria-modal="true" aria-label={label}>
        {children}
      </div>
    </div>
  );
}
export function ConfirmDialog(props: { open: boolean; title: string; body: ReactNode; confirm: string; danger?: boolean; busy?: boolean; onConfirm: () => void; onClose: () => void }) {
  return (
    <Dialog open={props.open} onClose={props.onClose} label={props.title}>
      <h3>{props.title}</h3>
      <p>{props.body}</p>
      <div className="acts">
        <button className="btn" onClick={props.onClose}>Cancel</button>
        <button className={`btn ${props.danger ? "danger" : "primary"}`} disabled={props.busy} onClick={props.onConfirm}>
          {props.busy ? "Working…" : props.confirm}
        </button>
      </div>
    </Dialog>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<{ text: string; n: number } | null>(null);
  const show = useCallback((text: string) => setMsg((m) => ({ text, n: (m?.n ?? 0) + 1 })), []);
  useEffect(() => {
    if (!msg) return;
    const t = setTimeout(() => setMsg(null), 2400);
    return () => clearTimeout(t);
  }, [msg]);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className={`toast${msg ? " show" : ""}`} role="status">{msg?.text}</div>
    </ToastContext.Provider>
  );
}

/** Stand-in for a page that is not built yet; lists the client calls it will use. */
export function PagePlaceholder({ title, lede, calls }: { title: string; lede: string; calls: string[] }) {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          <p>{lede}</p>
        </div>
      </div>
      <div className="card">
        <EmptyState icon="edit" title={`${title} is being built`}>
          Data comes from <span className="mono">{calls.join(", ")}</span> in <span className="mono">api/client.ts</span>.
        </EmptyState>
      </div>
    </>
  );
}
