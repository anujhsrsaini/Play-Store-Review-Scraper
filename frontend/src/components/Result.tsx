import { Check, Info, Link2, MessageSquarePlus, Star } from "lucide-react";
import { useState } from "react";
import type { AnalysisResult, Theme } from "@/lib/api";
import { Badge, Button, Card } from "./ui";
import { RatingHistogram, SentimentDonut } from "./Charts";

const sectionTitle = "mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500";

export function Result({
  result,
  onAskAnother,
}: {
  result: AnalysisResult;
  onAskAnother?: () => void;
}) {
  const { app, snapshot, answer, model, share_token } = result;
  const positives = answer.themes.filter((t) => t.polarity === "positive");
  const negatives = answer.themes.filter((t) => t.polarity !== "positive");

  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-3 border-b border-white/10 p-4 sm:p-5">
        {app.icon && <img src={app.icon} alt="" className="h-12 w-12 rounded-xl ring-1 ring-white/10" />}
        <div className="min-w-0">
          <div className="truncate font-semibold text-white">{app.title ?? app.app_id}</div>
          <div className="flex items-center gap-2 text-xs text-slate-400">
            {app.score != null && (
              <span className="inline-flex items-center gap-0.5">
                <Star className="h-3 w-3 fill-amber-400 text-amber-400" />
                {app.score.toFixed(2)}
              </span>
            )}
            <span>· {snapshot.review_count} reviews analyzed</span>
            <span className="hidden sm:inline">· {model}</span>
          </div>
        </div>
        <div className="ml-auto flex shrink-0 gap-2">
          <ShareButton token={share_token} />
          {onAskAnother && (
            <Button variant="ghost" size="sm" onClick={onAskAnother}>
              <MessageSquarePlus className="h-4 w-4" /> Ask another
            </Button>
          )}
        </div>
      </div>

      <MethodologyStrip result={result} />

      <div className="space-y-6 p-4 sm:p-6">
        <section>
          <h2 className={sectionTitle}>Answer</h2>
          <p className="text-[15px] leading-relaxed text-slate-100">{answer.summary}</p>
          {answer.not_enough_data && (
            <p className="mt-2 rounded-lg border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-sm text-amber-200">
              The reviews don't contain enough evidence for a confident answer.
            </p>
          )}
        </section>

        <div className="grid gap-6 sm:grid-cols-2">
          {answer.sentiment_breakdown && (
            <section>
              <h3 className={sectionTitle}>Sentiment</h3>
              <SentimentDonut s={answer.sentiment_breakdown} />
            </section>
          )}
          {app.histogram && app.histogram.length === 5 && (
            <section>
              <h3 className={sectionTitle}>Lifetime ratings</h3>
              <RatingHistogram histogram={app.histogram} />
            </section>
          )}
        </div>

        {(negatives.length > 0 || positives.length > 0) && (
          <div className="grid gap-6 sm:grid-cols-2">
            <ThemeColumn title="Top complaints" themes={negatives} quotes={answer.supporting_quotes} />
            <ThemeColumn title="What they love" themes={positives} quotes={answer.supporting_quotes} />
          </div>
        )}

        {answer.supporting_quotes.length > 0 && (
          <section>
            <h3 className={sectionTitle}>Evidence — verified quotes</h3>
            <div className="space-y-2">
              {answer.supporting_quotes.map((q, i) => (
                <blockquote
                  key={`${q.id}-${i}`}
                  className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2"
                >
                  <p className="text-sm text-slate-200">“{q.quote}”</p>
                  <p className="mt-1 text-xs text-slate-500">
                    {q.stars != null && `★${q.stars} · `}
                    {q.date || ""} · review {q.id}
                  </p>
                </blockquote>
              ))}
            </div>
          </section>
        )}

        <CaveatsBanner caveats={answer.caveats} dataQuality={answer.data_quality} />
      </div>
    </Card>
  );
}

function ThemeColumn({
  title,
  themes,
  quotes,
}: {
  title: string;
  themes: Theme[];
  quotes: AnalysisResult["answer"]["supporting_quotes"];
}) {
  if (themes.length === 0) return null;
  const negative = title.toLowerCase().includes("complaint");
  return (
    <section>
      <h3 className={sectionTitle}>{title}</h3>
      <ul className="space-y-2">
        {themes.map((t, i) => {
          const exampleId = t.supporting_quote_ids?.[0];
          const example = quotes.find((q) => q.id === exampleId);
          return (
            <li
              key={i}
              className={`rounded-r-lg border-l-2 bg-white/[0.03] px-3 py-2 ${negative ? "border-rose-400" : "border-emerald-400"}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm text-slate-200">{t.label}</span>
                <Badge tone={negative ? "negative" : "positive"}>{t.prevalence}</Badge>
              </div>
              {example && <p className="mt-1 truncate text-xs italic text-slate-400">“{example.quote}”</p>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function CaveatsBanner({ caveats, dataQuality }: { caveats: string[]; dataQuality?: string }) {
  const items = [...(caveats || [])];
  if (dataQuality) items.push(dataQuality);
  if (items.length === 0) return null;
  return (
    <div className="border-t border-white/10 pt-3 text-xs leading-relaxed text-slate-500">
      {items.map((c, i) => (
        <div key={i}>ⓘ {c}</div>
      ))}
    </div>
  );
}

function MethodologyStrip({ result }: { result: AnalysisResult }) {
  const { app, snapshot, answer } = result;
  const lifetime = app.reviews ? ` of ${app.reviews.toLocaleString()}` : "";
  const sortLabel = snapshot.sort.replace(/^Sort\./, "").toLowerCase();
  const fetched = new Date(snapshot.fetched_at).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  const sentimentSrc =
    answer.sentiment_breakdown?.source === "lifetime_histogram"
      ? "sentiment from lifetime ★ ratings"
      : "sentiment from the sampled reviews";
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-white/10 bg-white/[0.02] px-4 py-2 text-[11px] text-slate-400 sm:px-5">
      <Info className="h-3.5 w-3.5 text-slate-500" />
      <span>
        Analyzed <strong className="font-medium text-slate-300">{snapshot.review_count}{lifetime}</strong> reviews
      </span>
      <span aria-hidden>·</span>
      <span>{sortLabel}</span>
      <span aria-hidden>·</span>
      <span>{sentimentSrc}</span>
      <span aria-hidden>·</span>
      <span>fetched {fetched}</span>
      {!snapshot.complete && <span className="text-amber-400">· partial fetch</span>}
    </div>
  );
}

function ShareButton({ token }: { token: string }) {
  const [copied, setCopied] = useState(false);
  const onShare = async () => {
    const url = `${window.location.origin}/a/${token}`;
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      window.prompt("Copy this link:", url);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };
  return (
    <Button variant="outline" size="sm" onClick={onShare}>
      {copied ? <Check className="h-4 w-4 text-emerald-400" /> : <Link2 className="h-4 w-4" />}
      {copied ? "Copied" : "Share"}
    </Button>
  );
}
