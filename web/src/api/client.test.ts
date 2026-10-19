import { api, ApiError, auth, filterSortPage, onUnauthorized } from "./client";
import type { MemoryItem } from "./types";

function stubFetch(status: number, body: unknown) {
  const fn = vi.fn(async () => new Response(typeof body === "string" ? body : JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("request", () => {
  it("sends the admin key and query to /dashboard/api", async () => {
    auth.set("k-123");
    const fetch = stubFetch(200, { total: 0, items: [] });
    await api.itemsPage({ collection: "ctx_synatyx", layer: "L3", q: "qdrant", stale: true, offset: 50, limit: 25, sort: "importance", dir: "desc" });
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/dashboard/api/items?collection=ctx_synatyx&layer=L3&q=qdrant&stale=true&offset=50&limit=25&sort=-importance");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer k-123");
  });

  it("sends JSON bodies on writes", async () => {
    const fetch = stubFetch(200, { id: "t1" });
    await api.patchTask("t1", { user: "tom", status: "done" });
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/dashboard/api/tasks/t1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ user: "tom", status: "done" });
  });

  it("surfaces the server's error message", async () => {
    stubFetch(400, { error: "unknown collection 'ctx_nope'" });
    await expect(api.items("ctx_nope")).rejects.toMatchObject({ message: "unknown collection 'ctx_nope'", status: 400 });
  });

  it("falls back to the body text when the error is not JSON", async () => {
    stubFetch(502, "Bad gateway");
    await expect(api.overview()).rejects.toMatchObject({ message: "Bad gateway", status: 502 });
  });

  it("clears the key and notifies on 401", async () => {
    auth.set("stale");
    const seen = vi.fn();
    const off = onUnauthorized(seen);
    stubFetch(401, { error: "unauthorized" });
    await expect(api.overview()).rejects.toBeInstanceOf(ApiError);
    expect(auth.get()).toBe("");
    expect(seen).toHaveBeenCalledOnce();
    off();
  });

  it("reports network failures as status 0", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(api.overview()).rejects.toMatchObject({ status: 0, message: "Network error: Failed to fetch" });
  });

  it("refuses earlier brief versions until the server serves them", async () => {
    await expect(api.briefVersionBody("b1", "tom", 1)).rejects.toMatchObject({ status: 501 });
  });
});

describe("filterSortPage (mock paging)", () => {
  const row = (id: string, o: Partial<MemoryItem>): MemoryItem => ({
    id, content: `item ${id}`, memory_layer: "L3", importance: 0.5, is_pinned: false, is_deprecated: false, origin: "observer",
    type: null, user_id: "tom", project: "p", created_at: "2026-10-01T00:00:00Z", ...o,
  });
  const rows = [
    row("a", { importance: 0.9, created_at: "2026-10-03T00:00:00Z" }),
    row("b", { importance: 0.1, memory_layer: "L2", created_at: "2026-10-02T00:00:00Z" }),
    row("c", { importance: 0.5, is_deprecated: true }),
    row("d", { importance: 0.7, possibly_stale: true, content: "Qdrant collection" }),
  ];
  it("hides deprecated unless asked, newest first by default", () => {
    expect(filterSortPage(rows, { collection: "x" }).items.map((r) => r.id)).toEqual(["a", "b", "d"]);
    expect(filterSortPage(rows, { collection: "x", include_deprecated: true }).total).toBe(4);
  });
  it("filters by layer, text and staleness and pages", () => {
    expect(filterSortPage(rows, { collection: "x", layer: "L2" }).items.map((r) => r.id)).toEqual(["b"]);
    expect(filterSortPage(rows, { collection: "x", q: "qdrant" }).items.map((r) => r.id)).toEqual(["d"]);
    expect(filterSortPage(rows, { collection: "x", stale: true }).items.map((r) => r.id)).toEqual(["d"]);
    const p = filterSortPage(rows, { collection: "x", sort: "importance", dir: "asc", offset: 1, limit: 1 });
    expect(p).toMatchObject({ total: 3 });
    expect(p.items.map((r) => r.id)).toEqual(["d"]);
  });
});
