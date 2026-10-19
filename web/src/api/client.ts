// The one typed client for /dashboard/api/*. Components never call fetch.
// Every endpoint here is on the server. VITE_MOCK_ALL=1 (e2e, offline dev)
// serves all of them from an in-memory mock store instead.
import { BASE_PATH } from "../lib/base";
import { buildMockStore, type MockItem, type MockStore } from "./mockData";
import type {
  Brief,
  BriefCreate,
  BriefDetail,
  BriefPatch,
  BriefStatus,
  GraphResponse,
  Hygiene,
  IndexChunks,
  IndexGraph,
  IndexInfo,
  ItemAction,
  ItemDetail,
  ItemsPage,
  ItemsQuery,
  ItemsResponse,
  Layer,
  MemoryItem,
  Overview,
  Skill,
  SkillDetail,
  Task,
  TaskPatch,
  TaskStatus,
  Usage,
  UserStats,
} from "./types";

export const MOCK_ALL = import.meta.env.VITE_MOCK_ALL === "1";

// ── auth ────────────────────────────────────────────────────────
const KEY = "synatyx_admin_key";
export const auth = {
  get: (): string => sessionStorage.getItem(KEY) ?? "",
  set: (k: string): void => sessionStorage.setItem(KEY, k),
  clear: (): void => sessionStorage.removeItem(KEY),
};
type Listener = () => void;
const unauthorized = new Set<Listener>();
export function onUnauthorized(fn: Listener): () => void {
  unauthorized.add(fn);
  return () => unauthorized.delete(fn);
}

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// ── transport ───────────────────────────────────────────────────
type Query = Record<string, string | number | boolean | undefined | null>;
const API = `${BASE_PATH}/api`;

function qs(q: Query = {}): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

async function request<T>(path: string, opts: { method?: string; query?: Query; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${auth.get()}` };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(`${API}${path}${qs(opts.query)}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });
  } catch (e) {
    throw new ApiError(`Network error: ${(e as Error).message}`, 0);
  }
  if (res.status === 401) {
    auth.clear();
    unauthorized.forEach((fn) => fn());
    throw new ApiError("The admin key was rejected", 401);
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // non-JSON body (proxy error page); fall through to the status message
  }
  if (!res.ok) {
    const msg = data && typeof data === "object" && "error" in data ? String((data as { error: unknown }).error) : text.slice(0, 200) || res.statusText;
    throw new ApiError(msg, res.status);
  }
  return data as T;
}

// ── mock plumbing ───────────────────────────────────────────────
let store: MockStore | null = null;
const mockStore = (): MockStore => (store ??= buildMockStore());
/** Tests reset the store between cases. */
export function resetMocks(): void {
  store = null;
}
export const mockConfig = { delayMs: 120 };
const delay = <T,>(v: T): Promise<T> =>
  new Promise((r) => setTimeout(() => r(structuredClone(v)), mockConfig.delayMs));

export const collectionFor = (project: string): string => `ctx_${project}`;
export const projectOf = (collection: string): string => collection.replace(/^ctx_/, "");
const itemsIn = (collection: string): MockItem[] =>
  mockStore().items.filter((x) => collectionFor(x.project ?? "") === collection);
const toRow = (x: MockItem): MemoryItem & { possibly_stale: boolean } => ({
  id: x.id, content: x.content, truncated: x.truncated, memory_layer: x.memory_layer, importance: x.importance,
  is_pinned: x.is_pinned, is_deprecated: x.is_deprecated, origin: x.origin, type: x.type, checkpoint_name: x.checkpoint_name,
  user_id: x.user_id, session_id: x.session_id, project: x.project, created_at: x.created_at, possibly_stale: x.possibly_stale,
});
const byLayer = (xs: MockItem[]): Record<Layer, number> => {
  const b: Record<Layer, number> = { L1: 0, L2: 0, L3: 0, L4: 0 };
  xs.forEach((x) => b[x.memory_layer]++);
  return b;
};

// ── pure helpers (exported for tests) ───────────────────────────
export function filterSortPage(rows: (MemoryItem & { possibly_stale?: boolean })[], q: ItemsQuery): ItemsPage {
  const needle = q.q?.toLowerCase() ?? "";
  const xs = rows.filter(
    (x) =>
      (!needle || x.content.toLowerCase().includes(needle) || x.id.startsWith(needle)) &&
      (!q.layer || x.memory_layer === q.layer) &&
      (!q.user || x.user_id === q.user) &&
      (!q.origin || x.origin === q.origin) &&
      (!q.type || x.type === q.type) &&
      (!q.stale || !!x.possibly_stale) &&
      (q.include_deprecated || !x.is_deprecated) &&
      (!q.from || (x.created_at ?? "") >= q.from) &&
      (!q.to || (x.created_at ?? "") <= q.to),
  );
  const sort = q.sort ?? "created_at";
  const dir = q.dir === "asc" ? 1 : -1;
  xs.sort((a, b) => {
    const av = a[sort] ?? "";
    const bv = b[sort] ?? "";
    return (av > bv ? 1 : av < bv ? -1 : 0) * dir;
  });
  const offset = q.offset ?? 0;
  return { total: xs.length, items: xs.slice(offset, offset + (q.limit ?? 25)) };
}

// ── endpoints ───────────────────────────────────────────────────
export const api = {
  async overview(): Promise<Overview> {
    if (!MOCK_ALL) return request<Overview>("/overview");
    const s = mockStore();
    const cols = [...s.projects, "users"].map((p) => {
      const xs = s.items.filter((x) => x.project === p);
      const act = xs.filter((x) => !x.is_deprecated);
      return {
        collection: collectionFor(p), slug: p, is_user_global: p === "users", total: xs.length, active: act.length,
        deprecated: xs.length - act.length, pinned: act.filter((x) => x.is_pinned).length, by_layer: byLayer(act),
      };
    });
    const act = s.items.filter((x) => !x.is_deprecated);
    return delay({
      totals: {
        total: s.items.length, active: act.length, deprecated: s.items.length - act.length, pinned: act.filter((x) => x.is_pinned).length,
        by_layer: byLayer(act), collections: cols.length, open_tasks: s.tasks.filter((t) => t.status === "pending").length,
      },
      collections: cols,
      server: s.server,
    });
  },

  async items(collection: string, o: { layer?: Layer; user?: string; include_deprecated?: boolean; limit?: number } = {}): Promise<ItemsResponse> {
    if (!MOCK_ALL) return request<ItemsResponse>("/items", { query: { collection, ...o } });
    const rows = itemsIn(collection)
      .filter((x) => (!o.layer || x.memory_layer === o.layer) && (!o.user || x.user_id === o.user) && (o.include_deprecated || !x.is_deprecated))
      .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))
      .slice(0, o.limit ?? 50)
      .map(toRow);
    return delay({ collection, count: rows.length, generated_at: new Date(mockStore().now).toISOString(), items: rows });
  },

  /** Filtered, sorted, paged items in one collection. */
  async itemsPage(q: ItemsQuery): Promise<ItemsPage> {
    if (!MOCK_ALL) {
      return request<ItemsPage>("/items", {
        query: {
          collection: q.collection, layer: q.layer, user: q.user, q: q.q, origin: q.origin, type: q.type,
          stale: q.stale, include_deprecated: q.include_deprecated, from: q.from, to: q.to,
          offset: q.offset, limit: q.limit, sort: q.sort ? `${q.dir === "asc" ? "" : "-"}${q.sort}` : undefined,
        },
      });
    }
    return delay(filterSortPage(itemsIn(q.collection).map(toRow), q));
  },

  /** One item with relations, file staleness and deprecation info. */
  async item(id: string, collection: string): Promise<ItemDetail> {
    if (!MOCK_ALL) return request<ItemDetail>(`/items/${encodeURIComponent(id)}`, { query: { collection } });
    const s = mockStore();
    const x = s.items.find((i) => i.id === id);
    if (!x) throw new ApiError("item not found", 404);
    const byId = new Map(s.items.map((i) => [i.id, i]));
    return delay({
      ...toRow(x), confidence: x.confidence, fact_type: x.fact_type, files: x.files, possibly_stale: x.possibly_stale,
      stale_files: x.possibly_stale ? x.files.slice(-1) : [], deprecated_at: x.is_deprecated ? x.created_at : null, deprecated_reason: null,
      score: { semantic: 0.82, recency: x.memory_layer === "L1" ? 0.95 : 0.41, importance: x.importance, confidence: x.confidence },
      related: s.edges
        .filter((e) => e.source === id || e.target === id)
        .map((e) => {
          const other = byId.get(e.source === id ? e.target : e.source);
          return { id: other?.id ?? "", type: e.type, direction: e.source === id ? "out" : "in", label: other?.content ?? "", memory_layer: other?.memory_layer };
        }),
    });
  },

  /** pin / unpin / deprecate (optional reason) / restore; returns the updated detail. */
  async itemAction(id: string, action: ItemAction, collection: string, reason?: string): Promise<ItemDetail> {
    if (!MOCK_ALL) {
      return request<ItemDetail>(`/items/${encodeURIComponent(id)}/${action}`, {
        method: "POST", query: { collection }, body: action === "deprecate" && reason ? { reason } : undefined,
      });
    }
    const x = mockStore().items.find((i) => i.id === id);
    if (!x) throw new ApiError("item not found", 404);
    if (action === "pin") x.is_pinned = true;
    if (action === "unpin") x.is_pinned = false;
    if (action === "deprecate") x.is_deprecated = true;
    if (action === "restore") x.is_deprecated = false;
    return api.item(id, collection);
  },

  async users(collection: string): Promise<{ users: UserStats[] }> {
    if (!MOCK_ALL) return request("/users", { query: { collection } });
    const groups = new Map<string, MockItem[]>();
    itemsIn(collection).forEach((x) => groups.set(x.user_id ?? "(unknown)", [...(groups.get(x.user_id ?? "(unknown)") ?? []), x]));
    return delay({
      users: [...groups.entries()].map(([user_id, xs]) => ({
        user_id, total: xs.length, active: xs.filter((x) => !x.is_deprecated).length, deprecated: xs.filter((x) => x.is_deprecated).length,
        pinned: xs.filter((x) => x.is_pinned).length, by_layer: byLayer(xs), last_created_at: xs.map((x) => x.created_at ?? "").sort().pop() ?? null,
      })),
    });
  },

  async graph(collection: string, o: { user?: string; limit?: number } = {}): Promise<GraphResponse> {
    let res: GraphResponse;
    if (!MOCK_ALL) {
      res = await request<GraphResponse>("/graph", { query: { collection, ...o } });
    } else {
      const xs = itemsIn(collection).filter((x) => !o.user || x.user_id === o.user).slice(0, o.limit ?? 150);
      const ids = new Set(xs.map((x) => x.id));
      res = await delay({
        nodes: xs.map((x) => ({ id: x.id, label: x.content.slice(0, 120), memory_layer: x.memory_layer, importance: x.importance, is_pinned: x.is_pinned, is_deprecated: x.is_deprecated, user_id: x.user_id, checkpoint_name: x.checkpoint_name })),
        edges: mockStore().edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
      });
    }
    return res;
  },

  async tasks(status?: TaskStatus | "all"): Promise<{ tasks: Task[] }> {
    if (!MOCK_ALL) return request("/tasks", { query: { status: status ?? "all" } });
    return delay({ tasks: mockStore().tasks.filter((t) => !status || status === "all" || t.status === status) });
  },

  /** PATCH a task; `user` is the task's owner (user_id from the list). */
  async patchTask(id: string, patch: TaskPatch): Promise<Task> {
    if (!MOCK_ALL) return request<Task>(`/tasks/${encodeURIComponent(id)}`, { method: "PATCH", body: patch });
    const t = mockStore().tasks.find((x) => x.id === id && x.user_id === patch.user);
    if (!t) throw new ApiError("task not found", 404);
    const { user: _u, ...fields } = patch;
    Object.assign(t, fields, { updated_at: new Date(mockStore().now).toISOString() });
    return delay(t);
  },

  async skills(o: { user?: string; project?: string } = {}): Promise<{ skills: Skill[] }> {
    if (!MOCK_ALL) return request("/skills", { query: o });
    return delay({
      skills: mockStore()
        .skills.filter((x) => (!o.user || x.user_id === o.user) && (!o.project || !x.project || x.project === o.project))
        .map(({ content: _c, frontmatter: _f, ...x }) => x),
    });
  },
  /** `user` is required by the server (the skill's user_id). */
  async skill(slug: string, user: string, project?: string): Promise<SkillDetail> {
    if (!MOCK_ALL) return request(`/skills/${encodeURIComponent(slug)}`, { query: { user, project } });
    const x = mockStore().skills.find((k) => k.slug === slug && k.user_id === user);
    if (!x) throw new ApiError("skill not found", 404);
    return delay(x);
  },

  async indexes(): Promise<{ indexes: IndexInfo[] }> {
    if (!MOCK_ALL) return request("/indexes");
    return delay({ indexes: mockStore().indexes });
  },
  async indexGraph(collection: string): Promise<IndexGraph> {
    if (!MOCK_ALL) return request("/index_graph", { query: { collection } });
    const chunks = mockStore().chunks[collection] ?? [];
    const g: IndexGraph = { nodes: [{ id: "root", label: projectOf(collection).replace(/__index$/, ""), kind: "project", language: null, chunks: 0 }], edges: [], truncated: false };
    const dirs = new Set<string>();
    const ensure = (d: string): string => {
      if (!d) return "root";
      if (!dirs.has(d)) {
        dirs.add(d);
        g.nodes.push({ id: `d:${d}`, label: d, kind: "dir", language: null, chunks: 0 });
        g.edges.push({ source: `d:${d}`, target: ensure(d.includes("/") ? d.slice(0, d.lastIndexOf("/")) : ""), type: "in" });
      }
      return `d:${d}`;
    };
    chunks.filter((c) => c.chunk_index === 0).forEach((c) => {
      g.nodes.push({ id: `f:${c.path}`, label: c.path, kind: "file", language: c.language, chunks: c.chunk_total, indexed_at: c.indexed_at });
      g.edges.push({ source: `f:${c.path}`, target: ensure(c.path.includes("/") ? c.path.slice(0, c.path.lastIndexOf("/")) : ""), type: "in" });
    });
    return delay(g);
  },
  async indexChunks(collection: string, q = "", limit = 100): Promise<IndexChunks> {
    if (!MOCK_ALL) return request("/index_chunks", { query: { collection, q, limit } });
    const n = q.toLowerCase();
    const xs = (mockStore().chunks[collection] ?? []).filter((c) => !n || c.path.toLowerCase().includes(n) || (c.symbol ?? "").toLowerCase().includes(n));
    return delay({ total: xs.length, chunks: xs.slice(0, limit) });
  },

  async usage(days: number, o: { project?: string; user?: string } = {}): Promise<Usage> {
    if (!MOCK_ALL) return request("/usage", { query: { days, ...o } });
    const u = mockStore().usage;
    const k = days / 30;
    const scale = <T extends { calls: number; input_tokens: number; output_tokens: number; embedding_tokens: number }>(r: T): T => ({
      ...r, calls: Math.round(r.calls * k), input_tokens: Math.round(r.input_tokens * k), output_tokens: Math.round(r.output_tokens * k), embedding_tokens: Math.round(r.embedding_tokens * k),
    });
    return delay({
      days, totals: { ...scale(u.totals), embedding_cost_usd: +(u.totals.embedding_cost_usd * k).toFixed(4) },
      by_tool: u.by_tool.map(scale), by_project: u.by_project.filter((p) => !o.project || p.project === o.project).map(scale),
      by_day: u.by_day.slice(-Math.min(days, 30)),
    });
  },

  async hygiene(collection: string, user?: string): Promise<Hygiene> {
    if (!MOCK_ALL) return request("/hygiene", { query: { collection, user } });
    const s = mockStore();
    const inCol = new Set(itemsIn(collection).filter((x) => !user || x.user_id === user).map((x) => x.id));
    const byId = new Map(s.items.map((x) => [x.id, x]));
    const xs = s.items.filter((x) => inCol.has(x.id));
    return delay({
      collection,
      generated_at: new Date(s.now).toISOString(),
      expiring_7d: Math.round(xs.filter((x) => x.memory_layer === "L2" && !x.is_pinned).length / 2),
      deprecated: xs.filter((x) => x.is_deprecated).length,
      pending_hard_delete: Math.round(xs.filter((x) => x.is_deprecated).length / 2),
      protected: xs.filter((x) => x.is_pinned).length,
      duplicates: s.duplicates.flatMap((d) => {
        const a = byId.get(d.a);
        const b = byId.get(d.b);
        return a && b && inCol.has(a.id) ? [{ a: toRow(a), b: toRow(b), similarity: d.similarity }] : [];
      }),
      stale: xs.filter((x) => x.possibly_stale).map(toRow),
    });
  },

  async briefs(o: { project?: string; status?: BriefStatus; assignee?: string; user?: string } = {}): Promise<{ briefs: Brief[] }> {
    if (!MOCK_ALL) return request("/briefs", { query: o });
    return delay({
      briefs: mockStore()
        .briefs.filter((b) => (!o.project || b.project === o.project) && (!o.status || b.status === o.status) && (!o.assignee || b.assignee === o.assignee) && (!o.user || b.user_id === o.user))
        .sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""))
        .map((b) => ({
          id: b.id, slug: b.slug, title: b.title, project: b.project, assignee: b.assignee, status: b.status, version: b.version,
          user_id: b.user_id, updated_at: b.updated_at, last_report: b.reports[0] ? { text: b.reports[0].text, agent: b.reports[0].agent, at: b.reports[0].at } : null,
        })),
    });
  },
  async brief(id: string, user: string): Promise<BriefDetail> {
    if (!MOCK_ALL) return request(`/briefs/${encodeURIComponent(id)}`, { query: { user } });
    const b = mockStore().briefs.find((x) => x.id === id && x.user_id === user);
    if (!b) throw new ApiError("brief not found", 404);
    const { bodies: _b, ...detail } = b;
    return delay(detail);
  },
  /** Body of an earlier version, for the diff (GET /briefs/{id}?version=N). null when the server has no such version. */
  async briefVersionBody(id: string, user: string, version: number): Promise<string | null> {
    if (!MOCK_ALL) {
      try {
        return (await request<BriefDetail>(`/briefs/${encodeURIComponent(id)}`, { query: { user, version } })).body;
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    }
    const b = mockStore().briefs.find((x) => x.id === id);
    return delay(b?.bodies[version] ?? null);
  },
  async createBrief(input: BriefCreate): Promise<BriefDetail> {
    if (!MOCK_ALL) return request("/briefs", { method: "POST", body: input });
    const s = mockStore();
    const at = new Date(s.now).toISOString();
    const slug = input.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "brief";
    if (s.briefs.some((b) => b.project === input.project && b.slug === slug)) throw new ApiError(`slug ${slug} already exists in ${input.project}`, 409);
    const b = {
      id: crypto.randomUUID(), slug, title: input.title, project: input.project, assignee: input.assignee ?? null,
      status: (input.assignee ? "assigned" : "draft") as BriefStatus, version: 1, user_id: input.user, updated_at: at, created_at: at,
      body: input.body, reports: [], versions: [{ version: 1, at, author: "dashboard" }], bodies: { 1: input.body },
    };
    s.briefs.unshift(b);
    const { bodies: _b, ...detail } = b;
    return delay(detail);
  },
  async patchBrief(id: string, patch: BriefPatch): Promise<BriefDetail> {
    if (!MOCK_ALL) return request(`/briefs/${encodeURIComponent(id)}`, { method: "PATCH", body: patch });
    const s = mockStore();
    const b = s.briefs.find((x) => x.id === id && x.user_id === patch.user);
    if (!b) throw new ApiError("brief not found", 404);
    const at = new Date(s.now).toISOString();
    if (patch.title !== undefined) b.title = patch.title;
    if (patch.status !== undefined) b.status = patch.status;
    if (patch.assignee !== undefined) b.assignee = patch.assignee || null;
    if (patch.body !== undefined && patch.body !== b.body) {
      b.version += 1;
      b.body = patch.body;
      b.bodies[b.version] = patch.body;
      b.versions.unshift({ version: b.version, at, author: "dashboard" });
    }
    b.updated_at = at;
    const { bodies: _b, ...detail } = b;
    return delay(detail);
  },
};
export type Api = typeof api;
