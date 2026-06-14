import { ArrowLeft } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Result } from "@/components/Result";
import { Card, Skeleton } from "@/components/ui";
import { ApiError, api, type AnalysisResult } from "@/lib/api";

export function Shared() {
  const { id } = useParams();
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api
      .analysis(id)
      .then(setResult)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load this analysis"));
  }, [id]);

  return (
    <div className="space-y-4">
      <Link to="/" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800">
        <ArrowLeft className="h-4 w-4" /> Analyze your own app
      </Link>
      {error ? (
        <Card className="p-6 text-sm text-slate-600">{error}</Card>
      ) : result ? (
        <Result result={result} />
      ) : (
        <Skeleton className="h-72 w-full rounded-2xl" />
      )}
    </div>
  );
}
