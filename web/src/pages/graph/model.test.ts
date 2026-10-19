import { describe, expect, it } from "vitest";
import type { GraphEdge, GraphNode, GraphResponse, Layer } from "../../api/types";
import { applyFilters, findNodes, neighbours, nodeLabel, nodeRadius, relationTypes, relationVar, type GraphFilters } from "./model";

const node = (id: string, layer: Layer, over: Partial<GraphNode> = {}): GraphNode => ({
  id, label: `text of ${id}`, memory_layer: layer, importance: 0.5, is_pinned: false, is_deprecated: false, user_id: "u", checkpoint_name: null, ...over,
});
const edge = (source: string, target: string, type = "related_to"): GraphEdge => ({ source, target, type });

const graph: GraphResponse = {
  nodes: [node("a", "L2"), node("b", "L3"), node("c", "L3", { is_deprecated: true }), node("d", "L1"), node("e", "L4")],
  edges: [edge("a", "b"), edge("b", "c", "supersedes"), edge("a", "d", "depends_on")],
};
const all: GraphFilters = { layers: new Set<Layer>(["L1", "L2", "L3", "L4"]), relTypes: null, connectedOnly: false, showDeprecated: true };
const ids = (xs: { id: string }[]): string[] => xs.map((x) => x.id);

describe("applyFilters", () => {
  it("shows everything by default", () => {
    const v = applyFilters(graph, all);
    expect(ids(v.nodes)).toEqual(["a", "b", "c", "d", "e"]);
    expect(v.edges).toHaveLength(3);
  });

  it("drops a layer along with the edges that touch it", () => {
    const v = applyFilters(graph, { ...all, layers: new Set<Layer>(["L2", "L3", "L4"]) });
    expect(ids(v.nodes)).toEqual(["a", "b", "c", "e"]);
    expect(v.edges.map((e) => e.type)).toEqual(["related_to", "supersedes"]);
  });

  it("hides deprecated nodes and their edges", () => {
    const v = applyFilters(graph, { ...all, showDeprecated: false });
    expect(ids(v.nodes)).not.toContain("c");
    expect(v.edges.some((e) => e.type === "supersedes")).toBe(false);
  });

  it("keeps only the chosen relation types", () => {
    const v = applyFilters(graph, { ...all, relTypes: new Set(["depends_on"]) });
    expect(v.edges).toEqual([edge("a", "d", "depends_on")]);
    expect(applyFilters(graph, { ...all, relTypes: new Set() }).edges).toEqual([]);
  });

  it("connected only drops nodes with no visible edge", () => {
    const v = applyFilters(graph, { ...all, connectedOnly: true });
    expect(ids(v.nodes)).toEqual(["a", "b", "c", "d"]);
    const narrowed = applyFilters(graph, { ...all, connectedOnly: true, relTypes: new Set(["depends_on"]) });
    expect(ids(narrowed.nodes)).toEqual(["a", "d"]);
  });

  it("keeps the focused node whatever the filters say", () => {
    const v = applyFilters(graph, { ...all, layers: new Set<Layer>(["L2"]), connectedOnly: true, keep: "e" });
    expect(ids(v.nodes)).toEqual(["e"]);
  });
});

describe("helpers", () => {
  it("lists relation types present: known order first, then custom", () => {
    const g: GraphResponse = { nodes: [], edges: [edge("a", "b", "zeta"), edge("a", "b", "part_of"), edge("a", "b", "related_to"), edge("a", "b", "alpha")] };
    expect(relationTypes(g)).toEqual(["related_to", "part_of", "alpha", "zeta"]);
  });

  it("maps neighbours in both directions", () => {
    const n = neighbours(graph.edges);
    expect([...(n.get("a") ?? [])].sort()).toEqual(["b", "d"]);
    expect([...(n.get("b") ?? [])].sort()).toEqual(["a", "c"]);
    expect(n.has("e")).toBe(false);
  });

  it("sizes dots by importance within bounds", () => {
    expect(nodeRadius(0)).toBe(3);
    expect(nodeRadius(1)).toBe(9);
    expect(nodeRadius(5)).toBe(9);
    expect(nodeRadius(-1)).toBe(3);
    expect(nodeRadius(Number.NaN)).toBe(3);
  });

  it("labels a node by checkpoint name or shortened text", () => {
    expect(nodeLabel({ checkpoint_name: "handoff-model-v1", label: "x" })).toBe("handoff-model-v1");
    expect(nodeLabel({ checkpoint_name: null, label: "  a\n b  " })).toBe("a b");
    expect(nodeLabel({ checkpoint_name: null, label: "x".repeat(50) }, 10)).toBe(`${"x".repeat(9)}…`);
  });

  it("finds nodes, exact checkpoint names first and then by importance", () => {
    const nodes = [
      node("t1", "L3", { label: "uses qdrant", importance: 0.2 }),
      node("t2", "L3", { label: "other", checkpoint_name: "qdrant", importance: 0.1 }),
      node("t3", "L3", { label: "qdrant too", importance: 0.9 }),
      node("t4", "L3", { label: "nothing" }),
    ];
    expect(ids(findNodes(nodes, " Qdrant "))).toEqual(["t2", "t3", "t1"]);
    expect(findNodes(nodes, "")).toEqual([]);
    expect(findNodes(nodes, "zzz")).toEqual([]);
  });

  it("falls back to the related_to colour for custom relation types", () => {
    expect(relationVar("supersedes")).toBe("--r-supersedes");
    expect(relationVar("mine")).toBe("--r-related_to");
  });
});
