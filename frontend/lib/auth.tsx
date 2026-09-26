"use client";

/**
 * Session state for the whole app.
 * - `me` comes from /auth/me (httpOnly cookies; nothing about the user is stored in localStorage).
 * - Silent refresh every 10 minutes while the user is active.
 * - 15 minutes without keyboard/mouse activity -> sign out (the server enforces the same idle limit).
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";

import { API_PREFIX, api, csrfHeaders, onSessionExpired, refreshSession, type User } from "./api/client";
import { qk } from "./api/hooks";

const IDLE_MS = 15 * 60_000;
const REFRESH_MS = 10 * 60_000;
const ACTIVITY_EVENTS = ["pointerdown", "keydown", "wheel", "touchstart", "pointermove"] as const;

interface AuthState {
  user: User | undefined;
  isLoading: boolean;
  logout: (opts?: { everywhere?: boolean; reason?: string }) => Promise<void>;
  hasRole: (...roles: User["role"][]) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const qc = useQueryClient();
  const lastActivity = useRef(Date.now());

  const me = useQuery({
    queryKey: qk.me,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/auth/me");
      if (!response.ok) return null;
      return data?.user ?? null;
    },
    staleTime: 5 * 60_000,
    retry: false,
  });

  const goLogin = useCallback(
    (reason: string) => {
      qc.clear();
      const next = pathname && pathname !== "/login" ? `&next=${encodeURIComponent(pathname)}` : "";
      router.replace(`/login?reason=${reason}${next}`);
    },
    [qc, pathname, router],
  );

  const logout = useCallback(
    async ({ everywhere = false, reason = "signed-out" }: { everywhere?: boolean; reason?: string } = {}) => {
      await fetch(`${API_PREFIX}/auth/${everywhere ? "logout-all" : "logout"}`, {
        method: "POST",
        credentials: "same-origin",
        headers: csrfHeaders(),
      }).catch(() => undefined);
      goLogin(reason);
    },
    [goLogin],
  );

  useEffect(() => onSessionExpired((reason) => goLogin(reason)), [goLogin]);

  useEffect(() => {
    if (me.data === null) goLogin("expired");
  }, [me.data, goLogin]);

  // activity tracking, silent refresh and idle sign-out
  useEffect(() => {
    if (!me.data) return;
    const mark = () => {
      lastActivity.current = Date.now();
    };
    ACTIVITY_EVENTS.forEach((e) => window.addEventListener(e, mark, { passive: true }));
    const refreshTimer = window.setInterval(() => {
      if (Date.now() - lastActivity.current < IDLE_MS) void refreshSession();
    }, REFRESH_MS);
    const idleTimer = window.setInterval(() => {
      if (Date.now() - lastActivity.current >= IDLE_MS) void logout({ reason: "idle" });
    }, 30_000);
    return () => {
      ACTIVITY_EVENTS.forEach((e) => window.removeEventListener(e, mark));
      window.clearInterval(refreshTimer);
      window.clearInterval(idleTimer);
    };
  }, [me.data, logout]);

  const value = useMemo<AuthState>(
    () => ({
      user: me.data ?? undefined,
      isLoading: me.isLoading,
      logout,
      hasRole: (...roles) => !!me.data && roles.includes(me.data.role),
    }),
    [me.data, me.isLoading, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
