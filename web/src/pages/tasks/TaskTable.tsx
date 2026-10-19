import type { Task } from "../../api/types";
import { ProjTag, SkeletonRows } from "../../components/ui";
import { ago } from "../../lib/format";
import { AssigneeSelect, PrioritySelect, StatusSelect, type OnEdit } from "./controls";

export function TaskTable({ tasks, assignees, onEdit, loading }: { tasks: readonly Task[]; assignees: readonly string[]; onEdit: OnEdit; loading?: boolean }) {
  return (
    <div className="card flush table-wrap">
      <table aria-label="Tasks">
        <thead>
          <tr>
            <th>Title</th>
            <th>Status</th>
            <th>Priority</th>
            <th>Project</th>
            <th>Assignee</th>
            <th className="right">Created</th>
          </tr>
        </thead>
        {loading ? (
          <SkeletonRows rows={8} cols={6} />
        ) : (
          <tbody>
            {tasks.map((t) => (
              <tr key={t.id} data-testid="task-row">
                <td style={{ color: "var(--ink)" }}>
                  {t.title}
                  {t.description && <div className="muted small clamp2">{t.description}</div>}
                </td>
                <td>
                  <StatusSelect task={t} onEdit={onEdit} />
                </td>
                <td>
                  <PrioritySelect task={t} onEdit={onEdit} />
                </td>
                <td>
                  <ProjTag project={t.project} />
                </td>
                <td>
                  <AssigneeSelect task={t} assignees={assignees} onEdit={onEdit} />
                </td>
                <td className="right num muted">{ago(t.created_at)}</td>
              </tr>
            ))}
          </tbody>
        )}
      </table>
    </div>
  );
}
