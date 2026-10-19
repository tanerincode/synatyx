import { api, collectionFor } from "../api/client";
import type { MemoryItem } from "../api/types";
import { EmptyState, ErrorState, LayerPill, SkeletonBlock } from "../components/ui";
import { ago } from "../lib/format";
import { projectsOf } from "../lib/nav";
import { useScope } from "../lib/scope";
import { useApi } from "../lib/useApi";
import { similarityLabel } from "./hygiene/format";
import { ItemDrawer } from "./memories/ItemDrawer";

function Side({ item, onOpen }: { item: MemoryItem; onOpen: (id: string) => void }) {
  return (
    <div className="side" role="button" tabIndex={0} style={{ cursor: "pointer" }} onClick={() => onOpen(item.id)} onKeyDown={(e) => e.key === "Enter" && onOpen(item.id)}>
      <LayerPill layer={item.memory_layer} />
      <div style={{ marginTop: 6 }}>{item.content}</div>
      <div className="mono faint" style={{ fontSize: 11, marginTop: 6 }}>{ago(item.created_at)} · imp {item.importance.toFixed(2)}</div>
    </div>
  );
}

export default function HygienePage() {
  const { project, user, param, setParams } = useScope();
  const overview = useApi("shell:overview", () => api.overview());
  const projects = projectsOf(overview.data);
  const collection = param("collection") || collectionFor(project || projects[0] || "");
  const ready = !!project || !!param("collection") || projects.length > 0;
  const h = useApi(ready ? `hygiene:${collection}:${user}` : null, () => api.hygiene(collection, user || undefined));
  const itemId = param("item") || null;
  const open = (id: string): void => setParams({ item: id });

  const kpi = (label: string, value: number | undefined, note: string, color: string) => (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value" style={{ color }}>{value ?? "—"}</div>
      <div className="note">{note}</div>
    </div>
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Hygiene</h1>
          <p>Garbage collection, duplicates and staleness in <span className="mono">{collection || "…"}</span></p>
        </div>
      </div>
      {h.error ? (
        <div className="card"><ErrorState error={h.error} onRetry={h.reload} what="hygiene data" /></div>
      ) : (
        <>
          <div className="kpis" style={{ gridTemplateColumns: "repeat(4,minmax(0,1fr))" }}>
            {kpi("Expiring in 7 d", h.data?.expiring_7d, "unpinned, past decay", "var(--rose)")}
            {kpi("Deprecated", h.data?.deprecated, "excluded from retrieval", "var(--muted)")}
            {kpi("Pending hard delete", h.data?.pending_hard_delete, "next GC run", "var(--serious)")}
            {kpi("Protected", h.data?.protected, "pinned checkpoints", "var(--violet)")}
          </div>
          <div className="ov-grid">
            <div className="card">
              <div className="card-h">
                <h2>Duplicate suggestions</h2>
                <div className="grow" />
                <span className="tag" style={{ color: "var(--r-alternative_to)" }}>alternative_to</span>
              </div>
              {!h.data ? (
                <SkeletonBlock lines={5} />
              ) : h.data.duplicates.length === 0 ? (
                <EmptyState icon="dup" title="No duplicate suggestions">
                  Near-identical memories are linked with <span className="mono">alternative_to</span> when stored; pairs waiting for a decision show up here.
                </EmptyState>
              ) : (
                h.data.duplicates.map((d) => (
                  <div className="dup" key={`${d.a.id}-${d.b.id}`}>
                    <Side item={d.a} onOpen={open} />
                    <div className="mid"><div><b>{similarityLabel(d.similarity)}</b>similar</div></div>
                    <Side item={d.b} onOpen={open} />
                  </div>
                ))
              )}
            </div>
            <div className="card">
              <div className="card-h">
                <h2>Possibly stale</h2>
                <div className="grow" />
                <span className="tag warn">{h.data?.stale.length ?? 0}</span>
              </div>
              {!h.data ? (
                <SkeletonBlock lines={4} />
              ) : h.data.stale.length === 0 ? (
                <EmptyState icon="file" title="Nothing is stale">
                  Memories that reference files are flagged here when a file changes after the memory was stored.
                </EmptyState>
              ) : (
                h.data.stale.map((x) => (
                  <div className="rel-item" key={x.id} onClick={() => open(x.id)}>
                    <span className="dotc" style={{ background: `var(--${x.memory_layer.toLowerCase()})`, marginTop: 6 }} />
                    <span style={{ minWidth: 0 }}>
                      <span className="txt">{x.content}</span>
                      <span className="mono faint" style={{ fontSize: 11 }}>{ago(x.created_at)}</span>
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}
      <ItemDrawer itemId={itemId} collection={collection} onClose={() => setParams({ item: null })} onChanged={h.reload} />
    </>
  );
}
