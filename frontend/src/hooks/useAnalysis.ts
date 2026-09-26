import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type AnalysisResult, type Period } from "@/lib/api";

type Phase = "idle" | "working" | "done" | "error";

export interface AnalysisState {
  phase: Phase;
  status?: string; // queued | scraping | analyzing | submitting
  progress: number; // reviews fetched so far
  result?: AnalysisResult;
  error?: string;
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong";
}

/** Submit an analysis and poll the job to completion (or short-circuit on a cache hit). */
export function useAnalysis(onComplete?: () => void) {
  const [state, setState] = useState<AnalysisState>({ phase: "idle", progress: 0 });
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

  const setLive = (s: AnalysisState) => {
    if (mounted.current) setState(s);
  };

  const reset = useCallback(() => {
    stop();
    inFlight.current = false;
    setLive({ phase: "idle", progress: 0 });
  }, []);

  const submit = useCallback(
    async (appId: string, question: string, period: Period = "90d") => {
      stop();
      inFlight.current = false;
      setLive({ phase: "working", status: "submitting", progress: 0 });
      try {
        const r = await api.analyze(appId, question, period);
        if (r.cache_hit && r.result) {
          setLive({ phase: "done", progress: 0, result: r.result });
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
            const j = await api.job(jobId);
            if (j.status === "done" && j.result) {
              stop();
              setLive({ phase: "done", progress: j.progress, result: j.result });
              onComplete?.();
            } else if (j.status === "error") {
              stop();
              setLive({ phase: "error", progress: j.progress, error: j.error || "Analysis failed" });
            } else {
              setLive({ phase: "working", status: j.status, progress: j.progress });
            }
          } catch (e) {
            stop();
            setLive({ phase: "error", progress: 0, error: errMsg(e) });
          } finally {
            inFlight.current = false;
          }
        }, 1200);
      } catch (e) {
        // Quota/trial exhausted: not an error — hand back to idle so Home shows the
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
