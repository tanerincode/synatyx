import type { Usage, UsageCounts } from "../../api/types";

export const WINDOWS = [7, 30, 90] as const;
export type Window = (typeof WINDOWS)[number];
export const DEFAULT_WINDOW: Window = 30;

/** Parse the ?days= param; anything that is not a known window falls back to 30. */
export function parseWindow(raw: string): Window {
  const n = Number(raw);
  return (WINDOWS as readonly number[]).includes(n) ? (n as Window) : DEFAULT_WINDOW;
}

export type Series = "output_tokens" | "input_tokens" | "embedding_tokens";

/** Stack order, bottom first, with the legend name and colour of each series. */
export const SERIES: readonly { key: Series; label: string; color: string }[] = [
  { key: "embedding_tokens", label: "embeddings", color: "var(--violet)" },
  { key: "input_tokens", label: "args in", color: "var(--cyan)" },
  { key: "output_tokens", label: "context out", color: "var(--good)" },
];

export interface AreaLayer {
  key: Series;
  label: string;
  color: string;
  /** SVG path data inside a `width` × `height` box. */
  path: string;
}

export interface StackedAreas {
  layers: AreaLayer[];
  /** The largest stacked day, which is the top of the y axis. */
  max: number;
}

const round = (n: number): number => Math.round(n * 100) / 100;

/** x position of day `i` of `n` across `width`; a single day sits in the middle. */
export function dayX(i: number, n: number, width: number): number {
  return n < 2 ? width / 2 : (i / (n - 1)) * width;
}

/**
 * Stacked area paths for the daily series. Each layer is the band between the
 * running total below it and the total including it; the y axis tops out at the
 * largest daily total. An empty window gives no layers.
 */
export function stackAreas(days: readonly UsageCounts[], width: number, height: number): StackedAreas {
  const n = days.length;
  const totals = days.map((d) => SERIES.reduce((s, { key }) => s + d[key], 0));
  const max = Math.max(0, ...totals);
  if (n === 0 || max === 0) return { layers: [], max };
  const y = (v: number): number => round(height - (v / max) * height);

  const below = new Array<number>(n).fill(0);
  const layers: AreaLayer[] = SERIES.map(({ key, label, color }) => {
    const lower = [...below];
    const upper = days.map((d, i) => (below[i] ?? 0) + d[key]);
    upper.forEach((v, i) => (below[i] = v));
    const half = n < 2 ? 4 : 0; // a lone day is drawn as a narrow column
    const top = upper.map((v, i) => `${round(dayX(i, n, width) - half)},${y(v)}`);
    const bottom = lower.map((v, i) => `${round(dayX(i, n, width) + half)},${y(v)}`).reverse();
    const path =
      n < 2
        ? `M${top[0]} L${round(dayX(0, n, width) + half)},${y(upper[0] ?? 0)} L${bottom[0]} L${round(dayX(0, n, width) - half)},${y(lower[0] ?? 0)} Z`
        : `M${top.join(" L")} L${bottom.join(" L")} Z`;
    return { key, label, color, path };
  });
  return { layers, max };
}

/** "context_retrieve" → "retrieve"; the prefix is on every tool. */
export const toolLabel = (tool: string): string => tool.replace(/^context_/, "");

/**
 * A project's share of the embedding cost. The server prices only the total, so
 * a project gets the total in proportion to its embedding tokens.
 */
export function projectCost(totals: Usage["totals"], embeddingTokens: number): number {
  return totals.embedding_tokens > 0 ? (totals.embedding_cost_usd * embeddingTokens) / totals.embedding_tokens : 0;
}

/** Width in percent of a bar against the largest value, never below `floor` for a non-zero value. */
export function barPercent(value: number, max: number, floor = 3): number {
  if (value <= 0 || max <= 0) return 0;
  return Math.max(floor, Math.min(100, (value / max) * 100));
}

/** "2026-10-05" → "Oct 5". */
export function dayLabel(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}
