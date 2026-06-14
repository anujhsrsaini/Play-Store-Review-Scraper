import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Header } from "@/components/Header";
import { Home } from "@/pages/Home";
import { Shared } from "@/pages/Shared";
import { api, type Health, type Me } from "@/lib/api";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [me, setMe] = useState<Me | null>(null);

  const loadMe = useCallback(async () => {
    try {
      setMe(await api.me());
    } catch {
      setMe(null); // 401 when auth is on and not signed in
    }
  }, []);

  useEffect(() => {
    api.health().then(setHealth).catch(() => {});
    loadMe();
  }, [loadMe]);

  const authEnabled = health?.auth ?? false;

  return (
    <div className="grain relative min-h-full overflow-x-hidden">
      <Aurora />
      <div className="relative z-[2]">
        <Header me={me} authEnabled={authEnabled} />
        <main className="mx-auto max-w-3xl px-4 py-8">
          <Routes>
            <Route path="/" element={<Home me={me} authEnabled={authEnabled} onUsed={loadMe} />} />
            <Route path="/a/:id" element={<Shared />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
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
