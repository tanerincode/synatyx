import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";
import { api } from "../../api/client";
import type { Task } from "../../api/types";
import { ToastContext } from "../../lib/toast";
import { useTaskEdits } from "./useTaskEdits";

vi.mock("../../api/client", () => ({ api: { patchTask: vi.fn() } }));

const row: Task = {
  id: "t1", title: "Ship it", description: "", status: "pending", priority: "medium",
  project: "synatyx", user_id: "kai-bot", assignee: null, created_at: null,
};

function setup() {
  const toast = vi.fn();
  const wrapper = ({ children }: { children: ReactNode }) => <ToastContext.Provider value={toast}>{children}</ToastContext.Provider>;
  const hook = renderHook(({ tasks }) => useTaskEdits(tasks), { wrapper, initialProps: { tasks: [row] as readonly Task[] } });
  return { toast, ...hook };
}

describe("useTaskEdits", () => {
  beforeEach(() => {
    vi.mocked(api.patchTask).mockReset(); // not returned: a returned mock would run as vitest's teardown
  });

  it("shows the change before the server answers and sends the row's own user", async () => {
    let resolve: (t: Task) => void = () => {};
    vi.mocked(api.patchTask).mockReturnValue(new Promise<Task>((r) => (resolve = r)));
    const { result } = setup();

    let pending: Promise<void> = Promise.resolve();
    act(() => {
      pending = result.current.edit(row, { status: "done" });
    });
    expect(result.current.edits.t1?.status).toBe("done");
    expect(api.patchTask).toHaveBeenCalledWith("t1", { user: "kai-bot", status: "done" });

    await act(async () => {
      resolve({ ...row, status: "done" });
      await pending;
    });
    expect(result.current.edits.t1?.status).toBe("done");
  });

  it("rolls back and toasts when the PATCH fails", async () => {
    vi.mocked(api.patchTask).mockImplementation(() => Promise.reject(new Error("task not found")));
    const { result, toast } = setup();

    await act(async () => {
      await result.current.edit(row, { status: "done", priority: "high" });
    });

    expect(result.current.edits.t1).toMatchObject({ status: "pending", priority: "medium" });
    expect(toast).toHaveBeenCalledWith(expect.stringContaining("task not found"));
  });

  it("drops the overlays when a fresh list arrives", async () => {
    vi.mocked(api.patchTask).mockResolvedValue({ ...row, status: "done" });
    const { result, rerender } = setup();
    await act(async () => {
      await result.current.edit(row, { status: "done" });
    });
    expect(result.current.edits.t1).toBeDefined();
    rerender({ tasks: [{ ...row, status: "done" }] });
    expect(result.current.edits).toEqual({});
  });
});
