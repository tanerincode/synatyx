import type { Brief, BriefReport } from "../../api/types";
import { defaultOwner, knownAgents, threadWithTransitions } from "./shared";

const rep = (agent: string, status: BriefReport["status"], at: string): BriefReport => ({ agent, status, text: `${agent} ${status ?? "note"}`, at });

describe("threadWithTransitions", () => {
  it("recovers the status each report moved from, keeping newest first", () => {
    const newestFirst = [
      rep("w4", null, "2026-10-19T05:00:00Z"),
      rep("w4", "blocked", "2026-10-19T04:00:00Z"),
      rep("w4", "in_progress", "2026-10-19T03:00:00Z"),
      rep("lead", "assigned", "2026-10-19T02:00:00Z"),
      rep("kai", "draft", "2026-10-19T01:00:00Z"),
    ];
    expect(threadWithTransitions(newestFirst).map((r) => [r.status, r.from])).toEqual([
      [null, null],
      ["blocked", "in_progress"],
      ["in_progress", "assigned"],
      ["assigned", "draft"],
      ["draft", null],
    ]);
  });
  it("shows no transition when a report repeats the current status", () => {
    const t = threadWithTransitions([rep("b", "assigned", "2"), rep("a", "assigned", "1")]);
    expect(t.map((r) => r.from)).toEqual([null, null]);
  });
});

describe("helpers", () => {
  const brief = (assignee: string | null, user_id: string): Brief => ({
    id: assignee ?? "x", slug: "s", title: "t", project: "p", assignee, status: "draft", version: 1, user_id, updated_at: null, last_report: null,
  });
  it("lists known agents, sorted, including the current one", () => {
    expect(knownAgents([brief("w2", "u"), brief(null, "u"), brief("kai", "u"), brief("w2", "u")], "w9")).toEqual(["kai", "w2", "w9"]);
  });
  it("picks the most common owner", () => {
    expect(defaultOwner([brief("a", "tom"), brief("b", "ci"), brief("c", "tom")])).toBe("tom");
    expect(defaultOwner([])).toBe("");
  });
});
