import { ArrowLeftRight } from "lucide-react";
import { useCallback, useState } from "react";
import { CompareResultView } from "@/components/CompareResult";
import { Pricing } from "@/components/Pricing";
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
  const [picking, setPicking] = useState<"a" | "b" | "done">("a");
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
  const canSubmit =
    appA &&
    appB &&
    appA.app_id !== appB.app_id &&
    state.phase !== "working" &&
    !quotaExhausted;

  const pick = (slot: "a" | "b") => (a: AppSummary) => {
    const nextA = slot === "a" ? a : appA;
    const nextB = slot === "b" ? a : appB;
    if (slot === "a") setAppA(a);
    else setAppB(a);
    reset();
    // Auto-advance: after picking A move to B; when both are set, collapse
    // the pickers into confirmation chips.
    if (nextA && nextB) setPicking("done");
    else setPicking(slot === "a" ? "b" : "a");
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
          <SlotPicker
            label="App A"
            app={appA}
            open={picking === "a"}
            onPick={pick("a")}
            onChange={() => setPicking("a")}
          />
          <SlotPicker
            label="App B"
            app={appB}
            open={picking === "b"}
            onPick={pick("b")}
            onChange={() => setPicking("b")}
          />
        </div>

        <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_auto]">
          <div>
            <label htmlFor="compare-focus" className="mb-1 block text-xs font-medium text-slate-400">
              Angle (optional)
            </label>
            <Input
              id="compare-focus"
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              placeholder="“battery life”, “onboarding”…"
              maxLength={500}
            />
          </div>
          <div>
            <label htmlFor="compare-lookback" className="mb-1 block text-xs font-medium text-slate-400">
              Window
            </label>
            <select
              id="compare-lookback"
              value={lookback}
              onChange={(e) => setLookback(Number(e.target.value))}
              className="h-10 rounded-xl border border-white/10 bg-white/[0.04] px-3 text-sm text-slate-100"
              aria-label="Lookback window"
            >
              {LOOKBACKS.map((d) => (
                <option key={d} value={d} className="bg-slate-900">
                  last {d} days
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button onClick={onSubmit} disabled={!canSubmit} loading={state.phase === "working"}>
            <ArrowLeftRight className="h-4 w-4" /> Compare
          </Button>
          {appA && appB && appA.app_id === appB.app_id && (
            <span className="text-xs text-amber-300">Pick two different apps.</span>
          )}
          {quotaExhausted && (
            <span className="text-xs text-amber-300">
              Daily limit reached — upgrade below to run this comparison.
            </span>
          )}
        </div>
      </Card>

      {quotaExhausted && isAnon && <SignInUpsell variant="gate" />}
      {quotaExhausted && !isAnon && (
        <Card className="p-5 text-sm text-slate-300">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-400/20 bg-amber-400/10 px-2.5 py-0.5 text-xs font-medium text-amber-300 mb-1.5">
            Daily limit reached ({me?.used}/{me?.quota} used)
          </span>
          <h3 className="text-base font-semibold text-white">Need more comparisons today?</h3>
          <p className="mt-1 text-xs text-slate-400">
            Your quota resets at UTC midnight. Upgrade for a higher daily limit.
          </p>
          <div className="mt-3">
            <Pricing me={me} />
          </div>
        </Card>
      )}

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
        <Card className="p-5 text-sm text-rose-300" role="alert">
          {state.error ?? "Comparison failed"}
        </Card>
      )}
      {showResult && state.result && <CompareResultView result={state.result} onCompareAnother={reset} />}
      {showResult && isAnon && <SignInUpsell variant="soft" />}
    </div>
  );
}

function SlotPicker({
  label,
  app,
  open,
  onPick,
  onChange,
}: {
  label: string;
  app: AppSummary | null;
  open: boolean;
  onPick: (a: AppSummary) => void;
  onChange: () => void;
}) {
  return (
    <div>
      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-slate-500">
        {label}
      </div>
      {open || !app ? (
        <Search onPick={onPick} autoFocus={open} />
      ) : (
        <button
          type="button"
          onClick={onChange}
          title="Change app"
          className="flex w-full items-center gap-2.5 rounded-xl border border-emerald-400/25 bg-emerald-400/[0.07] px-3 py-2 text-left transition hover:border-emerald-400/50"
        >
          {app.icon ? (
            <img src={app.icon} alt="" className="h-8 w-8 rounded-lg ring-1 ring-white/10" />
          ) : (
            <div className="h-8 w-8 rounded-lg bg-white/10" />
          )}
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium text-slate-100">
              {app.title ?? app.app_id}
            </span>
            <span className="block truncate text-xs text-slate-500">{app.app_id} · tap to change</span>
          </span>
        </button>
      )}
    </div>
  );
}
