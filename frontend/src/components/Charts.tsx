import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, XAxis } from "recharts";
import type { Sentiment } from "@/lib/api";

const POS = "#34d399";
const NEU = "#64748b";
const NEG = "#fb7185";

export function SentimentDonut({ s }: { s: Sentiment }) {
  const data = [
    { name: "Positive", value: s.positive_pct, color: POS },
    { name: "Neutral", value: s.neutral_pct, color: NEU },
    { name: "Negative", value: s.negative_pct, color: NEG },
  ].filter((d) => d.value > 0);

  return (
    <div className="flex items-center gap-5">
      <div className="relative h-28 w-28 shrink-0 drop-shadow-[0_0_18px_rgba(52,211,153,0.25)]">
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
              paddingAngle={2}
            >
              {data.map((d) => (
                <Cell key={d.name} fill={d.color} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xl font-semibold text-white">{Math.round(s.positive_pct)}%</span>
          <span className="text-[11px] text-slate-400">positive</span>
        </div>
      </div>
      <ul className="space-y-1.5 text-sm">
        <LegendRow color={POS} label="Positive" value={s.positive_pct} />
        <LegendRow color={NEU} label="Neutral" value={s.neutral_pct} />
        <LegendRow color={NEG} label="Negative" value={s.negative_pct} />
        <li className="pt-1 text-xs text-slate-500">from {s.counted} star ratings</li>
      </ul>
    </div>
  );
}

function LegendRow({ color, label, value }: { color: string; label: string; value: number }) {
  return (
    <li className="flex items-center gap-2">
      <span className="h-2.5 w-2.5 rounded-full" style={{ background: color, boxShadow: `0 0 8px ${color}` }} />
      <span className="w-16 text-slate-400">{label}</span>
      <span className="font-medium text-slate-100">{value}%</span>
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
          <XAxis dataKey="star" tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: "#94a3b8" }} />
          <Bar dataKey="count" radius={[4, 4, 0, 0]}>
            {data.map((d) => (
              <Cell key={d.star} fill={d.count === max ? "#818cf8" : "rgba(129,140,248,0.28)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
