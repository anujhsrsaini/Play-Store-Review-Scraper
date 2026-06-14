import { Sparkles } from "lucide-react";
import { useState } from "react";
import type { AppSummary } from "@/lib/api";
import { Button, Card, Textarea } from "./ui";

const PRESETS = [
  "What do users complain about most?",
  "What do people love about this app?",
  "What features are users asking for?",
  "Did the latest update upset users?",
  "Summarize positive vs negative themes",
];

export function AskPanel({
  app,
  onSubmit,
  busy,
}: {
  app: AppSummary;
  onSubmit: (question: string) => void;
  busy: boolean;
}) {
  const [question, setQuestion] = useState("");
  const submit = () => {
    if (question.trim().length >= 3) onSubmit(question.trim());
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

      <div className="mt-3 flex items-center gap-3">
        <Button onClick={submit} loading={busy} disabled={question.trim().length < 3}>
          <Sparkles className="h-4 w-4" /> Analyze
        </Button>
        <span className="text-xs text-slate-500">⌘/Ctrl+Enter · cached re-asks are free</span>
      </div>
    </Card>
  );
}
