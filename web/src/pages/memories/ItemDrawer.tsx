// Shared detail drawer for one memory. Used by Memories, Hygiene and Graph.
// Fetches the item itself; pin/unpin/restore/deprecate update the view
// optimistically and roll back (with a toast) when the server refuses.
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import type { ItemAction, ItemDetail, Layer } from "../../api/types";
import { Dialog, ErrorState, Icon, LayerPill, ProjTag, SkeletonBlock } from "../../components/ui";
import { ago, short } from "../../lib/format";
import { Markdown } from "../../lib/markdown";
import { scoped } from "../../lib/scope";
import { useToast } from "../../lib/toast";
import { DONE, groupRelated, optimisticFor, type Optimistic } from "./itemActions";
import { useApi } from "../../lib/useApi";

export interface ItemDrawerProps {
  /** Item to show; null keeps the drawer closed. */
  itemId: string | null;
  /** Qdrant collection the item lives in, e.g. ctx_synatyx. */
  collection: string;
  onClose: () => void;
  /** Called after a successful pin/unpin/deprecate/restore so the caller can reload its list. */
  onChanged?: () => void;
}

export function ItemDrawer({ itemId, collection, onClose, onChanged }: ItemDrawerProps) {
  const toast = useToast();
  const [sp] = useSearchParams();
  // Following a related edge opens that item; it resets when the caller picks another one.
  const [nav, setNav] = useState<{ from: string | null; id: string } | null>(null);
  const id = itemId && nav && nav.from === itemId ? nav.id : itemId;
  const { data, error, reload } = useApi(id ? `item:${collection}:${id}` : null, () => api.item(id as string, collection));
  const [fresh, setFresh] = useState<ItemDetail | null>(null);
  const [opt, setOpt] = useState<(Optimistic & { id: string }) | null>(null);
  const [deprecating, setDeprecating] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!itemId) return;
    const h = (e: KeyboardEvent): void => {
      if (e.key === "Escape" && !deprecating) onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [itemId, deprecating, onClose]);

  const base = fresh && fresh.id === id ? fresh : data;
  const item: ItemDetail | undefined = base && opt && opt.id === base.id ? { ...base, ...opt } : base;

  async function run(action: ItemAction, why?: string): Promise<void> {
    if (!item || !id) return;
    setBusy(true);
    setOpt({ id, ...optimisticFor(action) });
    try {
      const updated = await api.itemAction(id, action, collection, why);
      setFresh(updated);
      toast(`${DONE[action]} ${short(id)}`);
      onChanged?.();
    } catch (e) {
      toast(`Couldn't ${action} ${short(id)}: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setOpt(null);
      setBusy(false);
    }
  }

  const open = itemId !== null;
  const newer = item?.related.find((r) => r.type === "supersedes" && r.direction === "in");
  const staleFiles = new Set(item?.stale_files ?? []);

  return (
    <>
      <div className={`scrim${open ? " open" : ""}`} onClick={onClose} />
      <aside className={`drawer${open ? " open" : ""}`} role="dialog" aria-label="Memory detail" aria-hidden={!open}>
        {open && (
          <>
            <div className="drawer-h">
              <div style={{ flex: 1, minWidth: 0 }}>
                {item && (
                  <div className="row wrap">
                    <LayerPill layer={item.memory_layer} />
                    <ProjTag project={item.project} />
                    {item.is_pinned && <span className="tag" style={{ color: "var(--violet)" }}>◆ {item.checkpoint_name || "pinned"}</span>}
                    {item.is_deprecated && <span className="tag ghost">deprecated</span>}
                    {item.possibly_stale && <span className="tag warn">possibly stale</span>}
                    {item.type && <span className={`tag${item.type === "attempt" ? " danger" : ""}`}>{item.type}</span>}
                  </div>
                )}
                <div className="mono faint small" style={{ marginTop: 8 }}>{id}</div>
              </div>
              <button className="icon-btn" onClick={onClose} title="Close (Esc)" aria-label="Close">
                <Icon name="x" />
              </button>
            </div>
            <div className="drawer-b">
              {error && <ErrorState error={error} onRetry={reload} what="this memory" />}
              {!item && !error && <SkeletonBlock lines={6} />}
              {item && (
                <>
                  {newer && (
                    <div className="card" style={{ padding: "10px 12px", marginBottom: 14, borderColor: "color-mix(in srgb,var(--r-supersedes) 45%,transparent)" }}>
                      <span className="small">Superseded by </span>
                      <a className="small" style={{ color: "var(--r-supersedes)", cursor: "pointer" }} onClick={() => itemId && setNav({ from: itemId, id: newer.id })}>
                        {newer.label.slice(0, 70)}…
                      </a>
                    </div>
                  )}
                  {item.is_deprecated && item.deprecated_reason && (
                    <p className="small muted" style={{ marginBottom: 12 }}>Deprecated: {item.deprecated_reason}</p>
                  )}
                  <Markdown source={item.content} />
                  <div className="section-t">Metadata</div>
                  <dl className="kv">
                    <dt>Importance</dt>
                    <dd>
                      <span className="ibar" style={{ width: 90 }}><b style={{ width: `${item.importance * 100}%` }} /></span> <span className="num">{item.importance.toFixed(2)}</span>
                    </dd>
                    <dt>Confidence</dt>
                    <dd className="num">{item.confidence === null ? "—" : item.confidence.toFixed(2)}</dd>
                    <dt>Origin</dt>
                    <dd className="mono">{item.origin ?? "—"}</dd>
                    <dt>Fact type</dt>
                    <dd className="mono">{item.fact_type ?? "—"}</dd>
                    <dt>User</dt>
                    <dd className="mono">{item.user_id ?? "—"}</dd>
                    <dt>Session</dt>
                    <dd className="mono">{item.session_id ?? "—"}</dd>
                    <dt>Created</dt>
                    <dd>
                      <span className="mono">{item.created_at?.slice(0, 16).replace("T", " ") ?? "—"}</span> <span className="muted">· {ago(item.created_at)}</span>
                    </dd>
                  </dl>
                  {item.files.length > 0 && (
                    <>
                      <div className="section-t">Linked files</div>
                      {item.files.map((f) => (
                        <div className="file" key={f}>
                          <Icon name="file" size={14} />
                          {f}
                          {staleFiles.has(f) ? <span className="tag warn" style={{ marginLeft: "auto" }}>changed since store</span> : <span className="tag ghost" style={{ marginLeft: "auto" }}>hash ok</span>}
                        </div>
                      ))}
                    </>
                  )}
                  <div className="section-t">Related <span className="faint">{item.related.length}</span></div>
                  {item.related.length === 0 && (
                    <div className="muted small">No relations. Agents link memories with <span className="mono">context_relate</span>.</div>
                  )}
                  {groupRelated(item.related).map(([type, rs]) => (
                    <div key={type}>
                      <div className="row small" style={{ margin: "8px 0 2px" }}>
                        <span className="edge-swatch" style={{ background: `var(--r-${type})` }} />
                        <span className="mono" style={{ color: `var(--r-${type})` }}>{type}</span>
                        <span className="faint">{rs.length}</span>
                      </div>
                      {rs.map((r) => (
                        <div className="rel-item" key={`${r.id}-${r.direction}`} onClick={() => itemId && setNav({ from: itemId, id: r.id })}>
                          <span className="faint mono small">{r.direction === "out" ? "→" : "←"}</span>
                          {r.memory_layer && <span className="dotc" style={{ background: `var(--${(r.memory_layer as Layer).toLowerCase()})`, marginTop: 6 }} />}
                          <span className="txt">{r.label || short(r.id)}</span>
                        </div>
                      ))}
                    </div>
                  ))}
                </>
              )}
            </div>
            <div className="drawer-f">
              {item && (
                <>
                  <button className="btn" disabled={busy} onClick={() => void run(item.is_pinned ? "unpin" : "pin")}>
                    <Icon name="pin" />
                    {item.is_pinned ? "Unpin" : "Pin"}
                  </button>
                  {item.is_deprecated ? (
                    <button className="btn" disabled={busy} onClick={() => void run("restore")}>
                      <Icon name="restore" />
                      Restore
                    </button>
                  ) : (
                    <button className="btn danger" disabled={busy} onClick={() => { setReason(""); setDeprecating(true); }}>
                      <Icon name="trash" />
                      Deprecate
                    </button>
                  )}
                  <button className="btn" onClick={() => { void navigator.clipboard?.writeText(item.id).catch(() => {}); toast(`Copied ${item.id}`); }}>
                    <Icon name="copy" />
                    Copy id
                  </button>
                  <div style={{ flex: 1 }} />
                  <Link className="btn" to={scoped("/graph", sp, { focus: item.id })}>
                    <Icon name="graph" />
                    Show in graph
                  </Link>
                </>
              )}
            </div>
          </>
        )}
      </aside>
      <Dialog open={deprecating} onClose={() => setDeprecating(false)} label="Deprecate memory">
        <h3>Deprecate this memory?</h3>
        <p>It disappears from retrieval. GC hard-deletes it after 30 days unless you restore it.</p>
        <div className="field" style={{ margin: "10px 0 14px" }}>
          <label htmlFor="dep-reason">Reason (optional)</label>
          <textarea id="dep-reason" className="input" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why is it outdated?" autoFocus />
        </div>
        <div className="acts">
          <button className="btn" onClick={() => setDeprecating(false)}>Cancel</button>
          <button
            className="btn danger"
            disabled={busy}
            onClick={() => {
              setDeprecating(false);
              void run("deprecate", reason.trim() || undefined);
            }}
          >
            Deprecate
          </button>
        </div>
      </Dialog>
    </>
  );
}
