import { useEffect, useMemo, useState } from "react";
import { api, collectionFor } from "../api/client";
import { LAYERS, ORIGINS } from "../api/types";
import { EmptyState, ErrorState, Icon, LayerPill, ProjTag, SkeletonRows } from "../components/ui";
import { ago, fmt, short } from "../lib/format";
import { projectsOf } from "../lib/nav";
import { useScope } from "../lib/scope";
import { useApi } from "../lib/useApi";
import { ItemDrawer } from "./memories/ItemDrawer";
import { DATE_PRESETS, hasActiveFilters, nextSort, PAGE_SIZES, pageInfo, parseFilters, queryKey, toQuery } from "./memories/query";

const TYPES = ["attempt", "decision", "checkpoint", "skill"];

export default function MemoriesPage() {
  const { project, user, param, setParams } = useScope();
  const overview = useApi("shell:overview", () => api.overview());
  const projects = projectsOf(overview.data);
  // Without a project the first one is shown, like the prototype. ?collection= picks the global one.
  const collection = param("collection") || collectionFor(project || projects[0] || "");
  const ready = !!project || !!param("collection") || projects.length > 0;
  const f = parseFilters(param);
  const query = useMemo(() => toQuery(f, collection, user), [f.q, f.layer, f.origin, f.type, f.range, f.stale, f.deprecated, f.sort, f.dir, f.page, f.size, collection, user]); // eslint-disable-line react-hooks/exhaustive-deps
  const list = useApi(ready ? queryKey(query) : null, () => api.itemsPage(query));
  const itemId = param("item") || null;
  const [focus, setFocus] = useState(0);

  // q is typed locally and pushed to the URL after a pause so each keystroke is not a request.
  const [qText, setQText] = useState(f.q);
  const [seenQ, setSeenQ] = useState(f.q);
  if (seenQ !== f.q) {
    setSeenQ(f.q);
    setQText(f.q);
  }
  useEffect(() => {
    if (qText === f.q) return;
    const t = setTimeout(() => setParams({ q: qText, page: null }), 300);
    return () => clearTimeout(t);
  }, [qText, f.q, setParams]);

  const rows = useMemo(() => list.data?.items ?? [], [list.data]);
  const total = list.data?.total ?? 0;
  const { pages, from, to } = pageInfo(total, f.page, f.size);

  useEffect(() => {
    const h = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "j") setFocus((n) => Math.min(rows.length - 1, n + 1));
      else if (e.key === "k") setFocus((n) => Math.max(0, n - 1));
      else if (e.key === "Enter" && !itemId && rows[focus]) setParams({ item: rows[focus].id });
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [rows, focus, itemId, setParams]);

  const set = (patch: Record<string, string | null>): void => {
    setFocus(0);
    setParams({ page: null, ...patch });
  };
  const sortBy = (col: "created_at" | "importance"): void => {
    const n = nextSort(f, col);
    set({ sort: n.sort === "created_at" ? null : n.sort, dir: n.dir === "desc" ? null : n.dir });
  };
  const arrow = (col: string): string => (f.sort === col ? (f.dir === "desc" ? "↓" : "↑") : "");
  const clear = (): void => {
    setQText("");
    set({ q: null, layer: null, origin: null, type: null, range: null, stale: null, dep: null });
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Memories</h1>
          <p>
            {list.data ? `${fmt(total)} items` : "Loading…"} in <span className="mono">{collection || "…"}</span> · click a row for detail ·{" "}
            <span className="mono">j</span>/<span className="mono">k</span> to move, <span className="mono">↵</span> to open, <span className="mono">esc</span> to close
          </p>
        </div>
      </div>
      <div className="toolbar">
        <span className="input-icon">
          <Icon name="search" />
          <input className="input" aria-label="Search memories" placeholder="Search content or id…" style={{ width: 260 }} value={qText} onChange={(e) => setQText(e.target.value)} />
        </span>
        <span className="chipset" role="group" aria-label="Layer">
          {LAYERS.map((l) => (
            <button key={l} className={`chip-toggle${f.layer === l ? " on" : ""}`} aria-pressed={f.layer === l} onClick={() => set({ layer: f.layer === l ? null : l })}>
              <span className="dotc" style={{ background: `var(--${l.toLowerCase()})` }} />
              {l}
            </button>
          ))}
        </span>
        <select className="select" aria-label="Origin" value={f.origin} onChange={(e) => set({ origin: e.target.value || null })}>
          <option value="">Any origin</option>
          {ORIGINS.map((o) => <option key={o}>{o}</option>)}
        </select>
        <select className="select" aria-label="Type" value={f.type} onChange={(e) => set({ type: e.target.value || null })}>
          <option value="">Any type</option>
          {TYPES.map((t) => <option key={t}>{t}</option>)}
        </select>
        <select className="select hide-md" aria-label="Date" value={f.range} onChange={(e) => set({ range: e.target.value || null })}>
          {DATE_PRESETS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
        </select>
        <label className="check"><input type="checkbox" checked={f.stale} onChange={(e) => set({ stale: e.target.checked ? "1" : null })} />Stale only</label>
        <label className="check"><input type="checkbox" checked={f.deprecated} onChange={(e) => set({ dep: e.target.checked ? "1" : null })} />Include deprecated</label>
      </div>
      {list.data?.capped && (
        <p className="small" role="status" style={{ color: "var(--warn)", marginBottom: 10 }}>
          The server scanned its 5000-item limit, so results may be incomplete. Narrow the filters to be sure.
        </p>
      )}
      <div className="card flush">
        <div className="table-wrap">
          <table id="mem-table">
            <thead>
              <tr>
                <th>Layer</th>
                <th>Content</th>
                <th>Origin</th>
                <th className="sortable" aria-sort={f.sort === "importance" ? (f.dir === "desc" ? "descending" : "ascending") : "none"} onClick={() => sortBy("importance")}>
                  Importance <span className="arr">{arrow("importance")}</span>
                </th>
                <th className="hide-md">Project</th>
                <th className="hide-md">User</th>
                <th className="sortable right" aria-sort={f.sort === "created_at" ? (f.dir === "desc" ? "descending" : "ascending") : "none"} onClick={() => sortBy("created_at")}>
                  Created <span className="arr">{arrow("created_at")}</span>
                </th>
              </tr>
            </thead>
            {list.error ? (
              <tbody><tr><td colSpan={7}><ErrorState error={list.error} onRetry={list.reload} what="memories" /></td></tr></tbody>
            ) : !list.data ? (
              <SkeletonRows rows={8} cols={7} />
            ) : rows.length === 0 ? (
              <tbody>
                <tr>
                  <td colSpan={7}>
                    <EmptyState title="No memories match these filters" action={hasActiveFilters(f) ? <button className="btn" onClick={clear}>Clear filters</button> : undefined}>
                      Agents fill this table with <span className="mono">context_store</span>. Clear a filter, include deprecated items, or pick another project.
                    </EmptyState>
                  </td>
                </tr>
              </tbody>
            ) : (
              <tbody>
                {rows.map((x, i) => (
                  <tr key={x.id} data-id={x.id} className={`${x.is_deprecated ? "dep" : ""} ${i === focus ? "focus" : ""}`} onClick={() => { setFocus(i); setParams({ item: x.id }); }}>
                    <td><LayerPill layer={x.memory_layer} /></td>
                    <td>
                      <div className="clamp2">
                        {x.is_pinned && <span title="pinned" style={{ color: "var(--violet)" }}><Icon name="pin" size={12} /> </span>}
                        {x.content}
                      </div>
                      <div className="row" style={{ marginTop: 5, gap: 6 }}>
                        {x.type === "attempt" && <span className="tag danger">attempt</span>}
                        {x.checkpoint_name && <span className="tag" style={{ color: "var(--violet)" }}>◆ {x.checkpoint_name}</span>}
                        {x.possibly_stale && <span className="tag warn">possibly stale</span>}
                        {x.is_deprecated && <span className="tag ghost">deprecated</span>}
                        <span className="faint mono" style={{ fontSize: 11 }}>{short(x.id)}</span>
                      </div>
                    </td>
                    <td><span className="mono small muted">{x.origin ?? "—"}</span></td>
                    <td>
                      <span className="ibar"><b style={{ width: `${x.importance * 100}%` }} /></span> <span className="num muted">{x.importance.toFixed(2)}</span>
                    </td>
                    <td className="hide-md"><ProjTag project={x.project} /></td>
                    <td className="hide-md"><span className="mono small">{x.user_id ?? "—"}</span></td>
                    <td className="right"><span className="num muted" title={x.created_at ?? ""}>{ago(x.created_at)}</span></td>
                  </tr>
                ))}
              </tbody>
            )}
          </table>
        </div>
        <div className="pager">
          <span>Showing {from}–{to} of {fmt(total)}</span>
          <div className="grow" />
          <select className="select" aria-label="Page size" style={{ padding: "3px 24px 3px 9px", fontSize: 12 }} value={f.size} onChange={(e) => set({ size: e.target.value === String(PAGE_SIZES[0]) ? null : e.target.value })}>
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n} / page</option>)}
          </select>
          <button className="btn sm" disabled={f.page === 0} onClick={() => { setFocus(0); setParams({ page: f.page === 1 ? null : String(f.page) }); }}>‹ Prev</button>
          <span className="mono">{Math.min(f.page, pages - 1) + 1} / {pages}</span>
          <button className="btn sm" disabled={f.page >= pages - 1} onClick={() => { setFocus(0); setParams({ page: String(f.page + 2) }); }}>Next ›</button>
        </div>
      </div>
      <ItemDrawer itemId={itemId} collection={collection} onClose={() => setParams({ item: null })} onChanged={list.reload} />
    </>
  );
}
