import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, XAxis } from "recharts";
import type { Sentiment } from "@/lib/api";

const POS = "#16a34a";
const NEU = "#9ca3af";
const NEG = "#dc2626";

export function SentimentDonut({ s }: { s: Sentiment }) {
  const data = [
    { name: "Positive", value: s.positive_pct, color: POS },
    { name: "Neutral", value: s.neutral_pct, color: NEU },
    { name: "Negative", value: s.negative_pct, color: NEG },
  ].filter((d) => d.value > 0);

  return (
    <div className="flex items-center gap-5">
      <div className="relative h-28 w-28 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              innerRadius={38}
              outerRadius={56}
              startAngle={90}
              endAngle={-270}
              stroke="none"
            >
              {data.map((d) => (
                <Cell key={d.name} fill={d.color} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xl font-semibold text-slate-800">{Math.round(s.positive_pct)}%</span>
          <span className="text-[11px] text-slate-500">positive</span>
        </div>
      </div>
      <ul className="space-y-1.5 text-sm">
        <LegendRow color={POS} label="Positive" value={s.positive_pct} />
        <LegendRow color={NEU} label="Neutral" value={s.neutral_pct} />
        <LegendRow color={NEG} label="Negative" value={s.negative_pct} />
        <li className="pt-1 text-xs text-slate-400">from {s.counted} star ratings</li>
      </ul>
    </div>
  );
}

function LegendRow({ color, label, value }: { color: string; label: string; value: number }) {
  return (
    <li className="flex items-center gap-2">
      <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
      <span className="w-16 text-slate-600">{label}</span>
      <span className="font-medium text-slate-800">{value}%</span>
    </li>
  );
}

export function RatingHistogram({ histogram }: { histogram: number[] }) {
  // histogram is [1★,2★,3★,4★,5★] lifetime counts
  const data = histogram.map((count, i) => ({ star: `${i + 1}★`, count }));
  const max = Math.max(...histogram, 1);
  return (
    <div className="h-28 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
          <XAxis dataKey="star" tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: "#64748b" }} />
          <Bar dataKey="count" radius={[4, 4, 0, 0]}>
            {data.map((d) => (
              <Cell key={d.star} fill={d.count === max ? "#4f46e5" : "#c7d2fe"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
