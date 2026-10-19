import type { Task } from "../../api/types";
import {
  countOpen,
  DEFAULT_STATUSES,
  filterTasks,
  groupByStatus,
  parsePriority,
  parseStatuses,
  patchFor,
  serializeStatuses,
  toggleStatus,
  UNASSIGNED,
  withEdits,
  type TaskFilters,
} from "./logic";

const task = (over: Partial<Task>): Task => ({
  id: "t1",
  title: "A task",
  description: "",
  status: "pending",
  priority: "medium",
  project: "synatyx",
  user_id: "tom",
  assignee: null,
  created_at: null,
  ...over,
});

const NO_FILTER: TaskFilters = { statuses: ["pending", "in_progress", "done", "cancelled"], priority: "", assignee: "", project: "", user: "" };

describe("status filter", () => {
  it("defaults to everything but cancelled", () => {
    expect(parseStatuses("")).toEqual(DEFAULT_STATUSES);
    expect(parseStatuses("nonsense")).toEqual(DEFAULT_STATUSES);
  });
  it("parses a list in canonical order and drops unknown values", () => {
    expect(parseStatuses("done,cancelled,bogus")).toEqual(["done", "cancelled"]);
  });
  it("round-trips, writing nothing for the default", () => {
    expect(serializeStatuses(DEFAULT_STATUSES)).toBeNull();
    expect(serializeStatuses(["done", "cancelled"])).toBe("done,cancelled");
  });
  it("toggles a chip but never empties the set", () => {
    expect(toggleStatus(["pending", "done"], "cancelled")).toEqual(["pending", "done", "cancelled"]);
    expect(toggleStatus(["pending", "done"], "done")).toEqual(["pending"]);
    expect(toggleStatus(["pending"], "pending")).toEqual(["pending"]);
  });
});

describe("filterTasks", () => {
  const rows = [
    task({ id: "a", status: "pending", priority: "high", assignee: "synatyx-w2" }),
    task({ id: "b", status: "done", priority: "low", project: "kai", user_id: "kai-bot" }),
    task({ id: "c", status: "cancelled", priority: "low" }),
  ];
  it("filters by status, priority, project and user", () => {
    const ids = (f: Partial<TaskFilters>): string[] => filterTasks(rows, { ...NO_FILTER, ...f }).map((t) => t.id);
    expect(ids({})).toEqual(["a", "b", "c"]);
    expect(ids({ statuses: DEFAULT_STATUSES })).toEqual(["a", "b"]);
    expect(ids({ priority: "low" })).toEqual(["b", "c"]);
    expect(ids({ project: "kai" })).toEqual(["b"]);
    expect(ids({ user: "kai-bot" })).toEqual(["b"]);
  });
  it("filters by assignee, including unassigned", () => {
    const ids = (assignee: string): string[] => filterTasks(rows, { ...NO_FILTER, assignee }).map((t) => t.id);
    expect(ids("synatyx-w2")).toEqual(["a"]);
    expect(ids(UNASSIGNED)).toEqual(["b", "c"]);
  });
  it("counts open tasks", () => {
    expect(countOpen(rows)).toBe(1);
  });
  it("validates priority", () => {
    expect(parsePriority("high")).toBe("high");
    expect(parsePriority("urgent")).toBe("");
  });
});

describe("edits", () => {
  it("takes the user from the row, not from any filter", () => {
    expect(patchFor(task({ user_id: "kai-bot" }), { status: "done" })).toEqual({ user: "kai-bot", status: "done" });
  });
  it("overlays optimistic edits without touching other rows", () => {
    const rows = [task({ id: "a" }), task({ id: "b" })];
    const shown = withEdits(rows, { a: { status: "done" } });
    expect(shown[0]?.status).toBe("done");
    expect(shown[1]).toBe(rows[1]);
    expect(rows[0]?.status).toBe("pending");
  });
  it("groups by status", () => {
    const g = groupByStatus([task({ id: "a" }), task({ id: "b", status: "done" })]);
    expect(g.pending).toHaveLength(1);
    expect(g.done).toHaveLength(1);
    expect(g.cancelled).toHaveLength(0);
  });
});
