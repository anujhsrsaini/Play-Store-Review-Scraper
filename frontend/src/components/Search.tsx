import { Search as SearchIcon, Star } from "lucide-react";
import { useState } from "react";
import { ApiError, api, type AppSummary } from "@/lib/api";
import { Button, Card, Input, Skeleton } from "./ui";

const PACKAGE_RE = /^[a-zA-Z0-9._]+$/;

export function Search({ onPick }: { onPick: (app: AppSummary) => void }) {
  const [q, setQ] = useState("");
  const [apps, setApps] = useState<AppSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    const query = q.trim();
    if (!query) return;
    // A bare package id (dotted, no spaces) can be analyzed directly.
    if (PACKAGE_RE.test(query) && query.includes(".")) {
      onPick({ app_id: query, title: query, score: null, installs: null, developer: "direct package id", free: null, icon: null });
      return;
    }
    setLoading(true);
    setError(null);
    setApps(null);
    try {
      setApps(await api.search(query));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Search failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div className="flex gap-2">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="Search an app (e.g. “Ultrahuman”) or paste a package id"
        />
        <Button onClick={run} loading={loading} size="lg">
          <SearchIcon className="h-4 w-4" /> Search
        </Button>
      </div>

      {error && <p className="mt-3 text-sm text-rose-300">{error}</p>}

      {loading && (
        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      )}

      {apps && apps.length === 0 && <p className="mt-4 text-sm text-slate-400">No apps found.</p>}

      {apps && apps.length > 0 && (
        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          {apps.map((a) => (
            <Card
              key={a.app_id}
              className="glass-hover flex cursor-pointer items-center gap-3 p-3"
              onClick={() => onPick(a)}
            >
              {a.icon ? (
                <img src={a.icon} alt="" className="h-10 w-10 rounded-lg ring-1 ring-white/10" />
              ) : (
                <div className="h-10 w-10 rounded-lg bg-white/10" />
              )}
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-slate-100">{a.title ?? a.app_id}</div>
                <div className="flex items-center gap-1.5 text-xs text-slate-400">
                  {a.score != null && (
                    <span className="inline-flex items-center gap-0.5">
                      <Star className="h-3 w-3 fill-amber-400 text-amber-400" />
                      {a.score.toFixed(1)}
                    </span>
                  )}
                  {a.installs && <span>· {a.installs}</span>}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
