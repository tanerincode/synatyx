import type { Skill } from "../../api/types";

export const skillKey = (s: Pick<Skill, "slug" | "user_id">): string => `${s.user_id}/${s.slug}`;

/** Case-insensitive match on name, slug and description; blank shows everything. */
export function searchSkills(skills: readonly Skill[], query: string): Skill[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...skills];
  return skills.filter((s) => [s.name, s.slug, s.description].some((f) => f.toLowerCase().includes(q)));
}

/**
 * The skill the URL points at (`skill` = slug, `owner` = user_id; a slug is only
 * unique per user), else the first one shown.
 */
export function pickSkill<T extends Pick<Skill, "slug" | "user_id">>(skills: readonly T[], slug: string, owner: string): T | undefined {
  return skills.find((s) => s.slug === slug && (!owner || s.user_id === owner)) ?? skills[0];
}
