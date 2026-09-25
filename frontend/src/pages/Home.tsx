import { BadgeCheck, Mail, MessageSquareText, Search as SearchIcon, Sparkles } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { AskPanel } from "@/components/AskPanel";
import { Progress } from "@/components/Progress";
import { Result } from "@/components/Result";
import { Search } from "@/components/Search";
import { SignInUpsell } from "@/components/SignInUpsell";
import { Button, Card } from "@/components/ui";
import { useAnalysis } from "@/hooks/useAnalysis";
import type { AppSummary, Me, Period } from "@/lib/api";
import { CONTACT_MAILTO } from "@/lib/site";

export function Home({ me, authEnabled, onUsed }: { me: Me | null; authEnabled: boolean; onUsed: () => void }) {
  const [app, setApp] = useState<AppSummary | null>(null);
  const [draft, setDraft] = useState<{ question?: string; period?: Period }>({});
  const [last, setLast] = useState<{ question: string; period: Period } | null>(null);
  const onComplete = useCallback(() => onUsed(), [onUsed]);
  const { state, submit, reset } = useAnalysis(onComplete);

  // Replay intent stashed before an OAuth round-trip: land the signed-in user back on the
  // app + question they were trying when they hit the sign-in gate.
  useEffect(() => {
    if (!me?.authenticated) return;
    try {
      const raw = sessionStorage.getItem("rl_intent");
      if (!raw) return;
      sessionStorage.removeItem("rl_intent");
      const intent = JSON.parse(raw) as { app: AppSummary; question?: string; period?: Period };
      if (intent.app) {
        setApp(intent.app);
        setDraft({ question: intent.question, period: intent.period });
      }
    } catch {
      /* ignore malformed/absent intent */
    }
  }, [me?.authenticated]);

  if (authEnabled && !me) return <SignInGate />;

  const isAnon = me?.is_anon === true;
  const quotaExhausted = me != null && me.remaining <= 0;
  const showResult = state.phase === "done" && state.result;
  const landing = !app && state.phase === "idle";

  const pick = (a: AppSummary) => {
    setApp(a);
    setDraft({});
    reset();
  };

  const onSubmit = (q: string, period: Period) => {
    if (!app) return;
    setLast({ question: q, period });
    submit(app.app_id, q, period);
  };

  return (
    <div className="space-y-5">
      {landing && <Hero anon={isAnon} />}

      <Search onPick={pick} />

      {landing && <ExampleApps onPick={pick} />}
      {landing && <HowItWorks />}

      {app && (
        <>
          {quotaExhausted ? (
            isAnon ? (
              <SignInUpsell variant="gate" app={app} question={last?.question} period={last?.period} />
            ) : (
              <Card className="p-5 text-sm text-slate-300">
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                  <div>
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-400/20 bg-amber-400/10 px-2.5 py-0.5 text-xs font-medium text-amber-300 mb-1.5">
                      Daily free limit reached ({me?.quota}/{me?.quota})
                    </span>
                    <h3 className="text-base font-semibold text-white">Unlock unlimited questions & deep analysis</h3>
                    <p className="mt-1 text-xs text-slate-400">
                      Resets at UTC midnight, or upgrade to Indie Pro for high limits, competitor comparisons, and executive exports.
                    </p>
                  </div>
                </div>

                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <div className="rounded-xl border border-indigo-400/30 bg-white/[0.04] p-3.5 flex flex-col justify-between">
                    <div>
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-white text-sm">Indie Pro</span>
                        <span className="text-sm font-bold text-gradient">$19/mo</span>
                      </div>
                      <p className="mt-1 text-xs text-slate-400 leading-relaxed">
                        150 analyses/mo, competitor diffing, CSV/Markdown export & priority queue.
                      </p>
                    </div>
                    <a
                      href="/api/billing/checkout?plan=starter"
                      className="mt-3 inline-flex h-8 items-center justify-center gap-1.5 rounded-lg bg-brand-grad px-3 text-xs font-medium text-white shadow-glow-sm transition hover:shadow-glow hover:brightness-110"
                    >
                      <Sparkles className="h-3.5 w-3.5" /> Upgrade to Pro
                    </a>
                  </div>

                  <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3.5 flex flex-col justify-between">
                    <div>
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-white text-sm">Indie Pass (One-Time)</span>
                        <span className="text-sm font-bold text-slate-200">$15 once</span>
                      </div>
                      <p className="mt-1 text-xs text-slate-400 leading-relaxed">
                        20 deep analyses + full PDF exports. Valid for 6 months, zero commitment.
                      </p>
                    </div>
                    <a
                      href="/api/billing/checkout?plan=pass"
                      className="mt-3 inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-white/15 bg-white/5 px-3 text-xs font-medium text-slate-200 transition hover:bg-white/10"
                    >
                      Get 20 Analyses
                    </a>
                  </div>
                </div>

                <div className="mt-3.5 pt-3 border-t border-white/5 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                  <span>Re-asking previously analyzed questions is always free.</span>
                  <a href={CONTACT_MAILTO} className="hover:text-slate-400 inline-flex items-center gap-1">
                    <Mail className="h-3 w-3" /> Need agency or custom tier?
                  </a>
                </div>
              </Card>
            )
          ) : (
            <AskPanel
              app={app}
              busy={state.phase === "working"}
              defaultQuestion={draft.question}
              defaultPeriod={draft.period}
              onSubmit={onSubmit}
            />
          )}
        </>
      )}

      {(state.phase === "working" || state.phase === "error") && <Progress state={state} />}
      {showResult && state.result && <Result result={state.result} onAskAnother={reset} />}
      {showResult && isAnon && (
        <SignInUpsell variant="soft" app={app} question={last?.question} period={last?.period} />
      )}
    </div>
  );
}

function Hero({ anon }: { anon?: boolean }) {
  return (
    <div className="pb-2 pt-6 text-center sm:pb-4 sm:pt-10">
      <span className="mb-4 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs text-slate-300">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_#34d399]" />
        Grounded in real Google Play reviews · every claim cited
      </span>
      <h1 className="text-balance text-3xl font-bold leading-tight tracking-tight sm:text-5xl">
        <span className="text-white">Ask any app's reviews a question.</span>
        <br className="hidden sm:block" />
        <span className="text-gradient"> Get a cited answer.</span>
      </h1>
      <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-slate-400">
        Search a Google Play app, ask in plain English, and get a clear answer backed by real,
        quoted reviews — complaints, praise, feature requests and sentiment from the last 30–90 days.
      </p>
      {anon && (
        <p className="mx-auto mt-4 inline-flex items-center gap-1.5 rounded-full border border-emerald-400/20 bg-emerald-400/10 px-3 py-1 text-xs font-medium text-emerald-200">
          <Sparkles className="h-3.5 w-3.5" /> No sign-up to try — your first analysis is on us.
        </p>
      )}
    </div>
  );
}

const STEPS = [
  { icon: SearchIcon, title: "Search an app", body: "Find any Google Play app, or paste its package id." },
  { icon: MessageSquareText, title: "Ask a question", body: "“What do users complain about?” — anything, in plain English." },
  { icon: BadgeCheck, title: "Get cited answers", body: "A grounded summary with real, verified review quotes." },
];

function HowItWorks() {
  return (
    <div className="grid gap-2 sm:grid-cols-3">
      {STEPS.map((s, i) => (
        <div key={i} className="glass flex items-start gap-3 p-3.5">
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-white/[0.06] text-indigo-300">
            <s.icon className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-sm font-medium text-slate-100">
              <span className="text-slate-500">{i + 1}.</span> {s.title}
            </div>
            <p className="mt-0.5 text-xs leading-relaxed text-slate-400">{s.body}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

const ex = (app_id: string, title: string): AppSummary => ({
  app_id,
  title,
  score: null,
  installs: null,
  developer: null,
  free: null,
  icon: null,
});

// One-click starting points so a visitor never faces a blank search box.
const EXAMPLE_APPS: AppSummary[] = [
  ex("com.whatsapp", "WhatsApp"),
  ex("com.instagram.android", "Instagram"),
  ex("com.spotify.music", "Spotify"),
  ex("in.swiggy.android", "Swiggy"),
  ex("com.netflix.mediaclient", "Netflix"),
  ex("com.ubercab", "Uber"),
];

function ExampleApps({ onPick }: { onPick: (app: AppSummary) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="inline-flex items-center gap-1 text-xs text-slate-500">
        <Sparkles className="h-3.5 w-3.5" /> Try one:
      </span>
      {EXAMPLE_APPS.map((a) => (
        <button
          key={a.app_id}
          onClick={() => onPick(a)}
          className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[13px] text-slate-300 transition hover:border-indigo-400/40 hover:bg-white/10"
        >
          {a.title}
        </button>
      ))}
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
