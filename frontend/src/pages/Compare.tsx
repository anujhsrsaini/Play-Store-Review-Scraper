import { ArrowLeftRight } from "lucide-react";
import { useCallback, useState } from "react";
import { CompareResultView } from "@/components/CompareResult";
import { Search } from "@/components/Search";
import { SignInUpsell } from "@/components/SignInUpsell";
import { Button, Card, Input } from "@/components/ui";
import { useCompare } from "@/hooks/useCompare";
import type { AppSummary, Me } from "@/lib/api";

const LOOKBACKS = [30, 60, 90, 180, 365] as const;

export function Compare({ me, authEnabled, onUsed }: { me: Me | null; authEnabled: boolean; onUsed: () => void }) {
  const [appA, setAppA] = useState<AppSummary | null>(null);
  const [appB, setAppB] = useState<AppSummary | null>(null);
  const [focus, setFocus] = useState("");
  const [lookback, setLookback] = useState<number>(90);
  const [picking, setPicking] = useState<"a" | "b">("a");
  const onComplete = useCallback(() => onUsed(), [onUsed]);
  const { state, submit, reset } = useCompare(onComplete);

  if (authEnabled && !me) {
    return (
      <Card className="mx-auto mt-12 max-w-md p-8 text-center">
        <h1 className="text-xl font-semibold text-white">Battle Lens</h1>
        <p className="mt-2 text-sm text-slate-400">Sign in to compare two apps side by side.</p>
        <a href="/auth/login" className="mt-6 inline-block">
          <Button size="lg">Sign in with Google</Button>
        </a>
      </Card>
    );
  }

  const isAnon = me?.is_anon === true;
  const quotaExhausted = me != null && me.remaining <= 0;
  const showResult = state.phase === "done" && state.result;
  const canSubmit = appA && appB && appA.app_id !== appB.app_id && state.phase !== "working";

  const pick = (slot: "a" | "b") => (a: AppSummary) => {
    if (slot === "a") setAppA(a);
    else setAppB(a);
    reset();
  };

  const onSubmit = () => {
    if (!canSubmit || !appA || !appB) return;
    submit(appA.app_id, appB.app_id, lookback, focus.trim());
  };

  return (
    <div className="space-y-5">
      <div className="pb-2 pt-4 text-center">
        <span className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs text-slate-300">
          <ArrowLeftRight className="h-3.5 w-3.5" /> Battle Lens · side-by-side comparison
        </span>
        <h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">
          Which app do users actually prefer?
        </h1>
        <p className="mx-auto mt-2 max-w-xl text-sm leading-relaxed text-slate-400">
          Pick two apps and an angle — get a verdict grounded in both review corpora, with every
          claim cited to its side.
        </p>
      </div>

      <Card className="p-4 sm:p-5">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-slate-500">
              App A {appA && <span className="text-slate-200">· {appA.title ?? appA.app_id}</span>}
            </div>
            {picking === "a" ? (
              <Search onPick={pick("a")} />
            ) : (
              <Button variant="ghost" size="sm" onClick={() => setPicking("a")}>
                {appA ? "Change app A" : "Pick app A"}
              </Button>
            )}
          </div>
          <div>
            <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-slate-500">
              App B {appB && <span className="text-slate-200">· {appB.title ?? appB.app_id}</span>}
            </div>
            {picking === "b" ? (
              <Search onPick={pick("b")} />
            ) : (
              <Button variant="ghost" size="sm" onClick={() => setPicking("b")}>
                {appB ? "Change app B" : "Pick app B"}
              </Button>
            )}
          </div>
        </div>

        <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_auto]">
          <Input
            value={focus}
            onChange={(e) => setFocus(e.target.value)}
            placeholder="Angle (optional) — “battery life”, “onboarding”…"
            maxLength={500}
          />
          <select
            value={lookback}
            onChange={(e) => setLookback(Number(e.target.value))}
            className="rounded-xl border border-white/10 bg-white/[0.04] px-3 text-sm text-slate-100"
            aria-label="Lookback window"
          >
            {LOOKBACKS.map((d) => (
              <option key={d} value={d}>
                last {d} days
              </option>
            ))}
          </select>
        </div>

        <div className="mt-3 flex items-center gap-2">
          <Button onClick={onSubmit} disabled={!canSubmit} loading={state.phase === "working"}>
            <ArrowLeftRight className="h-4 w-4" /> Compare
          </Button>
          {appA && appB && appA.app_id === appB.app_id && (
            <span className="text-xs text-amber-300">Pick two different apps.</span>
          )}
        </div>
      </Card>

      {quotaExhausted && isAnon && <SignInUpsell variant="gate" />}

      {state.phase === "working" && (
        <Card className="p-5">
          <div className="flex items-center justify-between text-sm text-slate-300">
            <span>{state.status === "submitting" ? "Submitting…" : (state.detail ?? state.status ?? "Working…")}</span>
            <span className="tabular-nums text-slate-500">{state.progress}%</span>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/10">
            <div className="h-full rounded-full bg-brand-grad transition-all" style={{ width: `${Math.max(4, state.progress)}%` }} />
          </div>
        </Card>
      )}
      {state.phase === "error" && (
        <Card className="p-5 text-sm text-rose-300">{state.error ?? "Comparison failed"}</Card>
      )}
      {showResult && state.result && <CompareResultView result={state.result} onCompareAnother={reset} />}
      {showResult && isAnon && <SignInUpsell variant="soft" />}
    </div>
  );
}
