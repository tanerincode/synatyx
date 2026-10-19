import type { IndexChunk, IndexGraph } from "../../api/types";

export interface TreeNode {
  /** Full path from the index root, e.g. "src/core" or "src/core/gc.py". */
  path: string;
  /** Last path segment. */
  name: string;
  kind: "dir" | "file";
  /** Chunk count; for a directory the sum over every file below it. */
  chunks: number;
  language: string | null;
  children: TreeNode[];
}

export interface TreeRow {
  node: TreeNode;
  depth: number;
  open: boolean;
}

/** Directories start open this deep until the user toggles them. */
export const DEFAULT_OPEN_DEPTH = 2;

const baseName = (path: string): string => path.slice(path.lastIndexOf("/") + 1);

/**
 * Turn the server's index graph (project root, dir hubs, file leaves, "in"
 * edges; every label is a full path) into a tree. Directories come before
 * files, each group sorted by name; directory chunk counts are rolled up.
 */
export function buildTree(graph: IndexGraph): TreeNode[] {
  const byId = new Map<string, TreeNode>();
  for (const n of graph.nodes) {
    if (n.kind === "project") continue;
    byId.set(n.id, {
      path: n.label,
      name: baseName(n.label),
      kind: n.kind === "dir" ? "dir" : "file",
      chunks: n.kind === "file" ? n.chunks : 0,
      language: n.language,
      children: [],
    });
  }
  const roots: TreeNode[] = [];
  const attached = new Set<string>();
  for (const e of graph.edges) {
    const child = byId.get(e.source);
    if (!child || attached.has(e.source)) continue;
    attached.add(e.source);
    const parent = byId.get(e.target);
    (parent ? parent.children : roots).push(child);
  }
  // nodes with no edge at all are shown at the top level rather than dropped
  for (const [id, node] of byId) if (!attached.has(id)) roots.push(node);

  const order = (a: TreeNode, b: TreeNode): number =>
    a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === "dir" ? -1 : 1;
  const settle = (nodes: TreeNode[]): number => {
    nodes.sort(order);
    let total = 0;
    for (const n of nodes) {
      if (n.kind === "dir") n.chunks = settle(n.children);
      total += n.chunks;
    }
    return total;
  };
  settle(roots);
  return roots;
}

/** The visible rows, depth first. `overrides` holds the user's open/close choices by path. */
export function visibleRows(roots: TreeNode[], overrides: ReadonlyMap<string, boolean>): TreeRow[] {
  const rows: TreeRow[] = [];
  const walk = (nodes: TreeNode[], depth: number): void => {
    for (const node of nodes) {
      const open = node.kind === "dir" && (overrides.get(node.path) ?? depth < DEFAULT_OPEN_DEPTH);
      rows.push({ node, depth, open });
      if (open) walk(node.children, depth + 1);
    }
  };
  walk(roots, 0);
  return rows;
}

/** Number of files under a node (1 for a file). */
export function fileCount(node: TreeNode): number {
  return node.kind === "file" ? 1 : node.children.reduce((s, c) => s + fileCount(c), 0);
}

const LANGUAGE_COLORS: Record<string, string> = {
  python: "#60a5fa",
  markdown: "#a78bfa",
  typescript: "#67e8f9",
  javascript: "#fbbf24",
  yaml: "#fbbf24",
  json: "#34d399",
  html: "#fb923c",
  dart: "#2dd4bf",
  css: "#f472b6",
  shell: "#34d399",
};
export const languageColor = (language: string | null | undefined): string =>
  LANGUAGE_COLORS[(language ?? "").toLowerCase()] ?? "var(--muted)";

export interface CodeLine {
  no: number | null;
  text: string;
}

/** A chunk preview as numbered lines (numbers are blank when the server sent no line range). */
export function chunkLines(chunk: Pick<IndexChunk, "preview" | "line_start">): CodeLine[] {
  const start = chunk.line_start;
  return chunk.preview.split("\n").map((text, i) => ({ no: start === null ? null : start + i, text }));
}

/** "L43–50", "L43" or "" for a chunk's line range. */
export function lineRange(chunk: Pick<IndexChunk, "line_start" | "line_end">): string {
  const { line_start: a, line_end: b } = chunk;
  if (a === null) return "";
  return b === null || b === a ? `L${a}` : `L${a}–${b}`;
}
