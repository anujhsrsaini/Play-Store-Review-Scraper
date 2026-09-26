import { ArrowLeft } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CompareResultView } from "@/components/CompareResult";
import { Card, Skeleton } from "@/components/ui";
import { ApiError, api, type CompareResult } from "@/lib/api";

export function SharedCompare() {
  const { token } = useParams();
  const [result, setResult] = useState<CompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    api
      .compareResult(token)
      .then(setResult)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load this comparison"));
  }, [token]);

  return (
    <div className="space-y-4">
      <Link to="/compare" className="inline-flex items-center gap-1 text-sm text-slate-400 transition hover:text-white">
        <ArrowLeft className="h-4 w-4" /> Compare your own apps
      </Link>
      {error ? (
        <Card className="p-6 text-sm text-slate-300">{error}</Card>
      ) : result ? (
        <CompareResultView result={result} />
      ) : (
        <Skeleton className="h-72 w-full rounded-2xl" />
      )}
    </div>
  );
}
