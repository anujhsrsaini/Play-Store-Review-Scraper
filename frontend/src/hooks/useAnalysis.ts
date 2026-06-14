import { useCallback, useRef, useState } from "react";
import { ApiError, api, type AnalysisResult } from "@/lib/api";

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

  const stop = () => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = undefined;
  };

  const reset = useCallback(() => {
    stop();
    setState({ phase: "idle", progress: 0 });
  }, []);

  const submit = useCallback(
    async (appId: string, question: string) => {
      stop();
      setState({ phase: "working", status: "submitting", progress: 0 });
      try {
        const r = await api.analyze(appId, question);
        if (r.cache_hit && r.result) {
          setState({ phase: "done", progress: 0, result: r.result });
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
            const j = await api.job(jobId);
            if (j.status === "done" && j.result) {
              stop();
              setState({ phase: "done", progress: j.progress, result: j.result });
              onComplete?.();
            } else if (j.status === "error") {
              stop();
              setState({ phase: "error", progress: j.progress, error: j.error || "Analysis failed" });
            } else {
              setState({ phase: "working", status: j.status, progress: j.progress });
            }
          } catch (e) {
            stop();
            setState({ phase: "error", progress: 0, error: errMsg(e) });
          }
        }, 1200);
      } catch (e) {
        setState({ phase: "error", progress: 0, error: errMsg(e) });
      }
    },
    [onComplete],
  );

  return { state, submit, reset };
}
