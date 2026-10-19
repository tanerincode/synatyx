// Pure logic for the Memories list: URL params <-> ItemsQuery, paging math.
import type { ItemSort, ItemsQuery, Layer } from "../../api/types";
import { LAYERS } from "../../api/types";

export const PAGE_SIZES = [15, 50, 100] as const;
export const DATE_PRESETS = [
  { value: "", label: "Any date", ms: 0 },
  { value: "24h", label: "Last 24 h", ms: 24 * 3600e3 },
  { value: "7d", label: "Last 7 days", ms: 7 * 24 * 3600e3 },
  { value: "30d", label: "Last 30 days", ms: 30 * 24 * 3600e3 },
] as const;

export interface MemoryFilters {
  q: string;
  layer: Layer | "";
  origin: string;
  type: string;
  range: string;
  stale: boolean;
  deprecated: boolean;
  sort: ItemSort;
  dir: "asc" | "desc";
  page: number;
  size: number;
}

export function parseFilters(param: (k: string) => string): MemoryFilters {
  const layer = param("layer");
  const sort = param("sort");
  const size = Number(param("size"));
  const page = Number(param("page"));
  return {
    q: param("q"),
    layer: (LAYERS as readonly string[]).includes(layer) ? (layer as Layer) : "",
    origin: param("origin"),
    type: param("type"),
    range: DATE_PRESETS.some((p) => p.value === param("range")) ? param("range") : "",
    stale: param("stale") === "1",
    deprecated: param("dep") === "1",
    sort: sort === "importance" ? "importance" : "created_at",
    dir: param("dir") === "asc" ? "asc" : "desc",
    page: Number.isInteger(page) && page > 0 ? page - 1 : 0,
    size: (PAGE_SIZES as readonly number[]).includes(size) ? size : PAGE_SIZES[0],
  };
}

/** `now` is injectable so tests (and the date range) are deterministic. */
export function toQuery(f: MemoryFilters, collection: string, user: string, now = Date.now()): ItemsQuery {
  const preset = DATE_PRESETS.find((p) => p.value === f.range);
  return {
    collection,
    user: user || undefined,
    q: f.q || undefined,
    layer: f.layer || undefined,
    origin: f.origin || undefined,
    type: f.type || undefined,
    stale: f.stale || undefined,
    include_deprecated: f.deprecated || undefined,
    from: preset && preset.ms ? new Date(now - preset.ms).toISOString() : undefined,
    sort: f.sort,
    dir: f.dir,
    offset: f.page * f.size,
    limit: f.size,
  };
}

/** Stable cache key for useApi: encodes every input of the request. */
export const queryKey = (q: ItemsQuery): string => `items:${JSON.stringify(q)}`;

export function pageInfo(total: number, page: number, size: number): { pages: number; from: number; to: number } {
  const pages = Math.max(1, Math.ceil(total / size));
  const p = Math.min(page, pages - 1);
  return { pages, from: total ? p * size + 1 : 0, to: Math.min(total, (p + 1) * size) };
}

/** Clicking a sort header: same column flips direction, a new one starts descending. */
export function nextSort(cur: Pick<MemoryFilters, "sort" | "dir">, col: ItemSort): { sort: ItemSort; dir: "asc" | "desc" } {
  return { sort: col, dir: cur.sort === col && cur.dir === "desc" ? "asc" : "desc" };
}

export const hasActiveFilters = (f: MemoryFilters): boolean =>
  !!(f.q || f.layer || f.origin || f.type || f.range || f.stale || f.deprecated);
