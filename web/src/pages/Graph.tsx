import { useMemo, useState } from "react";
import { api, collectionFor } from "../api/client";
import { LAYER_NAMES, LAYERS, type Layer } from "../api/types";
import { EmptyState, ErrorState, Icon, SkeletonBlock } from "../components/ui";
import { projectsOf } from "../lib/nav";
import { useScope } from "../lib/scope";
import { useApi } from "../lib/useApi";
import { GraphCanvas } from "./graph/GraphCanvas";
import { applyFilters, findNodes, relationTypes, relationVar } from "./graph/model";
import { ItemDrawer } from "./memories/ItemDrawer";

const NODE_LIMIT = 300;

export default function GraphPage() {
  const { project, user, param, setParams } = useScope();
  const overview = useApi("shell:overview", () => api.overview());
  const projects = projectsOf(overview.data);
  // Like Memories: without a project the first one is shown; ?collection= picks the global L4 one.
  const collection = param("collection") || collectionFor(project || projects[0] || "");
  const ready = !!project || !!param("collection") || projects.length > 0;
  const graph = useApi(ready ? `graph:${collection}:${user}` : null, () => api.graph(collection, { user: user || undefined, limit: NODE_LIMIT }));

  const [layers, setLayers] = useState<ReadonlySet<Layer>>(new Set(LAYERS));
  const [off, setOff] = useState<ReadonlySet<string>>(new Set()); // relation types switched off
  const [connectedOnly, setConnectedOnly] = useState(false);
  const [showDeprecated, setShowDeprecated] = useState(true);
  const [find, setFind] = useState("");
  const [noMatch, setNoMatch] = useState(false);

  const focusId = param("focus") || null;
  const itemId = param("item") || null;
  const data = graph.data;
  const types = useMemo(() => (data ? relationTypes(data) : []), [data]);
  const visible = useMemo(
    () =>
      data
        ? applyFilters(data, {
            layers,
            relTypes: off.size === 0 ? null : new Set(types.filter((t) => !off.has(t))),
            connectedOnly,
            showDeprecated,
            keep: focusId,
          })
        : null,
    [data, layers, off, types, connectedOnly, showDeprecated, focusId],
  );
  const focusMissing = !!focusId && !!data && !data.nodes.some((n) => n.id === focusId);

  const toggleLayer = (l: Layer): void =>
    setLayers((cur) => {
      const next = new Set(cur);
      if (next.has(l)) next.delete(l);
      else next.add(l);
      return next;
    });
  const toggleType = (t: string): void =>
    setOff((cur) => {
      const next = new Set(cur);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });

  const runFind = (): void => {
    if (!data || !find.trim()) return;
    const hit = findNodes(data.nodes, find)[0];
    setNoMatch(!hit);
    if (hit) setParams({ focus: hit.id });
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Graph</h1>
          <p>
            Memories of <b>{collection.replace(/^ctx_/, "") || "…"}</b> · node colour = layer, size = importance, ring = pinned, faded = deprecated
          </p>
        </div>
      </div>
      {!ready ? (
        <div className="card"><SkeletonBlock lines={6} /></div>
      ) : graph.error ? (
        <div className="card"><ErrorState error={graph.error} onRetry={graph.reload} what="the graph" /></div>
      ) : !data || !visible ? (
        <div className="card graph-shell"><div style={{ padding: 18 }}><SkeletonBlock lines={8} /></div></div>
      ) : data.nodes.length === 0 ? (
        <div className="card">
          <EmptyState icon="graph" title="No memories to draw">
            {user ? <>User <b>{user}</b> has no memories in this collection.</> : "Memories stored in this collection, and the relations between them, are drawn here."}
          </EmptyState>
        </div>
      ) : (
        <>
          <div className="toolbar" aria-label="Graph filters">
            <div className="chipset" role="group" aria-label="Layers">
              {LAYERS.map((l) => (
                <button key={l} className={`chip-toggle${layers.has(l) ? " on" : ""}`} aria-pressed={layers.has(l)} title={LAYER_NAMES[l]} onClick={() => toggleLayer(l)}>
                  <span className="swatch" style={{ background: `var(--${l.toLowerCase()})` }} />{l}
                </button>
              ))}
            </div>
            <span className="faint">|</span>
            <div className="chipset" role="group" aria-label="Relation types" style={{ flexWrap: "wrap" }}>
              {types.length === 0 && <span className="muted small">no relations yet</span>}
              {types.map((t) => (
                <button key={t} className={`chip-toggle${off.has(t) ? "" : " on"}`} aria-pressed={!off.has(t)} onClick={() => toggleType(t)}>
                  <span className="edge-swatch" style={{ background: `var(${relationVar(t)})` }} />{t}
                </button>
              ))}
            </div>
            <div className="grow" />
            <label className="check"><input type="checkbox" checked={connectedOnly} onChange={(e) => setConnectedOnly(e.target.checked)} /> Connected only</label>
          </div>
          <div className="toolbar">
            <label className="check"><input type="checkbox" checked={showDeprecated} onChange={(e) => setShowDeprecated(e.target.checked)} /> Deprecated</label>
            <label className="input-icon">
              <Icon name="search" />
              <input
                className="input"
                style={{ width: 240 }}
                placeholder="Find node…"
                aria-label="Find node"
                value={find}
                onChange={(e) => { setFind(e.target.value); setNoMatch(false); }}
                onKeyDown={(e) => e.key === "Enter" && runFind()}
              />
            </label>
            {noMatch && <span className="small" style={{ color: "var(--warn)" }} role="status">No node matches “{find}”.</span>}
            {focusMissing && (
              <span className="small" style={{ color: "var(--warn)" }} role="status">
                That memory isn't among the {NODE_LIMIT} most recent shown here.
              </span>
            )}
            {focusId && <button className="btn sm" onClick={() => setParams({ focus: null })}><Icon name="x" /> Clear focus</button>}
          </div>
          <GraphCanvas graph={visible} focusId={focusMissing ? null : focusId} openId={itemId} onOpen={(id) => setParams({ item: id })} />
        </>
      )}
      <ItemDrawer itemId={itemId} collection={collection} onClose={() => setParams({ item: null })} onChanged={graph.reload} />
    </>
  );
}
