import { describe, expect, it } from "vitest";
import type { IndexGraph } from "../../api/types";
import { buildTree, chunkLines, fileCount, languageColor, lineRange, visibleRows } from "./tree";

const graph: IndexGraph = {
  truncated: false,
  nodes: [
    { id: "root", label: "synatyx", kind: "project", language: null, chunks: 0 },
    { id: "d:src", label: "src", kind: "dir", language: null, chunks: 0 },
    { id: "d:src/core", label: "src/core", kind: "dir", language: null, chunks: 0 },
    { id: "f:src/core/gc.py", label: "src/core/gc.py", kind: "file", language: "python", chunks: 3 },
    { id: "f:src/core/a.py", label: "src/core/a.py", kind: "file", language: "python", chunks: 2 },
    { id: "f:src/main.py", label: "src/main.py", kind: "file", language: "python", chunks: 4 },
    { id: "f:README.md", label: "README.md", kind: "file", language: "markdown", chunks: 1 },
  ],
  edges: [
    { source: "d:src", target: "root", type: "in" },
    { source: "d:src/core", target: "d:src", type: "in" },
    { source: "f:src/core/gc.py", target: "d:src/core", type: "in" },
    { source: "f:src/core/a.py", target: "d:src/core", type: "in" },
    { source: "f:src/main.py", target: "d:src", type: "in" },
    { source: "f:README.md", target: "root", type: "in" },
  ],
};

describe("buildTree", () => {
  it("nests dirs and files under the root, dirs first, sorted by name", () => {
    const roots = buildTree(graph);
    expect(roots.map((n) => n.path)).toEqual(["src", "README.md"]);
    const src = roots[0];
    expect(src?.children.map((n) => n.path)).toEqual(["src/core", "src/main.py"]);
    expect(src?.children[0]?.children.map((n) => n.name)).toEqual(["a.py", "gc.py"]);
  });

  it("rolls chunk counts up into directories", () => {
    const [src] = buildTree(graph);
    expect(src?.chunks).toBe(9);
    expect(src?.children[0]?.chunks).toBe(5);
  });

  it("keeps a node that has no edge at the top level", () => {
    const roots = buildTree({ ...graph, edges: [], nodes: [graph.nodes[3]!] });
    expect(roots.map((n) => n.path)).toEqual(["src/core/gc.py"]);
  });

  it("is empty for a graph with only a root", () => {
    expect(buildTree({ nodes: [graph.nodes[0]!], edges: [], truncated: false })).toEqual([]);
  });
});

describe("visibleRows", () => {
  it("opens the first two levels by default", () => {
    const rows = visibleRows(buildTree(graph), new Map());
    expect(rows.map((r) => [r.node.path, r.depth, r.open])).toEqual([
      ["src", 0, true],
      ["src/core", 1, true],
      ["src/core/a.py", 2, false],
      ["src/core/gc.py", 2, false],
      ["src/main.py", 1, false],
      ["README.md", 0, false],
    ]);
  });

  it("honours the user's choices, which beat the depth default", () => {
    const rows = visibleRows(buildTree(graph), new Map([["src", false]]));
    expect(rows.map((r) => r.node.path)).toEqual(["src", "README.md"]);
    const deep = visibleRows(buildTree(graph), new Map([["src/core", false]]));
    expect(deep.map((r) => r.node.path)).toEqual(["src", "src/core", "src/main.py", "README.md"]);
  });
});

describe("helpers", () => {
  it("counts files below a node", () => {
    const [src] = buildTree(graph);
    expect(src && fileCount(src)).toBe(3);
  });

  it("numbers preview lines from line_start", () => {
    expect(chunkLines({ preview: "a\nb", line_start: 43 })).toEqual([
      { no: 43, text: "a" },
      { no: 44, text: "b" },
    ]);
    expect(chunkLines({ preview: "x", line_start: null })).toEqual([{ no: null, text: "x" }]);
  });

  it("formats line ranges", () => {
    expect(lineRange({ line_start: 43, line_end: 50 })).toBe("L43–50");
    expect(lineRange({ line_start: 43, line_end: 43 })).toBe("L43");
    expect(lineRange({ line_start: null, line_end: null })).toBe("");
  });

  it("falls back to the muted colour for unknown languages", () => {
    expect(languageColor("python")).toBe("#60a5fa");
    expect(languageColor("cobol")).toBe("var(--muted)");
    expect(languageColor(null)).toBe("var(--muted)");
  });
});
