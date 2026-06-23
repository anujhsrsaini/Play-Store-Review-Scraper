import { CalendarRange, Sparkles } from "lucide-react";
import { useState } from "react";
import type { AppSummary, Period } from "@/lib/api";
import { Button, Card, Textarea } from "./ui";

const PRESETS = [
  "What do users complain about most?",
  "What do people love about this app?",
  "What features are users asking for?",
  "Did the latest update upset users?",
  "Summarize positive vs negative themes",
];

const PERIODS: { value: Period; label: string }[] = [
  { value: "30d", label: "Last 30 days" },
  { value: "60d", label: "Last 60 days" },
  { value: "90d", label: "Last 90 days" },
];

export function AskPanel({
  app,
  onSubmit,
  busy,
  defaultQuestion,
  defaultPeriod,
}: {
  app: AppSummary;
  onSubmit: (question: string, period: Period) => void;
  busy: boolean;
  defaultQuestion?: string;
  defaultPeriod?: Period;
}) {
  const [question, setQuestion] = useState(defaultQuestion ?? "");
  const [period, setPeriod] = useState<Period>(defaultPeriod ?? "90d");
  const submit = () => {
    if (question.trim().length >= 3) onSubmit(question.trim(), period);
  };

  return (
    <Card className="p-4 sm:p-5">
      <div className="mb-3 flex items-center gap-2">
        {app.icon && <img src={app.icon} alt="" className="h-8 w-8 rounded-lg ring-1 ring-white/10" />}
        <span className="text-sm font-medium text-slate-100">{app.title ?? app.app_id}</span>
        <span className="truncate text-xs text-slate-500">{app.app_id}</span>
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        {PRESETS.map((p) => (
          <button
            key={p}
            onClick={() => setQuestion(p)}
            disabled={busy}
            className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[13px] text-slate-300 transition hover:border-indigo-400/40 hover:bg-white/10 disabled:opacity-50"
          >
            {p}
          </button>
        ))}
      </div>

      <Textarea
        rows={3}
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit();
        }}
        placeholder="Ask anything about this app's reviews…"
        disabled={busy}
      />

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button onClick={submit} loading={busy} disabled={question.trim().length < 3}>
          <Sparkles className="h-4 w-4" /> Analyze
        </Button>
        <label className="inline-flex items-center gap-1.5 text-xs text-slate-400">
          <CalendarRange className="h-3.5 w-3.5 text-slate-500" />
          <span className="hidden sm:inline">Reviews from</span>
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value as Period)}
            disabled={busy}
            className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-xs text-slate-200 outline-none transition hover:border-indigo-400/40 focus:border-indigo-400/60 disabled:opacity-50"
          >
            {PERIODS.map((p) => (
              <option key={p.value} value={p.value} className="bg-slate-900 text-slate-100">
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <span className="text-xs text-slate-500">⌘/Ctrl+Enter · cached re-asks are free</span>
      </div>
    </Card>
  );
}
