// Shapes of /dashboard/api/*. Real endpoints mirror
// src/transports/mcp/dashboard; endpoints marked (new) are mocked in client.ts
// until the backend lands.

export type Layer = "L1" | "L2" | "L3" | "L4";
export const LAYERS: readonly Layer[] = ["L1", "L2", "L3", "L4"];
export const LAYER_NAMES: Record<Layer, string> = {
  L1: "Working",
  L2: "Episodic",
  L3: "Semantic",
  L4: "Procedural",
};
export type ByLayer = Record<Layer, number>;

export type Origin =
  | "user-stated"
  | "agent-inferred"
  | "observer"
  | "ingested-from-file"
  | "ingested-from-web";
export const ORIGINS: readonly Origin[] = [
  "user-stated",
  "agent-inferred",
  "observer",
  "ingested-from-file",
  "ingested-from-web",
];

export type RelationType =
  | "related_to"
  | "supersedes"
  | "part_of"
  | "depends_on"
  | "caused_by"
  | "alternative_to"
  | "used_for";
export const RELATION_TYPES: readonly RelationType[] = [
  "related_to",
  "supersedes",
  "part_of",
  "depends_on",
  "caused_by",
  "alternative_to",
  "used_for",
];

// ── overview ────────────────────────────────────────────────────
export interface CollectionStats {
  collection: string;
  slug: string;
  is_user_global: boolean;
  total: number;
  active: number;
  deprecated: number;
  pinned: number;
  by_layer: ByLayer;
}
export interface ServerInfo {
  version: string;
  commit: string;
  tools: number;
}
export interface Overview {
  totals: {
    total: number;
    active: number;
    deprecated: number;
    pinned: number;
    by_layer: ByLayer;
    collections: number;
    open_tasks: number;
  };
  collections: CollectionStats[];
  server: ServerInfo;
}

// ── memories ────────────────────────────────────────────────────
export interface MemoryItem {
  id: string;
  content: string;
  /** list rows only: content was cut at 400 chars */
  truncated?: boolean;
  memory_layer: Layer;
  importance: number;
  is_pinned: boolean;
  is_deprecated: boolean;
  origin: string | null;
  type: string | null;
  checkpoint_name?: string | null;
  user_id: string | null;
  session_id?: string | null;
  project: string | null;
  created_at: string | null;
  possibly_stale?: boolean;
}
export interface ItemsResponse {
  collection: string;
  count: number;
  generated_at: string;
  items: MemoryItem[];
}
export type ItemSort = "created_at" | "importance";
/** GET /items with the paging params (PR #59). One layer at a time. */
export interface ItemsQuery {
  collection: string;
  layer?: Layer;
  user?: string;
  q?: string;
  origin?: string;
  type?: string;
  stale?: boolean;
  include_deprecated?: boolean;
  from?: string;
  to?: string;
  offset?: number;
  limit?: number;
  sort?: ItemSort;
  dir?: "asc" | "desc";
}
export interface ItemsPage {
  collection?: string;
  count?: number;
  offset?: number;
  generated_at?: string;
  total: number;
  /** true when the server hit its 5000-item scan cap */
  capped?: boolean;
  items: (MemoryItem & { possibly_stale?: boolean })[];
}
export interface RelatedRef {
  id: string;
  type: RelationType | string;
  direction: "in" | "out";
  label: string;
  /** not sent by the server; filled in when known */
  memory_layer?: Layer;
}
/** GET /items/{id}?collection (PR #59) */
export interface ItemDetail extends MemoryItem {
  confidence: number | null;
  fact_type: string | null;
  files: string[];
  possibly_stale: boolean;
  stale_files?: string[];
  deprecated_at?: string | null;
  deprecated_reason?: string | null;
  metadata?: Record<string, unknown> | null;
  related: RelatedRef[];
  score?: Record<string, number> | null;
}
export type ItemAction = "pin" | "unpin" | "deprecate" | "restore";

export interface UserStats {
  user_id: string;
  total: number;
  active: number;
  deprecated: number;
  pinned: number;
  by_layer: ByLayer;
  last_created_at: string | null;
}

// ── graph ───────────────────────────────────────────────────────
export interface GraphNode {
  id: string;
  label: string;
  memory_layer: Layer;
  importance: number;
  is_pinned: boolean;
  is_deprecated: boolean;
  user_id: string | null;
  checkpoint_name: string | null;
}
export interface GraphEdge {
  source: string;
  target: string;
  type: RelationType | string;
}
export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// ── tasks ───────────────────────────────────────────────────────
export type TaskStatus = "pending" | "in_progress" | "done" | "cancelled";
export const TASK_STATUSES: readonly TaskStatus[] = ["pending", "in_progress", "done", "cancelled"];
export type Priority = "low" | "medium" | "high";
export interface Task {
  id: string;
  title: string;
  description: string | null;
  status: TaskStatus;
  priority: Priority;
  project: string | null;
  user_id: string;
  assignee?: string | null;
  created_at: string | null;
  updated_at?: string | null;
}
/** PATCH /tasks/{id}; user is the task's owner (user_id from the list). */
export interface TaskPatch {
  user: string;
  status?: TaskStatus;
  priority?: Priority;
  title?: string;
  description?: string;
  assignee?: string;
}

// ── skills (new) ────────────────────────────────────────────────
export interface Skill {
  slug: string;
  name: string;
  description: string;
  project: string | null;
  user_id: string;
  updated_at: string | null;
}
export interface SkillDetail extends Skill {
  content: string;
  frontmatter?: Record<string, unknown> | null;
}

// ── code index ──────────────────────────────────────────────────
export interface IndexInfo {
  collection: string;
  project: string;
  files: number;
  chunks: number;
  by_language: Record<string, number>;
  last_indexed_at: string | null;
  users: string[];
}
export interface IndexNode {
  id: string;
  label: string;
  kind: "project" | "dir" | "file";
  language: string | null;
  chunks: number;
  indexed_at?: string | null;
}
export interface IndexGraph {
  nodes: IndexNode[];
  edges: { source: string; target: string; type: string }[];
  truncated: boolean;
}
export interface IndexChunk {
  path: string;
  chunk_index: number;
  chunk_total: number;
  symbol: string | null;
  kind: string | null;
  language: string | null;
  line_start: number | null;
  line_end: number | null;
  preview: string;
  indexed_at: string | null;
}
export interface IndexChunks {
  total: number;
  chunks: IndexChunk[];
}

// ── usage ───────────────────────────────────────────────────────
export interface UsageCounts {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  embedding_tokens: number;
}
export interface Usage {
  days: number;
  totals: UsageCounts & { embedding_cost_usd: number };
  by_tool: (UsageCounts & { tool: string })[];
  by_project: (UsageCounts & { project: string })[];
  by_day: (UsageCounts & { day: string })[];
}

// ── hygiene (new) ───────────────────────────────────────────────
export interface Hygiene {
  collection: string;
  generated_at: string;
  expiring_7d: number;
  deprecated: number;
  pending_hard_delete: number;
  protected: number;
  duplicates: { a: MemoryItem; b: MemoryItem; similarity: number | null }[];
  stale: MemoryItem[];
}

// ── briefs (handoffs) ───────────────────────────────────────────
export type BriefStatus = "draft" | "assigned" | "in_progress" | "blocked" | "done" | "cancelled";
export const BRIEF_STATUSES: readonly BriefStatus[] = [
  "draft",
  "assigned",
  "in_progress",
  "blocked",
  "done",
  "cancelled",
];
export interface LastReport {
  text: string;
  agent: string;
  at: string | null;
}
export interface Brief {
  id: string;
  slug: string;
  title: string;
  project: string;
  assignee: string | null;
  status: BriefStatus;
  version: number;
  user_id: string;
  updated_at: string | null;
  last_report: LastReport | null;
}
export interface BriefReport {
  at: string | null;
  agent: string;
  /** status_after of the report; null when the report did not change status */
  status: BriefStatus | null;
  text: string;
}
export interface BriefVersion {
  version: number;
  at: string | null;
  author: string | null;
}
export interface BriefDetail extends Omit<Brief, "last_report"> {
  created_at?: string | null;
  body: string;
  /** newest first */
  reports: BriefReport[];
  /** newest first */
  versions: BriefVersion[];
}
export interface BriefCreate {
  user: string;
  project: string;
  title: string;
  body: string;
  assignee?: string | null;
}
export interface BriefPatch {
  user: string;
  title?: string;
  body?: string;
  status?: BriefStatus;
  assignee?: string;
}
