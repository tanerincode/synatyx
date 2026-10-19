import { useMemo } from "react";
import { api } from "../api/client";
import { TASK_STATUSES, type TaskStatus } from "../api/types";
import { EmptyState, ErrorState } from "../components/ui";
import { STATUS_LABEL } from "../lib/labels";
import { useScope } from "../lib/scope";
import { useApi } from "../lib/useApi";
import { TaskBoard } from "./tasks/TaskBoard";
import { TaskTable } from "./tasks/TaskTable";
import {
  assigneesOf,
  countOpen,
  filterTasks,
  parsePriority,
  parseStatuses,
  PRIORITIES,
  serializeStatuses,
  toggleStatus,
  UNASSIGNED,
  withEdits,
} from "./tasks/logic";
import { useTaskEdits } from "./tasks/useTaskEdits";

export default function TasksPage() {
  const { project, user, param, setParams } = useScope();
  const all = useApi("tasks:all", () => api.tasks("all"));
  const { edits, edit } = useTaskEdits(all.data?.tasks);

  const statuses = parseStatuses(param("status"));
  const priority = parsePriority(param("priority"));
  const assignee = param("assignee");
  const view = param("view") === "board" ? "board" : "table";

  const tasks = useMemo(() => withEdits(all.data?.tasks ?? [], edits), [all.data, edits]);
  // The global project/user switcher scopes the page; the chips and selects narrow it further.
  const inScope = useMemo(() => filterTasks(tasks, { statuses: TASK_STATUSES, priority: "", assignee: "", project, user }), [tasks, project, user]);
  const shown = useMemo(() => filterTasks(tasks, { statuses, priority, assignee, project, user }), [tasks, statuses, priority, assignee, project, user]);
  const assignees = useMemo(() => assigneesOf(all.data?.tasks ?? []), [all.data]);

  const onToggle = (s: TaskStatus): void => setParams({ status: serializeStatuses(toggleStatus(statuses, s)) });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Tasks</h1>
          <p>{all.data ? `${countOpen(inScope)} open of ${inScope.length}` : "Tasks across projects and users"}</p>
        </div>
      </div>

      <div className="toolbar">
        <div className="seg" role="group" aria-label="View">
          <button className={view === "table" ? "on" : ""} aria-pressed={view === "table"} onClick={() => setParams({ view: null })}>
            Table
          </button>
          <button className={view === "board" ? "on" : ""} aria-pressed={view === "board"} onClick={() => setParams({ view: "board" })}>
            Board
          </button>
        </div>
        <span className="chipset" role="group" aria-label="Status filter">
          {TASK_STATUSES.map((s) => (
            <button key={s} className={`chip-toggle ${statuses.includes(s) ? "on" : ""}`} aria-pressed={statuses.includes(s)} onClick={() => onToggle(s)}>
              <span className="dotc" style={{ background: `var(--s-${s})` }} />
              {STATUS_LABEL[s]}
            </button>
          ))}
        </span>
        <select className="select" aria-label="Priority filter" value={priority} onChange={(e) => setParams({ priority: e.target.value })}>
          <option value="">Any priority</option>
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <select className="select" aria-label="Assignee filter" value={assignee} onChange={(e) => setParams({ assignee: e.target.value })}>
          <option value="">Any assignee</option>
          <option value={UNASSIGNED}>Unassigned</option>
          {assignees.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
      </div>

      {all.error ? (
        <ErrorState error={all.error} onRetry={all.reload} what="tasks" />
      ) : all.loading && !all.data ? (
        <TaskTable tasks={[]} assignees={[]} onEdit={edit} loading />
      ) : shown.length === 0 ? (
        <EmptyState icon="tasks" title={tasks.length === 0 ? "No tasks yet" : "No tasks match these filters"}>
          {tasks.length === 0 ? "Agents add tasks with context_task_add; they appear here." : "Widen the status chips or clear the priority, assignee, project or user filter."}
        </EmptyState>
      ) : view === "board" ? (
        <TaskBoard tasks={shown} statuses={statuses} onEdit={edit} />
      ) : (
        <TaskTable tasks={shown} assignees={assignees} onEdit={edit} />
      )}
    </>
  );
}
