import { useCallback, useRef, useState } from "react";
import { ApiError, api, type CompareResult } from "@/lib/api";

type Phase = "idle" | "working" | "done" | "error";

export interface CompareState {
  phase: Phase;
  status?: string; // queued | fetching | analyzing | submitting
  progress: number; // 0-100 from the compare job
  detail?: string | null;
  result?: CompareResult;
  error?: string;
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

/** Submit a Battle Lens comparison and poll the job to completion (or short-circuit on a cache hit). */
export function useCompare(onComplete?: () => void) {
  const [state, setState] = useState<CompareState>({ phase: "idle", progress: 0 });
  const timer = useRef<number | undefined>(undefined);

  const stop = () => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = undefined;
  };

  const reset = useCallback(() => {
    stop();
    setState({ phase: "idle", progress: 0 });
  }, []);

  const submit = useCallback(
    async (appAId: string, appBId: string, lookbackDays = 90, customFocus = "") => {
      stop();
      setState({ phase: "working", status: "submitting", progress: 0 });
      try {
        const r = await api.compare(appAId, appBId, lookbackDays, customFocus);
        if (r.cache_hit && r.result) {
          setState({ phase: "done", progress: 100, result: r.result });
          onComplete?.();
          return;
        }
        if (!r.job_id) {
          setState({ phase: "error", progress: 0, error: "No job was created" });
          return;
        }
        const jobId = r.job_id;
        timer.current = window.setInterval(async () => {
          try {
            const j = await api.compareJob(jobId);
            if (j.status === "done" && j.result) {
              stop();
              setState({ phase: "done", progress: 100, result: j.result });
              onComplete?.();
            } else if (j.status === "error") {
              stop();
              setState({ phase: "error", progress: j.progress_percent, error: j.error || "Comparison failed" });
            } else {
              setState({ phase: "working", status: j.status, progress: j.progress_percent, detail: j.progress_detail });
            }
          } catch (e) {
            stop();
            setState({ phase: "error", progress: 0, error: errMsg(e) });
          }
        }, 1200);
      } catch (e) {
        // Quota/trial exhausted: not an error — hand back to idle so the page shows the
        // sign-in gate (anon) or the daily-limit card (signed-in). Refresh /api/me.
        if (e instanceof ApiError && e.status === 429) {
          stop();
          setState({ phase: "idle", progress: 0 });
          onComplete?.();
          return;
        }
        setState({ phase: "error", progress: 0, error: errMsg(e) });
      }
    },
    [onComplete],
  );

  return { state, submit, reset };
}
