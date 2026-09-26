import { Mail, Sparkles } from "lucide-react";
import type { Me } from "@/lib/api";
import { CONTACT_MAILTO } from "@/lib/site";

/** Stash plan intent so an anonymous buyer lands back on checkout after sign-in. */
export function stashPlanIntent(plan: string) {
  try {
    const raw = sessionStorage.getItem("rl_intent");
    const intent = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
    sessionStorage.setItem("rl_intent", JSON.stringify({ ...intent, plan }));
  } catch {
    /* sessionStorage unavailable — checkout still works for signed-in users */
  }
}

/** Honest plan cards. Quotas mirror the backend (_quota_for_tier):
 * free = daily setting, starter = 50/day, pro = 200/day, pass = +20 credits. */
export function Pricing({ me }: { me: Me | null }) {
  const freeQuota = me && !me.is_anon ? me.quota : (me?.quota ?? 5);
  return (
    <div>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3.5 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between">
              <span className="font-semibold text-white text-sm">Free</span>
              <span className="text-sm font-bold text-slate-200">$0</span>
            </div>
            <p className="mt-1 text-xs text-slate-400 leading-relaxed">
              {freeQuota} analyses every day, side-by-side comparisons, shareable links.
            </p>
          </div>
        </div>

        <div className="rounded-xl border border-indigo-400/30 bg-white/[0.04] p-3.5 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between">
              <span className="font-semibold text-white text-sm">Indie Pro</span>
              <span className="text-sm font-bold text-gradient">$19/mo</span>
            </div>
            <p className="mt-1 text-xs text-slate-400 leading-relaxed">
              50 analyses every day, comparisons, priority queue. Cancel anytime.
            </p>
          </div>
          <a
            href="/api/billing/checkout?plan=starter"
            onClick={() => stashPlanIntent("starter")}
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
              20 extra analyses on top of your daily quota. Never expire, zero commitment.
            </p>
          </div>
          <a
            href="/api/billing/checkout?plan=pass"
            onClick={() => stashPlanIntent("pass")}
            className="mt-3 inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-white/15 bg-white/5 px-3 text-xs font-medium text-slate-200 transition hover:bg-white/10"
          >
            Get 20 Analyses
          </a>
        </div>
      </div>

      <div className="mt-3.5 pt-3 border-t border-white/5 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <span>Re-asking previously analyzed questions is always free.</span>
        <a href={CONTACT_MAILTO} className="hover:text-slate-400 inline-flex items-center gap-1">
          <Mail className="h-3 w-3" /> Need Agency Pro (200/day)? Talk to us.
        </a>
      </div>
    </div>
  );
}
