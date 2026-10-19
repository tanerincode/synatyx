import { lineDiff } from "./diff";

describe("lineDiff", () => {
  it("marks added, removed and unchanged lines in order", () => {
    expect(lineDiff("a\nb\nc", "a\nc\nd")).toEqual([
      { op: "ctx", text: "a" },
      { op: "del", text: "b" },
      { op: "ctx", text: "c" },
      { op: "add", text: "d" },
    ]);
  });
  it("is all context for identical input", () => {
    expect(lineDiff("x\ny", "x\ny").every((l) => l.op === "ctx")).toBe(true);
  });
});
