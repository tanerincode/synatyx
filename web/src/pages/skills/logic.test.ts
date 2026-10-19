import type { Skill } from "../../api/types";
import { pickSkill, searchSkills, skillKey } from "./logic";

const skill = (slug: string, over: Partial<Skill> = {}): Skill => ({
  slug, name: slug.toUpperCase(), description: `does ${slug}`, project: null, user_id: "tom", updated_at: null, ...over,
});

describe("searchSkills", () => {
  const rows = [skill("deploy", { description: "ship to prod" }), skill("tests", { name: "Write Tests" })];
  it("matches name, slug and description, ignoring case", () => {
    expect(searchSkills(rows, "SHIP").map((s) => s.slug)).toEqual(["deploy"]);
    expect(searchSkills(rows, "write").map((s) => s.slug)).toEqual(["tests"]);
    expect(searchSkills(rows, "deploy").map((s) => s.slug)).toEqual(["deploy"]);
  });
  it("shows everything for a blank query", () => {
    expect(searchSkills(rows, "  ")).toHaveLength(2);
  });
});

describe("pickSkill", () => {
  const rows = [skill("a"), skill("b"), skill("b", { user_id: "kai" })];
  it("selects by slug and owner", () => {
    expect(pickSkill(rows, "b", "kai")?.user_id).toBe("kai");
    expect(pickSkill(rows, "b", "")?.user_id).toBe("tom");
  });
  it("falls back to the first skill for an unknown or missing slug", () => {
    expect(pickSkill(rows, "gone", "")?.slug).toBe("a");
    expect(pickSkill(rows, "", "")?.slug).toBe("a");
    expect(pickSkill([], "a", "")).toBeUndefined();
  });
  it("keys a skill by owner and slug", () => {
    expect(skillKey(rows[2] as Skill)).toBe("kai/b");
  });
});
