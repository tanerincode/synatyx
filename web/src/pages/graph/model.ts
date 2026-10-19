import { RELATION_TYPES, type GraphEdge, type GraphNode, type GraphResponse, type Layer } from "../../api/types";

export interface GraphFilters {
  /** Layers shown. */
  layers: ReadonlySet<Layer>;
  /** Relation types shown; null means all of them. */
  relTypes: ReadonlySet<string> | null;
  connectedOnly: boolean;
  showDeprecated: boolean;
  /** A node that stays visible whatever the filters say (the one ?focus= points at). */
  keep?: string | null;
}

export interface VisibleGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/**
 * Apply the toolbar filters. An edge needs both endpoints visible and its type
 * enabled; "connected only" then drops nodes left with no edge, except `keep`.
 */
export function applyFilters(graph: GraphResponse, f: GraphFilters): VisibleGraph {
  const kept = (n: GraphNode): boolean => n.id === f.keep;
  const shown = new Set<string>();
  for (const n of graph.nodes) {
    if (kept(n) || (f.layers.has(n.memory_layer) && (f.showDeprecated || !n.is_deprecated))) shown.add(n.id);
  }
  const edges = graph.edges.filter(
    (e) => shown.has(e.source) && shown.has(e.target) && (f.relTypes === null || f.relTypes.has(e.type)),
  );
  let nodes = graph.nodes.filter((n) => shown.has(n.id));
  if (f.connectedOnly) {
    const linked = new Set<string>();
    for (const e of edges) {
      linked.add(e.source);
      linked.add(e.target);
    }
    nodes = nodes.filter((n) => linked.has(n.id) || kept(n));
  }
  return { nodes, edges };
}

/** Relation types present in the data: the known ones in their usual order, then any custom ones. */
export function relationTypes(graph: GraphResponse): string[] {
  const present = new Set(graph.edges.map((e) => e.type));
  const known: string[] = RELATION_TYPES.filter((t) => present.has(t));
  const custom = [...present].filter((t) => !known.includes(t)).sort();
  return [...known, ...custom];
}

/** node id → ids it shares an edge with, either direction. */
export function neighbours(edges: readonly GraphEdge[]): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>();
  const add = (a: string, b: string): void => {
    const set = out.get(a);
    if (set) set.add(b);
    else out.set(a, new Set([b]));
  };
  for (const e of edges) {
    add(e.source, e.target);
    add(e.target, e.source);
  }
  return out;
}

/** Dot radius in graph units: 3 for importance 0 up to 9 for importance 1. */
export function nodeRadius(importance: number): number {
  const clamped = Math.min(1, Math.max(0, Number.isFinite(importance) ? importance : 0));
  return 3 + clamped * 6;
}

/** What a node is called on the canvas: its checkpoint name, else the start of its text. */
export function nodeLabel(n: Pick<GraphNode, "checkpoint_name" | "label">, max = 36): string {
  const text = n.checkpoint_name || n.label.replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Nodes whose text or checkpoint name contains `query`; exact checkpoint-name matches first. */
export function findNodes(nodes: readonly GraphNode[], query: string): GraphNode[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const rank = (n: GraphNode): number => {
    if ((n.checkpoint_name ?? "").toLowerCase() === q) return 0;
    if ((n.checkpoint_name ?? "").toLowerCase().includes(q)) return 1;
    return n.label.toLowerCase().includes(q) ? 2 : 3;
  };
  return nodes
    .filter((n) => rank(n) < 3)
    .sort((a, b) => rank(a) - rank(b) || b.importance - a.importance);
}

/** CSS variable holding a relation type's colour; custom types fall back to related_to's. */
export function relationVar(type: string): string {
  return (RELATION_TYPES as readonly string[]).includes(type) ? `--r-${type}` : "--r-related_to";
}

/** Resolve a CSS custom property to a colour string the canvas can use. */
export function cssColor(name: string, el: Element = document.documentElement): string {
  return getComputedStyle(el).getPropertyValue(name).trim() || "#94a3b8";
}
