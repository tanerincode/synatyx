import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from "react-force-graph-2d";
import { LAYERS, type GraphEdge, type GraphNode } from "../../api/types";
import { Icon, LayerPill } from "../../components/ui";
import { cssColor, neighbours, nodeLabel, nodeRadius, relationVar, type VisibleGraph } from "./model";

type N = NodeObject<GraphNode>;
type L = LinkObject<GraphNode, GraphEdge>;

/** The theme name on <html>, so the canvas redraws with new colours when it flips. */
function useThemeName(): string {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme ?? "dark");
  useEffect(() => {
    const mo = new MutationObserver(() => setTheme(document.documentElement.dataset.theme ?? "dark"));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);
  return theme;
}

function useSize(ref: React.RefObject<HTMLElement | null>): { w: number; h: number } {
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = (): void => setSize({ w: Math.floor(el.clientWidth), h: Math.floor(el.clientHeight) });
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return size;
}

/** Canvas colours from the CSS variables; `theme` is only the cache key, the variables differ per theme. */
function readColors(theme: string) {
  void theme;
  return {
    layer: Object.fromEntries(LAYERS.map((l) => [l, cssColor(`--${l.toLowerCase()}`)])) as Record<string, string>,
    rel: (t: string) => cssColor(relationVar(t)),
    ink: cssColor("--ink"),
    faint: cssColor("--faint"),
    cyan: cssColor("--cyan"),
  };
}

const endId = (end: string | number | N | undefined): string => (typeof end === "object" ? String(end.id) : String(end));

export interface GraphCanvasProps {
  graph: VisibleGraph;
  /** Node to centre on and ring (?focus=). */
  focusId: string | null;
  /** Node whose drawer is open (?item=). */
  openId: string | null;
  onOpen: (id: string) => void;
}

/** The force-directed canvas plus its legend, counts, zoom buttons and hover card. */
export function GraphCanvas({ graph, focusId, openId, onOpen }: GraphCanvasProps) {
  const shell = useRef<HTMLDivElement>(null);
  const fg = useRef<ForceGraphMethods<N, L> | undefined>(undefined);
  const { w, h } = useSize(shell);
  const theme = useThemeName();
  const [hover, setHover] = useState<{ node: N; x: number; y: number } | null>(null);
  const [ready, setReady] = useState(false);
  const [centred, setCentred] = useState<string | null>(null);
  const fitted = useRef(false);
  const mouse = useRef({ x: 0, y: 0 });

  // the library writes x/y onto what it is given, so it gets copies
  const data = useMemo(
    () => ({
      nodes: graph.nodes.map((n) => ({ ...n })) as N[],
      links: graph.edges.map((e) => ({ source: e.source, target: e.target, type: e.type })) as unknown as L[],
    }),
    [graph],
  );
  const adj = useMemo(() => neighbours(graph.edges), [graph.edges]);
  const colors = useMemo(() => readColors(theme), [theme]);

  const hoverId = hover ? String(hover.node.id) : null;
  const near = hoverId ? adj.get(hoverId) : undefined;
  const dimmed = (id: string): boolean => hoverId !== null && id !== hoverId && !near?.has(id);

  const paintNode = useCallback(
    (n: N, ctx: CanvasRenderingContext2D, scale: number) => {
      const id = String(n.id);
      const r = nodeRadius(n.importance);
      const x = n.x ?? 0;
      const y = n.y ?? 0;
      ctx.save();
      ctx.globalAlpha = (n.is_deprecated ? 0.35 : 1) * (dimmed(id) ? 0.25 : 1);
      ctx.beginPath();
      ctx.arc(x, y, r, 0, 2 * Math.PI);
      ctx.fillStyle = colors.layer[n.memory_layer] ?? colors.faint;
      ctx.fill();
      if (n.is_pinned) {
        ctx.lineWidth = 1.6 / scale;
        ctx.strokeStyle = colors.ink;
        ctx.beginPath();
        ctx.arc(x, y, r + 2 / scale, 0, 2 * Math.PI);
        ctx.stroke();
      }
      if (id === focusId || id === openId) {
        ctx.lineWidth = 1.4 / scale;
        ctx.strokeStyle = colors.cyan;
        ctx.setLineDash([3 / scale, 2 / scale]);
        ctx.beginPath();
        ctx.arc(x, y, r + 5 / scale, 0, 2 * Math.PI);
        ctx.stroke();
      }
      const labelled = (n.is_pinned && n.checkpoint_name) || id === hoverId || id === focusId;
      if (labelled && !dimmed(id)) {
        ctx.setLineDash([]);
        ctx.font = `${11 / scale}px ui-monospace, monospace`;
        ctx.fillStyle = colors.ink;
        ctx.textBaseline = "middle";
        ctx.fillText(nodeLabel(n), x + r + 4 / scale, y);
      }
      ctx.restore();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- dimmed() reads hoverId and near
    [colors, focusId, openId, hoverId, near],
  );

  const paintPointer = useCallback((n: N, colour: string, ctx: CanvasRenderingContext2D) => {
    ctx.fillStyle = colour;
    ctx.beginPath();
    ctx.arc(n.x ?? 0, n.y ?? 0, nodeRadius(n.importance) + 3, 0, 2 * Math.PI);
    ctx.fill();
  }, []);

  const linkTouchesHover = (l: L): boolean => hoverId !== null && (endId(l.source) === hoverId || endId(l.target) === hoverId);

  // Move the view to the focused node once per ?focus= value. `centred` flips after the
  // 400 ms camera move, so a reader (or a test) knows when the node is really at the centre.
  const centring = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const centreOnFocus = useCallback((): boolean => {
    const api = fg.current;
    const target = focusId ? data.nodes.find((n) => String(n.id) === focusId) : undefined;
    if (!api || !focusId || !target || target.x === undefined || target.y === undefined) return false;
    if (centring.current === focusId) return true;
    centring.current = focusId;
    api.centerAt(target.x, target.y, 400);
    api.zoom(4, 400);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCentred(focusId), 450);
    return true;
  }, [data, focusId]);

  // Fit once when the graph first settles; a focused node takes over from the fit.
  const onStop = useCallback(() => {
    setReady(true);
    if (!centreOnFocus() && !fitted.current) fg.current?.zoomToFit(400, 60);
    fitted.current = true;
  }, [centreOnFocus]);

  // A new ?focus= on an already settled graph moves the view without waiting for the engine.
  useEffect(() => {
    if (!focusId) {
      centring.current = null;
      return;
    }
    if (ready) centreOnFocus();
  }, [ready, focusId, centreOnFocus]);

  // Weaker repulsion keeps loose nodes from drifting far away and shrinking the fit.
  const mounted = w > 0 && h > 0;
  useEffect(() => {
    if (!mounted) return;
    fg.current?.d3Force("charge")?.strength(-35);
    fg.current?.d3Force("link")?.distance(28);
  }, [mounted, data]);

  const zoomBy = (k: number): void => {
    const api = fg.current;
    if (api) api.zoom(api.zoom() * k, 200);
  };
  const linkCount = graph.edges.length;

  return (
    <div
      ref={shell}
      className="card graph-shell"
      data-testid="graph-shell"
      data-ready={ready ? "1" : "0"}
      data-focused={focusId && centred === focusId ? focusId : ""}
      onMouseMove={(e) => {
        mouse.current = { x: e.clientX, y: e.clientY };
        setHover((cur) => (cur ? { ...cur, ...mouse.current } : cur));
      }}
    >
      {w > 0 && h > 0 && (
        <ForceGraph2D<GraphNode, GraphEdge>
          key={theme}
          ref={fg}
          width={w}
          height={h}
          graphData={data}
          backgroundColor="rgba(0,0,0,0)"
          cooldownTicks={120}
          warmupTicks={30}
          nodeRelSize={1}
          nodeCanvasObject={paintNode}
          nodeCanvasObjectMode={() => "replace"}
          nodePointerAreaPaint={paintPointer}
          linkColor={(l) => {
            const c = colors.rel(String((l as L & { type?: string }).type ?? "related_to"));
            return hoverId === null || linkTouchesHover(l) ? c : `${c}33`;
          }}
          linkWidth={(l) => (linkTouchesHover(l) ? 2 : 1)}
          linkDirectionalArrowLength={(l) => (linkTouchesHover(l) ? 4 : 0)}
          linkDirectionalArrowRelPos={1}
          onNodeHover={(n) => setHover(n ? { node: n, ...mouse.current } : null)}
          onNodeClick={(n) => onOpen(String(n.id))}
          onEngineStop={onStop}
        />
      )}
      <div className="graph-stats" data-testid="graph-stats">{graph.nodes.length} nodes · {linkCount} edges</div>
      <div className="graph-legend">
        <div className="lg-row">
          {LAYERS.map((l) => (
            <span key={l} className="key row"><span className="swatch" style={{ background: `var(--${l.toLowerCase()})` }} />{l}</span>
          ))}
          <span className="key row"><span className="swatch" style={{ background: "transparent", boxShadow: "0 0 0 2px var(--ink)" }} />pinned</span>
          <span className="key row"><span className="swatch" style={{ background: "var(--faint)" }} />deprecated</span>
          <span className="faint">size = importance</span>
        </div>
      </div>
      <div className="graph-zoom">
        <button className="btn sm" aria-label="Zoom in" onClick={() => zoomBy(1.4)}><Icon name="zoomin" /></button>
        <button className="btn sm" aria-label="Zoom out" onClick={() => zoomBy(1 / 1.4)}><Icon name="zoomout" /></button>
        <button className="btn sm" aria-label="Fit to screen" onClick={() => fg.current?.zoomToFit(400, 60)}><Icon name="fit" /></button>
      </div>
      {hover && (
        <div className="tooltip" role="tooltip" style={{ display: "block", left: hover.x + 14, top: hover.y + 14 }} data-testid="graph-tooltip">
          <LayerPill layer={hover.node.memory_layer} />
          <div style={{ margin: "6px 0", color: "var(--ink)" }}>{hover.node.label}</div>
          <div className="faint mono" style={{ fontSize: 11 }}>
            imp {hover.node.importance.toFixed(2)} · {adj.get(String(hover.node.id))?.size ?? 0} links · click to open
          </div>
        </div>
      )}
    </div>
  );
}
