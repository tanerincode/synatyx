import { describe, expect, it } from "vitest";
import { DONE, groupRelated, optimisticFor } from "./itemActions";

describe("optimisticFor", () => {
  it("predicts what the server will do for each action", () => {
    expect(optimisticFor("pin")).toEqual({ is_pinned: true });
    expect(optimisticFor("unpin")).toEqual({ is_pinned: false });
    expect(optimisticFor("deprecate")).toEqual({ is_deprecated: true });
    expect(optimisticFor("restore")).toEqual({ is_deprecated: false });
  });
  it("has a toast word for every action", () => {
    expect(Object.keys(DONE).sort()).toEqual(["deprecate", "pin", "restore", "unpin"]);
  });
});

describe("groupRelated", () => {
  it("groups edges by type in first-seen order", () => {
    const r = (id: string, type: string) => ({ id, type, direction: "out" as const, label: id });
    const groups = groupRelated([r("a", "supersedes"), r("b", "related_to"), r("c", "supersedes")]);
    expect(groups.map(([t, rs]) => [t, rs.map((x) => x.id)])).toEqual([
      ["supersedes", ["a", "c"]],
      ["related_to", ["b"]],
    ]);
    expect(groupRelated([])).toEqual([]);
  });
});
