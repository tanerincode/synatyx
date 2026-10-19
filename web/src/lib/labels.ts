import type { BriefStatus, TaskStatus } from "../api/types";

export const STATUS_LABEL: Record<BriefStatus | TaskStatus, string> = {
  draft: "Draft",
  assigned: "Assigned",
  in_progress: "In progress",
  blocked: "Blocked",
  done: "Done",
  cancelled: "Cancelled",
  pending: "Pending",
};
const AGENT_COL = ["#67e8f9", "#a78bfa", "#fb7185", "#34d399", "#fbbf24", "#60a5fa", "#f472b6", "#2dd4bf"];
export const agentColor = (a: string): string => AGENT_COL[[...a].reduce((s, c) => s + c.charCodeAt(0), 0) % AGENT_COL.length] as string;
export const agentInitials = (a: string): string => a.replace(/^[a-z]+-(?=w\d|lead)/, "").slice(0, 2).toUpperCase();
