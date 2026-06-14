import { Check, Link2, MessageSquarePlus, Star } from "lucide-react";
import { useState } from "react";
import type { AnalysisResult, Theme } from "@/lib/api";
import { Badge, Button, Card } from "./ui";
import { RatingHistogram, SentimentDonut } from "./Charts";

export function Result({
  result,
  onAskAnother,
}: {
  result: AnalysisResult;
  onAskAnother?: () => void;
}) {
  const { app, snapshot, answer, model, analysis_id } = result;
  const positives = answer.themes.filter((t) => t.polarity === "positive");
  const negatives = answer.themes.filter((t) => t.polarity !== "positive");

  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-3 border-b border-slate-100 p-4 sm:p-5">
        {app.icon && <img src={app.icon} alt="" className="h-12 w-12 rounded-xl" />}
        <div className="min-w-0">
          <div className="truncate font-semibold text-slate-900">{app.title ?? app.app_id}</div>
          <div className="flex items-center gap-2 text-xs text-slate-500">
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
          <ShareButton analysisId={analysis_id} />
          {onAskAnother && (
            <Button variant="ghost" size="sm" onClick={onAskAnother}>
              <MessageSquarePlus className="h-4 w-4" /> Ask another
            </Button>
          )}
        </div>
      </div>

      <div className="space-y-6 p-4 sm:p-6">
        <section>
          <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Answer</h2>
          <p className="text-[15px] leading-relaxed text-slate-800">{answer.summary}</p>
          {answer.not_enough_data && (
            <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
              The reviews don't contain enough evidence for a confident answer.
            </p>
          )}
        </section>

        <div className="grid gap-6 sm:grid-cols-2">
          {answer.sentiment_breakdown && (
            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Sentiment</h3>
              <SentimentDonut s={answer.sentiment_breakdown} />
            </section>
          )}
          {app.histogram && app.histogram.length === 5 && (
            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Lifetime ratings
              </h3>
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
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
              Evidence — verified quotes
            </h3>
            <div className="space-y-2">
              {answer.supporting_quotes.map((q, i) => (
                <blockquote key={`${q.id}-${i}`} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                  <p className="text-sm text-slate-700">“{q.quote}”</p>
                  <p className="mt-1 text-xs text-slate-400">
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

function ThemeColumn({ title, themes, quotes }: { title: string; themes: Theme[]; quotes: AnalysisResult["answer"]["supporting_quotes"] }) {
  if (themes.length === 0) return null;
  const negative = title.toLowerCase().includes("complaint");
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</h3>
      <ul className="space-y-2">
        {themes.map((t, i) => {
          const exampleId = t.supporting_quote_ids?.[0];
          const example = quotes.find((q) => q.id === exampleId);
          return (
            <li
              key={i}
              className={`rounded-r-lg border-l-2 bg-slate-50 px-3 py-2 ${negative ? "border-neg" : "border-pos"}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm text-slate-800">{t.label}</span>
                <Badge tone={negative ? "negative" : "positive"}>{t.prevalence}</Badge>
              </div>
              {example && <p className="mt-1 truncate text-xs italic text-slate-500">“{example.quote}”</p>}
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
    <div className="border-t border-slate-100 pt-3 text-xs leading-relaxed text-slate-400">
      {items.map((c, i) => (
        <div key={i}>ⓘ {c}</div>
      ))}
    </div>
  );
}

function ShareButton({ analysisId }: { analysisId: number }) {
  const [copied, setCopied] = useState(false);
  const onShare = async () => {
    const url = `${window.location.origin}/a/${analysisId}`;
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
      {copied ? <Check className="h-4 w-4 text-pos" /> : <Link2 className="h-4 w-4" />}
      {copied ? "Copied" : "Share"}
    </Button>
  );
}
