import { useCallback, useState } from "react";
import { api } from "../../api/client";
import type { Task, TaskPatch } from "../../api/types";
import { useToast } from "../../lib/toast";
import { patchFor } from "./logic";

/**
 * Inline edits with an optimistic update: the row shows the change at once, the
 * PATCH goes out with the row's own user_id, and a failure puts the row back and
 * says why. A fresh server list replaces every pending overlay.
 */
export function useTaskEdits(serverTasks: readonly Task[] | undefined): {
  edits: Record<string, Partial<Task>>;
  edit: (task: Task, change: Omit<TaskPatch, "user">) => Promise<void>;
} {
  const toast = useToast();
  const [edits, setEdits] = useState<Record<string, Partial<Task>>>({});
  const [seen, setSeen] = useState(serverTasks);
  if (seen !== serverTasks) {
    // A new list from the server: its rows are the truth again (adjusting state while rendering).
    setSeen(serverTasks);
    setEdits({});
  }

  const edit = useCallback(
    async (task: Task, change: Omit<TaskPatch, "user">): Promise<void> => {
      const before = Object.fromEntries(Object.keys(change).map((k) => [k, task[k as keyof Task]])) as Partial<Task>;
      setEdits((e) => ({ ...e, [task.id]: { ...e[task.id], ...change } }));
      try {
        const saved = await api.patchTask(task.id, patchFor(task, change));
        setEdits((e) => ({ ...e, [task.id]: { ...e[task.id], ...saved } }));
      } catch (err) {
        setEdits((e) => ({ ...e, [task.id]: { ...e[task.id], ...before } }));
        toast(`Couldn't update “${task.title}”: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [toast],
  );

  return { edits, edit };
}
