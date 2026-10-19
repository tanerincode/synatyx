import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";

/** Bumped by the top-bar refresh; only mounted pages refetch. */
export const RefreshContext = createContext<{ tick: number; bump: () => void; syncedAt: number }>({
  tick: 0,
  bump: () => {},
  syncedAt: 0,
});

export interface ApiState<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  reload: () => void;
}

interface Settled<T> {
  key: string;
  req: string;
  data?: T;
  error?: Error;
}

/**
 * Fetch on mount and whenever `key` or the global refresh tick changes. `key`
 * must encode every input of `fn`; pass null to skip fetching. While a reload
 * of the same key is in flight the previous data stays visible.
 */
export function useApi<T>(key: string | null, fn: () => Promise<T>): ApiState<T> {
  const { tick } = useContext(RefreshContext);
  const fnRef = useRef(fn);
  useLayoutEffect(() => {
    fnRef.current = fn;
  });
  const [local, setLocal] = useState(0);
  const [settled, setSettled] = useState<Settled<T> | null>(null);
  const req = key === null ? null : `${key}#${tick}#${local}`;

  useEffect(() => {
    if (key === null || req === null) return;
    let live = true;
    fnRef.current().then(
      (data) => {
        if (live) setSettled({ key, req, data });
      },
      (error: unknown) => {
        if (live) setSettled({ key, req, error: error instanceof Error ? error : new Error(String(error)) });
      },
    );
    return () => {
      live = false;
    };
  }, [key, req]);

  const reload = useCallback(() => setLocal((n) => n + 1), []);
  const sameKey = settled !== null && settled.key === key;
  const current = sameKey && settled.req === req;
  return {
    data: sameKey ? settled.data : undefined,
    error: current ? settled.error : undefined,
    loading: req !== null && !current,
    reload,
  };
}
