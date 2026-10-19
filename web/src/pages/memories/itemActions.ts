import type { ItemAction, ItemDetail, RelatedRef } from "../../api/types";

export type Optimistic = Partial<Pick<ItemDetail, "is_pinned" | "is_deprecated">>;

export const DONE: Record<ItemAction, string> = { pin: "Pinned", unpin: "Unpinned", deprecate: "Deprecated", restore: "Restored" };

/** What each action changes locally before the server answers. */
export const optimisticFor = (action: ItemAction): Optimistic =>
  action === "pin" ? { is_pinned: true } : action === "unpin" ? { is_pinned: false } : action === "deprecate" ? { is_deprecated: true } : { is_deprecated: false };

/** Group related edges by relation type, keeping first-seen order. */
export function groupRelated(related: RelatedRef[]): [string, RelatedRef[]][] {
  const groups = new Map<string, RelatedRef[]>();
  related.forEach((r) => groups.set(r.type, [...(groups.get(r.type) ?? []), r]));
  return [...groups.entries()];
}
