// Typed client for the FastAPI backend. All calls are same-origin (the SPA is served by
// FastAPI; the Vite dev server proxies /api + /auth).

export interface AppSummary {
  app_id: string;
  title: string | null;
  score: number | null;
  installs: string | null;
  developer: string | null;
  free: boolean | null;
  icon: string | null;
}

export interface Theme {
  label: string;
  polarity: "positive" | "negative" | "mixed";
  prevalence: "high" | "medium" | "low";
  supporting_quote_ids?: string[];
}

export interface Quote {
  id: string;
  quote: string;
  stars?: number | null;
  date?: string | null;
}

export interface Sentiment {
  positive_pct: number;
  neutral_pct: number;
  negative_pct: number;
  counted: number;
  source: string;
}

export interface Answer {
  summary: string;
  not_enough_data: boolean;
  themes: Theme[];
  supporting_quotes: Quote[];
  caveats: string[];
  sentiment_breakdown?: Sentiment;
  data_quality?: string;
}

export interface AnalysisResult {
  share_token: string;
  model: string;
  app: {
    app_id: string;
    title: string | null;
    score: number | null;
    ratings: number | null;
    installs: string | null;
    version: string | null;
    histogram: number[] | null;
    icon: string | null;
    reviews: number | null; // lifetime review count
  };
  snapshot: { fetched_at: string; review_count: number; sort: string; complete: boolean };
  answer: Answer;
}

export interface Me {
  authenticated: boolean;
  email: string;
  name: string;
  used: number;
  quota: number;
  remaining: number;
}

export interface Health {
  ok: boolean;
  provider: string;
  llm: string;
  auth: boolean;
}

export type JobStatus = "queued" | "scraping" | "analyzing" | "done" | "error";

export interface JobState {
  job_id: string;
  status: JobStatus;
  progress: number;
  error: string | null;
  result?: AnalysisResult;
}

export interface SubmitResult {
  job_id: string | null;
  status: string;
  cache_hit: boolean;
  result?: AnalysisResult;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (!res.ok) {
    throw new ApiError(await readError(res), res.status);
  }
  return (await res.json()) as T;
}

async function readError(res: Response): Promise<string> {
  try {
    const j = await res.json();
    if (typeof j.detail === "string") return j.detail;
    if (Array.isArray(j.detail)) return j.detail.map((d: { msg?: string }) => d.msg ?? "invalid").join("; ");
    if (j.error) return j.error;
  } catch {
    /* non-JSON body */
  }
  return `HTTP ${res.status}`;
}

export const api = {
  health: () => req<Health>("/api/health"),
  me: () => req<Me>("/api/me"),
  search: (q: string, country = "in", lang = "en") =>
    req<AppSummary[]>(`/api/search?q=${encodeURIComponent(q)}&country=${country}&lang=${lang}`),
  analyze: (appId: string, question: string, country = "in", lang = "en") =>
    req<SubmitResult>("/api/analyze", {
      method: "POST",
      body: JSON.stringify({ app_id: appId, question, country, lang }),
    }),
  job: (jobId: string) => req<JobState>(`/api/jobs/${jobId}`),
  analysis: (id: number | string) => req<AnalysisResult>(`/api/analysis/${id}`),
};
