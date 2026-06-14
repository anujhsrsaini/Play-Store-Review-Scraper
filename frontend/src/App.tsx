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
    <div className="min-h-full">
      <Header me={me} authEnabled={authEnabled} />
      <main className="mx-auto max-w-3xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Home me={me} authEnabled={authEnabled} onUsed={loadMe} />} />
          <Route path="/a/:id" element={<Shared />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
