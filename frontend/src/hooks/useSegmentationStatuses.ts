/**
 * Fetch segmentation status for a set of document ids.
 *
 * Why this exists: `DocumentResponse` (the list payload) does **not** include
 * segmentation state — the backend exposes it only at
 * `GET /v1/documents/{id}/segmentation`. This hook therefore enriches a page of
 * documents with a bounded amount of parallel requests.
 *
 * Failures are tolerated: a document whose status could not be fetched simply
 * stays `null` and renders as "Unknown". Nothing is guessed.
 */

import { useEffect, useRef, useState } from "react";
import * as api from "@/services/api";
import type { SegmentationStatus } from "@/types/api";

const MAX_CONCURRENCY = 4;

export interface SegmentationStatuses {
  statuses: Record<string, SegmentationStatus>;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useSegmentationStatuses(ids: string[]): SegmentationStatuses {
  const [statuses, setStatuses] = useState<Record<string, SegmentationStatus>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const cache = useRef(statuses);
  cache.current = statuses;
  const key = ids.join(",");

  useEffect(() => {
    const list = key ? key.split(",") : [];
    if (list.length === 0) {
      setStatuses({});
      setLoading(false);
      setError(null);
      return undefined;
    }

    const controller = new AbortController();
    let cancelled = false;
    setLoading(true);
    setError(null);

    // Preserve anything already known so a refresh does not blank the UI.
    const next: Record<string, SegmentationStatus> = { ...cache.current };

    async function run(): Promise<void> {
      const queue = [...list];
      const failures: string[] = [];

      async function worker(): Promise<void> {
        while (!cancelled && controller.signal.aborted === false) {
          const id = queue.shift();
          if (!id) return;
          try {
            const result = await api.getSegmentationStatus(id, controller.signal);
            if (cancelled) return;
            next[id] = result;
          } catch (err) {
            const aborted =
              (err instanceof DOMException && err.name === "AbortError") ||
              (typeof err === "object" && err !== null && (err as { code?: string }).code === "aborted");
            if (aborted) return;
            failures.push(id);
          }
        }
      }

      await Promise.all(Array.from({ length: Math.min(MAX_CONCURRENCY, list.length) }, worker));
      if (cancelled) return;
      setStatuses(next);
      setError(failures.length > 0 && failures.length === list.length ? "Segmentation status unavailable." : null);
      setLoading(false);
    }

    void run();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [key, nonce]);

  return {
    statuses,
    loading,
    error,
    reload: () => setNonce((n) => n + 1),
  };
}
