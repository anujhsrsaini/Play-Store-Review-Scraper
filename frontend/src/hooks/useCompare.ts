import { useCallback, useEffect, useRef, useState } from "react";
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
  const mounted = useRef(true);
  // Guards setState after unmount (jobs run for minutes; users navigate away)
  // and skips overlapping poll ticks when a request is slower than the interval.
  const inFlight = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (timer.current) window.clearInterval(timer.current);
      timer.current = undefined;
    };
  }, []);

  const stop = () => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = undefined;
  };

  const setLive = (s: CompareState) => {
    if (mounted.current) setState(s);
  };

  const reset = useCallback(() => {
    stop();
    inFlight.current = false;
    setLive({ phase: "idle", progress: 0 });
  }, []);

  const submit = useCallback(
    async (appAId: string, appBId: string, lookbackDays = 90, customFocus = "") => {
      stop();
      inFlight.current = false;
      setLive({ phase: "working", status: "submitting", progress: 0 });
      try {
        const r = await api.compare(appAId, appBId, lookbackDays, customFocus);
        if (r.cache_hit && r.result) {
          setLive({ phase: "done", progress: 100, result: r.result });
          onComplete?.();
          return;
        }
        if (!r.job_id) {
          setLive({ phase: "error", progress: 0, error: "No job was created" });
          return;
        }
        const jobId = r.job_id;
        timer.current = window.setInterval(async () => {
          if (inFlight.current) return; // previous tick still awaiting — skip, don't pile up
          inFlight.current = true;
          try {
            const j = await api.compareJob(jobId);
            if (j.status === "done" && j.result) {
              stop();
              setLive({ phase: "done", progress: 100, result: j.result });
              onComplete?.();
            } else if (j.status === "error") {
              stop();
              setLive({ phase: "error", progress: j.progress_percent, error: j.error || "Comparison failed" });
            } else {
              setLive({ phase: "working", status: j.status, progress: j.progress_percent, detail: j.progress_detail });
            }
          } catch (e) {
            stop();
            setLive({ phase: "error", progress: 0, error: errMsg(e) });
          } finally {
            inFlight.current = false;
          }
        }, 1200);
      } catch (e) {
        // Quota/trial exhausted: not an error — hand back to idle so the page shows the
        // sign-in gate (anon) or the daily-limit card (signed-in). Refresh /api/me.
        if (e instanceof ApiError && e.status === 429) {
          stop();
          setLive({ phase: "idle", progress: 0 });
          onComplete?.();
          return;
        }
        setLive({ phase: "error", progress: 0, error: errMsg(e) });
      }
    },
    [onComplete],
  );

  return { state, submit, reset };
}
