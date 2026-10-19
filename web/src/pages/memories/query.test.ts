import { describe, expect, it } from "vitest";
import { hasActiveFilters, nextSort, pageInfo, parseFilters, queryKey, toQuery } from "./query";

const params = (o: Record<string, string>) => (k: string) => o[k] ?? "";

describe("parseFilters", () => {
  it("defaults to newest first, 15 per page, nothing filtered", () => {
    const f = parseFilters(params({}));
    expect(f).toMatchObject({ q: "", layer: "", sort: "created_at", dir: "desc", page: 0, size: 15, stale: false, deprecated: false });
    expect(hasActiveFilters(f)).toBe(false);
  });
  it("reads every param and ignores junk", () => {
    const f = parseFilters(params({ q: "redis", layer: "L3", origin: "user-stated", type: "attempt", range: "7d", stale: "1", dep: "1", sort: "importance", dir: "asc", page: "3", size: "50" }));
    expect(f).toMatchObject({ q: "redis", layer: "L3", origin: "user-stated", type: "attempt", range: "7d", stale: true, deprecated: true, sort: "importance", dir: "asc", page: 2, size: 50 });
    expect(hasActiveFilters(f)).toBe(true);
    const junk = parseFilters(params({ layer: "L9", sort: "nope", page: "-2", size: "7", range: "1y" }));
    expect(junk).toMatchObject({ layer: "", sort: "created_at", page: 0, size: 15, range: "" });
  });
});

describe("toQuery", () => {
  const now = Date.parse("2026-10-19T12:00:00Z");
  it("maps filters to the API query with offset and limit", () => {
    const f = parseFilters(params({ q: "x", layer: "L2", page: "3", size: "50", range: "24h", stale: "1", sort: "importance", dir: "asc" }));
    expect(toQuery(f, "ctx_kai", "tom", now)).toEqual({
      collection: "ctx_kai", user: "tom", q: "x", layer: "L2", origin: undefined, type: undefined, stale: true,
      include_deprecated: undefined, from: "2026-10-18T12:00:00.000Z", sort: "importance", dir: "asc", offset: 100, limit: 50,
    });
  });
  it("drops empty values and gives each request its own key", () => {
    const q = toQuery(parseFilters(params({})), "ctx_kai", "", now);
    expect(q.user).toBeUndefined();
    expect(q.from).toBeUndefined();
    expect(queryKey(q)).not.toBe(queryKey({ ...q, offset: 15 }));
  });
});

describe("pageInfo", () => {
  it("counts pages and the visible range", () => {
    expect(pageInfo(0, 0, 15)).toEqual({ pages: 1, from: 0, to: 0 });
    expect(pageInfo(40, 0, 15)).toEqual({ pages: 3, from: 1, to: 15 });
    expect(pageInfo(40, 2, 15)).toEqual({ pages: 3, from: 31, to: 40 });
    expect(pageInfo(40, 9, 15)).toEqual({ pages: 3, from: 31, to: 40 });
  });
});

describe("nextSort", () => {
  it("flips the active column and starts new ones descending", () => {
    expect(nextSort({ sort: "created_at", dir: "desc" }, "created_at")).toEqual({ sort: "created_at", dir: "asc" });
    expect(nextSort({ sort: "created_at", dir: "asc" }, "created_at")).toEqual({ sort: "created_at", dir: "desc" });
    expect(nextSort({ sort: "created_at", dir: "asc" }, "importance")).toEqual({ sort: "importance", dir: "desc" });
  });
});
