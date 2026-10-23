/**
 * Shared data-loading hook.
 *
 * Guarantees the behaviours required for a reliable demo:
 *   - explicit idle / loading / error / success states for every view
 *   - stale responses never overwrite newer state (request sequencing)
 *   - requests are aborted on unmount
 *   - no overlapping duplicate requests while one is in flight
 *   - errors do not erase previously valid data
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  /** True only on the first load; refreshes keep showing prior data. */
  initialLoading: boolean;
  /** True while a background refresh is running over existing data. */
  refreshing: boolean;
  reload: () => void;
  setData: (updater: T | ((prev: T | null) => T | null)) => void;
}

export function useAsyncData<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  options: { enabled?: boolean } = {},
): AsyncState<T> {
  const enabled = options.enabled ?? true;

  const [data, setDataState] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(enabled);
  const [hasLoaded, setHasLoaded] = useState(false);

  const seq = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      abortRef.current?.abort();
    };
  }, []);

  const run = useCallback(() => {
    if (!enabled) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const mySeq = ++seq.current;

    setLoading(true);
    setError(null);

    loaderRef
      .current(controller.signal)
      .then((result) => {
        // Ignore out-of-order or cancelled responses.
        if (!mounted.current || controller.signal.aborted || mySeq !== seq.current) return;
        setDataState(result);
        setHasLoaded(true);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!mounted.current || controller.signal.aborted || mySeq !== seq.current) return;
        // Aborts are not user-visible failures.
        const aborted =
          (err instanceof DOMException && err.name === "AbortError") ||
          (typeof err === "object" && err !== null && (err as { code?: string }).code === "aborted");
        if (!aborted) setError(err);
      })
      .finally(() => {
        if (!mounted.current || mySeq !== seq.current) return;
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return undefined;
    }
    run();
    return () => {
      abortRef.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps]);

  const setData = useCallback((updater: T | ((prev: T | null) => T | null)) => {
    setDataState((prev) =>
      typeof updater === "function" ? ((updater as (p: T | null) => T | null)(prev) as T) : updater,
    );
  }, []);

  return {
    data,
    error,
    loading,
    initialLoading: loading && !hasLoaded,
    refreshing: loading && hasLoaded,
    reload: run,
    setData,
  };
}

/**
 * Conservative polling for lifecycle status.
 *
 * - stops at a terminal state or when `active` becomes false
 * - never overlaps: the next tick is scheduled only after the current request
 *   settles, so a slow backend cannot accumulate queued requests
 * - aborts on unmount
 * - a transient error keeps the last known value and simply retries later
 */
export function usePolling(callback: () => Promise<unknown>, intervalMs: number, active: boolean) {
  const saved = useRef(callback);
  saved.current = callback;
  const busy = useRef(false);

  useEffect(() => {
    if (!active) return undefined;
    let cancelled = false;
    // DOM timers return numbers; be explicit so @types/node's NodeJS.Timeout
    // (a different type) never mismatches against `window.setTimeout`.
    let timer: number | undefined;

    const tick = async () => {
      if (cancelled || busy.current) return;
      busy.current = true;
      try {
        await saved.current();
      } finally {
        busy.current = false;
        if (!cancelled) timer = window.setTimeout(tick, intervalMs);
      }
    };

    timer = window.setTimeout(tick, intervalMs);

    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [active, intervalMs]);
}
