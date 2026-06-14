import { LogOut, ScanSearch } from "lucide-react";
import type { Me } from "@/lib/api";
import { Badge } from "./ui";

export function Header({ me, authEnabled }: { me: Me | null; authEnabled: boolean }) {
  return (
    <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/80 backdrop-blur">
      <div className="mx-auto flex max-w-3xl items-center gap-3 px-4 py-3">
        <a href="/" className="flex items-center gap-2 font-semibold text-slate-900">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand text-brand-fg">
            <ScanSearch className="h-5 w-5" />
          </span>
          Review Lens
        </a>
        <span className="hidden text-sm text-slate-400 sm:inline">· Play Store review analysis</span>
        <div className="ml-auto flex items-center gap-3">
          {me ? (
            <>
              <Badge tone={me.remaining > 0 ? "neutral" : "negative"}>
                {me.remaining}/{me.quota} left today
              </Badge>
              {authEnabled && (
                <a
                  href="/auth/logout"
                  title={me.email}
                  className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"
                >
                  <LogOut className="h-4 w-4" /> Sign out
                </a>
              )}
            </>
          ) : (
            authEnabled && (
              <a
                href="/auth/login"
                className="inline-flex h-9 items-center gap-2 rounded-lg bg-brand px-4 text-sm font-medium text-brand-fg hover:bg-indigo-700"
              >
                Sign in with Google
              </a>
            )
          )}
        </div>
      </div>
    </header>
  );
}
