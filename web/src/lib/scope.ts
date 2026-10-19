import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

/** Global project/user filter, kept in the URL query so views are shareable. */
export function useScope(): {
  project: string;
  user: string;
  setScope: (patch: { project?: string; user?: string }) => void;
  param: (k: string) => string;
  setParams: (patch: Record<string, string | null>) => void;
} {
  const [sp, setSp] = useSearchParams();
  const setParams = useCallback(
    (patch: Record<string, string | null>) => {
      setSp(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (v === null || v === "") next.delete(k);
            else next.set(k, v);
          }
          return next;
        },
        { replace: false },
      );
    },
    [setSp],
  );
  const setScope = useCallback(
    (patch: { project?: string; user?: string }) => setParams({ ...patch, page: null, item: null }),
    [setParams],
  );
  return {
    project: sp.get("project") ?? "",
    user: sp.get("user") ?? "",
    setScope,
    param: (k: string) => sp.get(k) ?? "",
    setParams,
  };
}

/** Link target that keeps project/user. */
export function scoped(path: string, sp: URLSearchParams, extra: Record<string, string> = {}): string {
  const q = new URLSearchParams();
  const p = sp.get("project");
  const u = sp.get("user");
  if (p) q.set("project", p);
  if (u) q.set("user", u);
  for (const [k, v] of Object.entries(extra)) if (v) q.set(k, v);
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}
