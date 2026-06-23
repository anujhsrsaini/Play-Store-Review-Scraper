import { LogIn } from "lucide-react";
import type { AppSummary, Period } from "@/lib/api";
import { Card } from "./ui";

/** Conversion prompt for anonymous trial users. `soft` shows under their first result;
 * `gate` replaces the ask panel once the free analysis is used. On click we stash the
 * current app + question so the user lands back on it after the OAuth round-trip. */
export function SignInUpsell({
  variant,
  app,
  question,
  period,
}: {
  variant: "soft" | "gate";
  app?: AppSummary | null;
  question?: string;
  period?: Period;
}) {
  const gate = variant === "gate";
  const onSignIn = () => {
    try {
      if (app) sessionStorage.setItem("rl_intent", JSON.stringify({ app, question, period }));
    } catch {
      /* sessionStorage unavailable — sign-in still works, just no replay */
    }
  };
  return (
    <Card className={`p-5 ${gate ? "" : "mt-4 border-indigo-400/20"}`}>
      <h3 className="text-base font-semibold text-white">
        {gate ? "One more question? Sign in — it's free." : "Liked that? Sign in for 5 a day — free."}
      </h3>
      <p className="mt-1 text-sm leading-relaxed text-slate-400">
        {gate
          ? "You've used your free analysis. Sign in with Google to keep going — 5 analyses every day, your history saved, shareable links."
          : "That one was on us. Sign in with Google for 5 analyses a day (free), saved history, and shareable result links."}
      </p>
      <a
        href="/auth/login"
        onClick={onSignIn}
        className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl bg-brand-grad px-4 text-sm font-medium text-white shadow-glow-sm transition hover:shadow-glow hover:brightness-110"
      >
        <LogIn className="h-4 w-4" /> Sign in with Google
      </a>
      <p className="mt-2 text-xs text-slate-500">
        We only use your email to set your daily limit. No posting, ever.
      </p>
    </Card>
  );
}
