import { AlertCircle } from "lucide-react";
import type { AnalysisState } from "@/hooks/useAnalysis";
import { Card, Spinner } from "./ui";

const LABELS: Record<string, string> = {
  submitting: "Submitting…",
  queued: "Queued…",
  scraping: "Fetching reviews…",
  analyzing: "Analyzing reviews…",
};

const FRIENDLY_ERRORS: Record<string, string> = {
  AppNotFound: "That app couldn't be found on the Play Store.",
  ScraperUnavailable: "Couldn't fetch reviews right now (the source may be rate-limiting). Try again shortly.",
  RateLimitedUpstream: "The Play Store is rate-limiting requests right now. Try again in a bit.",
  ScrapeTimeout: "Fetching reviews timed out. Try a smaller app or again later.",
};

export function Progress({ state }: { state: AnalysisState }) {
  if (state.phase === "error") {
    return (
      <Card className="flex items-start gap-3 border-rose-400/30 bg-rose-500/10 p-4">
        <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-rose-300" />
        <div>
          <div className="font-medium text-rose-200">Couldn't complete the analysis</div>
          <div className="text-sm text-rose-300/80">
            {FRIENDLY_ERRORS[state.error ?? ""] ?? state.error ?? "Unknown error"}
          </div>
        </div>
      </Card>
    );
  }

  const label = LABELS[state.status ?? "submitting"] ?? "Working…";
  return (
    <Card className="flex items-center gap-3 p-4">
      <Spinner className="h-5 w-5" />
      <span className="text-sm text-slate-200">
        {label}
        {state.status === "scraping" && state.progress > 0 && (
          <span className="text-slate-400"> · {state.progress} reviews so far</span>
        )}
      </span>
    </Card>
  );
}
