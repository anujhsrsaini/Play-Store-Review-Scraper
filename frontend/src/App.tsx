import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Header } from "@/components/Header";
import { Compare } from "@/pages/Compare";
import { Home } from "@/pages/Home";
import { Shared } from "@/pages/Shared";
import { SharedCompare } from "@/pages/SharedCompare";
import { api, type Health, type Me } from "@/lib/api";
import { CONTACT_MAILTO, SITE_URL } from "@/lib/site";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [meLoaded, setMeLoaded] = useState(false);
  const [checkout, setCheckout] = useState<string | null>(null);

  const loadMe = useCallback(async () => {
    try {
      setMe(await api.me());
    } catch {
      setMe(null); // 401 when auth is on and not signed in
    } finally {
      setMeLoaded(true);
    }
  }, []);

  useEffect(() => {
    api.health().then(setHealth).catch(() => {});
    loadMe();
    // Post-payment landing (Dodo success_url): confirm + refresh quota, then
    // clean the URL so a refresh doesn't replay the banner.
    try {
      const params = new URLSearchParams(window.location.search);
      if (params.get("checkout") === "success") {
        setCheckout(params.get("plan"));
        loadMe();
        params.delete("checkout");
        params.delete("plan");
        const rest = params.toString();
        window.history.replaceState(
          null,
          "",
          window.location.pathname + (rest ? `?${rest}` : ""),
        );
      }
    } catch {
      /* non-browser or malformed URL — skip */
    }
  }, [loadMe]);

  const authEnabled = health?.auth ?? false;
  const ready = health !== null && meLoaded;

  return (
    <div className="grain relative min-h-full overflow-x-hidden">
      <Aurora />
      <div className="relative z-[2] flex min-h-screen flex-col">
        <Header me={me} authEnabled={authEnabled} />
        <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">
          {checkout && (
            <div
              role="status"
              className="mb-5 rounded-xl border border-emerald-400/25 bg-emerald-400/10 px-4 py-3 text-sm text-emerald-200"
            >
              Payment confirmed{checkout === "pass" ? " — 20 analyses added" : " — Pro activated"}. Your
              new quota is live.
            </div>
          )}
          {!ready ? (
            <div className="space-y-3 pt-10" aria-busy="true" aria-label="Loading">
              <div className="h-8 w-2/3 animate-pulse rounded-lg bg-white/10" />
              <div className="h-4 w-1/2 animate-pulse rounded-lg bg-white/10" />
              <div className="h-24 animate-pulse rounded-xl bg-white/5" />
            </div>
          ) : (
            <Routes>
              <Route
                path="/"
                element={<Home me={me} authEnabled={authEnabled} onUsed={loadMe} />}
              />
              <Route path="/a/:id" element={<Shared />} />
              <Route
                path="/compare"
                element={<Compare me={me} authEnabled={authEnabled} onUsed={loadMe} />}
              />
              <Route path="/c/:token" element={<SharedCompare />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          )}
        </main>
        <footer className="border-t border-white/10 py-5">
          <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-2 px-4 text-xs text-slate-500">
            <span>© {new Date().getFullYear()} Review Lens · {SITE_URL.replace("https://", "")}</span>
            <span className="inline-flex items-center gap-3">
              {/* Plain anchor: /privacy is served by FastAPI, not an SPA route. */}
              <a href="/privacy" className="transition hover:text-slate-300">
                Privacy
              </a>
              <a href={CONTACT_MAILTO} className="transition hover:text-slate-300">
                Contact
              </a>
            </span>
          </div>
        </footer>
      </div>
    </div>
  );
}

function Aurora() {
  return (
    <>
      <div className="aurora-blob left-[-10%] top-[-12%] h-[42vh] w-[42vh] animate-drift bg-indigo-600" />
      <div className="aurora-blob right-[-8%] top-[6%] h-[38vh] w-[38vh] animate-drift-slow bg-fuchsia-600" />
      <div className="aurora-blob bottom-[-15%] left-[30%] h-[40vh] w-[40vh] animate-drift bg-cyan-500" />
    </>
  );
}
