import { TASK_STATUSES, type Priority, type Task, type TaskPatch, type TaskStatus } from "../../api/types";
import { STATUS_LABEL } from "../../lib/labels";
import { PRIORITIES } from "./logic";

const PRIORITY_COLOR: Record<Priority, string> = { high: "var(--rose)", medium: "var(--warn)", low: "var(--muted)" };
const SMALL_SELECT = { padding: "2px 24px 2px 8px", fontSize: 12 } as const;

export type OnEdit = (task: Task, change: Omit<TaskPatch, "user">) => void;

export function PriorityDot({ priority }: { priority: Priority }) {
  return (
    <span className="row small" style={{ gap: 5 }}>
      <span className="dotc" style={{ background: PRIORITY_COLOR[priority] }} />
      {priority}
    </span>
  );
}

export function StatusSelect({ task, onEdit }: { task: Task; onEdit: OnEdit }) {
  return (
    <select
      className="select"
      style={SMALL_SELECT}
      aria-label={`Status of ${task.title}`}
      value={task.status}
      onChange={(e) => onEdit(task, { status: e.target.value as TaskStatus })}
    >
      {TASK_STATUSES.map((s) => (
        <option key={s} value={s}>
          {STATUS_LABEL[s]}
        </option>
      ))}
    </select>
  );
}

export function PrioritySelect({ task, onEdit }: { task: Task; onEdit: OnEdit }) {
  return (
    <select
      className="select"
      style={SMALL_SELECT}
      aria-label={`Priority of ${task.title}`}
      value={task.priority}
      onChange={(e) => onEdit(task, { priority: e.target.value as Priority })}
    >
      {PRIORITIES.map((p) => (
        <option key={p} value={p}>
          {p}
        </option>
      ))}
    </select>
  );
}

export function AssigneeSelect({ task, assignees, onEdit }: { task: Task; assignees: readonly string[]; onEdit: OnEdit }) {
  const current = task.assignee ?? "";
  const options = current && !assignees.includes(current) ? [...assignees, current] : assignees;
  return (
    <select
      className="select"
      style={SMALL_SELECT}
      aria-label={`Assignee of ${task.title}`}
      value={current}
      onChange={(e) => onEdit(task, { assignee: e.target.value })}
    >
      <option value="">unassigned</option>
      {options.map((a) => (
        <option key={a} value={a}>
          {a}
        </option>
      ))}
    </select>
  );
}
