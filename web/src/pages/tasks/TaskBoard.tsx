import { TASK_STATUSES, type Task, type TaskStatus } from "../../api/types";
import { Agent, ProjTag } from "../../components/ui";
import { STATUS_LABEL } from "../../lib/labels";
import { PrioritySelect, StatusSelect, type OnEdit } from "./controls";
import { groupByStatus } from "./logic";

export function TaskBoard({ tasks, statuses, onEdit }: { tasks: readonly Task[]; statuses: readonly TaskStatus[]; onEdit: OnEdit }) {
  const groups = groupByStatus(tasks);
  const shown = TASK_STATUSES.filter((s) => statuses.includes(s));
  return (
    <div className="board" style={{ gridTemplateColumns: `repeat(${Math.max(shown.length, 1)}, minmax(200px, 1fr))` }}>
      {shown.map((s) => (
        <section key={s} className="col" aria-label={STATUS_LABEL[s]}>
          <div className="col-h">
            <span className="dotc" style={{ background: `var(--s-${s})` }} />
            {STATUS_LABEL[s]}
            <span className="n">{groups[s].length}</span>
          </div>
          {groups[s].map((t) => (
            <div key={t.id} className="bcard" data-testid="task-card" style={{ ["--sc" as string]: `var(--s-${s})` }}>
              <div className="t">{t.title}</div>
              <div className="m">
                <ProjTag project={t.project} />
                <Agent name={t.assignee} />
              </div>
              <div className="m">
                <StatusSelect task={t} onEdit={onEdit} />
                <PrioritySelect task={t} onEdit={onEdit} />
              </div>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
