import { useCallback, useState } from "react";
import { AskPanel } from "@/components/AskPanel";
import { Progress } from "@/components/Progress";
import { Result } from "@/components/Result";
import { Search } from "@/components/Search";
import { Button, Card } from "@/components/ui";
import { useAnalysis } from "@/hooks/useAnalysis";
import type { AppSummary, Me } from "@/lib/api";

export function Home({ me, authEnabled, onUsed }: { me: Me | null; authEnabled: boolean; onUsed: () => void }) {
  const [app, setApp] = useState<AppSummary | null>(null);
  const onComplete = useCallback(() => onUsed(), [onUsed]);
  const { state, submit, reset } = useAnalysis(onComplete);

  if (authEnabled && !me) return <SignInGate />;

  const quotaExhausted = me != null && me.remaining <= 0;
  const showResult = state.phase === "done" && state.result;

  return (
    <div className="space-y-5">
      {!app && state.phase === "idle" && <Hero />}

      <Search
        onPick={(a) => {
          setApp(a);
          reset();
        }}
      />

      {app && (
        <>
          {quotaExhausted ? (
            <Card className="p-4 text-sm text-slate-300">
              You've used all {me?.quota} analyses for today — resets at UTC midnight. Opening a
              previously analyzed question is still free.
            </Card>
          ) : (
            <AskPanel
              app={app}
              busy={state.phase === "working"}
              onSubmit={(q) => submit(app.app_id, q)}
            />
          )}
        </>
      )}

      {(state.phase === "working" || state.phase === "error") && <Progress state={state} />}
      {showResult && state.result && <Result result={state.result} onAskAnother={reset} />}
    </div>
  );
}

function Hero() {
  return (
    <div className="py-8 text-center sm:py-12">
      <span className="mb-4 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs text-slate-300">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_#34d399]" />
        Grounded in real reviews · cited quotes
      </span>
      <h1 className="text-balance text-3xl font-bold tracking-tight sm:text-5xl">
        <span className="text-gradient">Ask anything</span>
        <span className="text-white"> about an app's reviews</span>
      </h1>
      <p className="mx-auto mt-3 max-w-xl text-slate-400">
        Complaints, praise, feature requests, sentiment — answered from real Google Play reviews
        with verified quotes. Search any app to start.
      </p>
    </div>
  );
}

function SignInGate() {
  return (
    <Card className="mx-auto mt-12 max-w-md p-8 text-center">
      <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-brand-grad shadow-glow">
        <span className="text-xl">✦</span>
      </div>
      <h1 className="text-xl font-semibold text-white">Review Lens</h1>
      <p className="mt-2 text-sm text-slate-400">
        Sign in to analyze any Google Play app's reviews — grounded, cited answers. Free, with a
        daily limit.
      </p>
      <a href="/auth/login" className="mt-6 inline-block">
        <Button size="lg">Sign in with Google</Button>
      </a>
    </Card>
  );
}
