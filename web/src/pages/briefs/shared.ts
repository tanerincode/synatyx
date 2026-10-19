import { useSearchParams } from "react-router-dom";
import type { Brief, BriefReport, BriefStatus } from "../../api/types";
import { scoped } from "../../lib/scope";

/** Board columns, left to right. Cancelled is opt-in. */
export const BOARD_STATUSES: readonly BriefStatus[] = ["draft", "assigned", "in_progress", "blocked", "done"];

/** Detail URL. Brief ids resolve inside one user's scope, so the owner rides along. */
export function useBriefLink(): (b: Pick<Brief, "id" | "user_id">) => string {
  const [sp] = useSearchParams();
  return (b) => scoped(`/briefs/${b.id}`, sp, { owner: b.user_id });
}

export interface ThreadEntry extends BriefReport {
  /** status before this report, when the report changed it */
  from: BriefStatus | null;
}

/**
 * Reports arrive newest first with only the status *after* each one. Walk them
 * oldest → newest to recover the status each report moved away from.
 */
export function threadWithTransitions(reports: BriefReport[]): ThreadEntry[] {
  let prev: BriefStatus | null = null;
  const oldestFirst = [...reports].reverse().map((r) => {
    const from = r.status && prev !== r.status ? prev : null;
    if (r.status) prev = r.status;
    return { ...r, from };
  });
  return oldestFirst.reverse();
}

/** Assignee suggestions: everyone seen on a brief, plus the current one. */
export function knownAgents(briefs: Brief[] | undefined, current?: string | null): string[] {
  const s = new Set((briefs ?? []).map((b) => b.assignee).filter((a): a is string => !!a));
  if (current) s.add(current);
  return [...s].sort();
}

/** Most common owner among briefs: the default user for a new brief. */
export function defaultOwner(briefs: Brief[] | undefined): string {
  const n = new Map<string, number>();
  (briefs ?? []).forEach((b) => n.set(b.user_id, (n.get(b.user_id) ?? 0) + 1));
  return [...n.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? "";
}

export const BRIEF_TEMPLATES: Record<string, string> = {
  Brief: "# <title>\n\n## Goal\n\nOne sentence.\n\n## Deliver\n\n- \n\n## Rules\n\n- No deploy.\n- Never pkill; stop only your own pids.\n\n## Done when\n\n- ",
  Review: "# Review: <what>\n\nRead-only review for correctness, missed requirements and constraint violations.\n\n## Scope\n\n- PR: \n\n## Report\n\n- Findings with file:line, severity, and a suggested fix.",
  Bug: "# Bug: <symptom>\n\n## Seen\n\n- What happened, where, how often.\n\n## Expected\n\n- \n\n## Done when\n\n- Root cause named, fix merged, regression test added.",
  Blank: "",
};

