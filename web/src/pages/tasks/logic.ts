import { TASK_STATUSES, type Priority, type Task, type TaskPatch, type TaskStatus } from "../../api/types";

export const PRIORITIES: readonly Priority[] = ["high", "medium", "low"];

export interface TaskFilters {
  /** Statuses shown; cancelled tasks are hidden until asked for. */
  statuses: readonly TaskStatus[];
  priority: Priority | "";
  assignee: string;
  project: string;
  user: string;
}

export const DEFAULT_STATUSES: readonly TaskStatus[] = ["pending", "in_progress", "done"];
/** Sentinel for the "unassigned" option of the assignee filter. */
export const UNASSIGNED = "__unassigned__";

/** `status=pending,done` → statuses; absent or unusable means the default set. */
export function parseStatuses(raw: string): readonly TaskStatus[] {
  if (!raw) return DEFAULT_STATUSES;
  const wanted = new Set(raw.split(","));
  const picked = TASK_STATUSES.filter((s) => wanted.has(s));
  return picked.length > 0 ? picked : DEFAULT_STATUSES;
}

export function serializeStatuses(statuses: readonly TaskStatus[]): string | null {
  const same = statuses.length === DEFAULT_STATUSES.length && DEFAULT_STATUSES.every((s) => statuses.includes(s));
  return same ? null : statuses.join(",");
}

export function toggleStatus(statuses: readonly TaskStatus[], status: TaskStatus): readonly TaskStatus[] {
  const next = statuses.includes(status) ? statuses.filter((s) => s !== status) : [...statuses, status];
  // Never end up showing nothing because the last chip was clicked off.
  return next.length === 0 ? statuses : TASK_STATUSES.filter((s) => next.includes(s));
}

export function parsePriority(raw: string): Priority | "" {
  return (PRIORITIES as readonly string[]).includes(raw) ? (raw as Priority) : "";
}

export function filterTasks(tasks: readonly Task[], f: TaskFilters): Task[] {
  return tasks.filter(
    (t) =>
      f.statuses.includes(t.status) &&
      (!f.priority || t.priority === f.priority) &&
      (!f.project || t.project === f.project) &&
      (!f.user || t.user_id === f.user) &&
      (!f.assignee || (f.assignee === UNASSIGNED ? !t.assignee : t.assignee === f.assignee)),
  );
}

export function countOpen(tasks: readonly Task[]): number {
  return tasks.filter((t) => t.status === "pending" || t.status === "in_progress").length;
}

export function assigneesOf(tasks: readonly Task[]): string[] {
  return [...new Set(tasks.map((t) => t.assignee).filter((a): a is string => !!a))].sort();
}

/** The PATCH body for one change; `user` is the row's owner, never the page filter. */
export function patchFor(task: Task, change: Omit<TaskPatch, "user">): TaskPatch {
  return { user: task.user_id, ...change };
}

/** Show pending edits over the server's rows (optimistic update). */
export function withEdits(tasks: readonly Task[], edits: Readonly<Record<string, Partial<Task>>>): Task[] {
  return tasks.map((t) => (edits[t.id] ? { ...t, ...edits[t.id] } : t));
}

export function groupByStatus(tasks: readonly Task[]): Record<TaskStatus, Task[]> {
  const out: Record<TaskStatus, Task[]> = { pending: [], in_progress: [], done: [], cancelled: [] };
  for (const t of tasks) out[t.status].push(t);
  return out;
}
