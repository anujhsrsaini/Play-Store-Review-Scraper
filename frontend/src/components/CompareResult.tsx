import { ArrowLeftRight, Link2 } from "lucide-react";
import { useState } from "react";
import type { CompareQuote, CompareResult, CompareTheme } from "@/lib/api";
import { Badge, Button, Card } from "./ui";

const sectionTitle = "mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500";

function sideLabel(result: CompareResult, side: "a" | "b"): string {
  if (side === "a") return result.app_a_title || result.app_a_id;
  return result.app_b_title || result.app_b_id;
}

function ThemeList({ themes, quotes }: { themes: CompareTheme[]; quotes: CompareQuote[] }) {
  const byId = new Map(quotes.map((q) => [q.id, q]));
  return (
    <div className="space-y-3">
      {themes.map((t, i) => (
        <div key={`${t.label}-${i}`} className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2">
          <div className="flex items-center gap-2 text-sm font-medium text-slate-100">
            <span>{t.label}</span>
            <Badge tone={t.polarity === "positive" ? "positive" : t.polarity === "negative" ? "negative" : "neutral"}>
              {t.polarity}
            </Badge>
            <span className="text-xs font-normal text-slate-500">{t.prevalence}</span>
          </div>
          {(t.supporting_quote_ids ?? []).map((id) => {
            const q = byId.get(id);
            if (!q) return null;
            return (
              <p key={id} className="mt-1 text-[13px] italic leading-relaxed text-slate-400">
                “{q.quote}”
              </p>
            );
          })}
        </div>
      ))}
    </div>
  );
}

export function CompareResultView({
  result,
  onCompareAnother,
}: {
  result: CompareResult;
  onCompareAnother?: () => void;
}) {
  const { comparison, model, share_token } = result;
  const [copied, setCopied] = useState(false);
  const themesA = comparison.themes.filter((t) => t.side === "a" || t.side === "both");
  const themesB = comparison.themes.filter((t) => t.side === "b" || t.side === "both");
  const quotesA = comparison.supporting_quotes.filter((q) => q.side === "a");
  const quotesB = comparison.supporting_quotes.filter((q) => q.side === "b");
  const winner =
    comparison.winner === "tie"
      ? "Even match"
      : comparison.winner === "a"
        ? `${sideLabel(result, "a")} leads`
        : comparison.winner === "b"
          ? `${sideLabel(result, "b")} leads`
          : null;

  const share = async () => {
    if (!share_token) return;
    const url = `${window.location.origin}/c/${share_token}`;
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      window.prompt("Copy this link:", url);
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-white/10 p-4 sm:p-5">
        <div className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-white/[0.06] text-indigo-300">
          <ArrowLeftRight className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1 basis-40">
          <div className="truncate font-semibold text-white">
            {sideLabel(result, "a")} <span className="text-slate-500">vs</span> {sideLabel(result, "b")}
          </div>
          <div className="text-xs text-slate-400">
            last {result.lookback_days} days · {model}
            {winner && (
              <span className="ml-2 rounded-full border border-emerald-400/20 bg-emerald-400/10 px-2 py-0.5 font-medium text-emerald-300">
                {winner}
              </span>
            )}
          </div>
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {share_token && (
            <Button variant="ghost" size="sm" onClick={share}>
              <Link2 className="h-4 w-4" /> {copied ? "Copied" : "Share"}
            </Button>
          )}
          {onCompareAnother && (
            <Button variant="ghost" size="sm" onClick={onCompareAnother}>
              Compare another
            </Button>
          )}
        </div>
      </div>

      <div className="space-y-6 p-4 sm:p-6">
        <section>
          <h2 className={sectionTitle}>Verdict</h2>
          <p className="text-[15px] leading-relaxed text-slate-100">{comparison.summary}</p>
          {comparison.not_enough_data && (
            <p className="mt-2 rounded-lg border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-sm text-amber-200">
              The reviews don't contain enough evidence for a confident comparison.
            </p>
          )}
        </section>

        {(themesA.length > 0 || themesB.length > 0) && (
          <div className="grid gap-6 sm:grid-cols-2">
            <section>
              <h3 className={sectionTitle}>{sideLabel(result, "a")}</h3>
              <ThemeList themes={themesA} quotes={comparison.supporting_quotes} />
            </section>
            <section>
              <h3 className={sectionTitle}>{sideLabel(result, "b")}</h3>
              <ThemeList themes={themesB} quotes={comparison.supporting_quotes} />
            </section>
          </div>
        )}

        {(quotesA.length > 0 || quotesB.length > 0) && (
          <div className="grid gap-6 sm:grid-cols-2">
            <section>
              <h3 className={sectionTitle}>Evidence · {sideLabel(result, "a")}</h3>
              <div className="space-y-2">
                {quotesA.map((q, i) => (
                  <blockquote key={`${q.id}-${i}`} className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2">
                    <p className="text-sm text-slate-200">“{q.quote}”</p>
                    <p className="mt-1 text-xs text-slate-500">
                      {q.stars != null && `★${q.stars} · `}
                      {q.date || ""} · review {q.id}
                    </p>
                  </blockquote>
                ))}
              </div>
            </section>
            <section>
              <h3 className={sectionTitle}>Evidence · {sideLabel(result, "b")}</h3>
              <div className="space-y-2">
                {quotesB.map((q, i) => (
                  <blockquote key={`${q.id}-${i}`} className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2">
                    <p className="text-sm text-slate-200">“{q.quote}”</p>
                    <p className="mt-1 text-xs text-slate-500">
                      {q.stars != null && `★${q.stars} · `}
                      {q.date || ""} · review {q.id}
                    </p>
                  </blockquote>
                ))}
              </div>
            </section>
          </div>
        )}

        {comparison.caveats.length > 0 && (
          <section>
            <h3 className={sectionTitle}>Caveats</h3>
            <ul className="list-disc space-y-1 pl-5 text-sm text-slate-400">
              {comparison.caveats.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </Card>
  );
}
