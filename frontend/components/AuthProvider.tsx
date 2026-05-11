/**
 * AuthProvider — React context for authentication state.
 *
 * Wrap your app with <AuthProvider> to give every component access to:
 *   - user:           The current Supabase user object (or null)
 *   - isAnonymous:    Whether the user is anonymously signed in
 *   - isSuperUser:    Whether the server confirms this user is an admin
 *   - isLoading:      Whether we're still checking the auth state
 *   - signInWithGithub(), signInWithGoogle(), signInWithMagicLink(email)
 *   - signOut()
 *
 * HOW IT WORKS:
 * 1. On first load, it checks for an existing session.
 * 2. If no session exists, it signs in anonymously (invisible to the user).
 * 3. When the user clicks "Sign in with GitHub/Google", we redirect to the
 *    OAuth provider. When they come back, the anonymous account is upgraded
 *    to a real account automatically (Supabase handles this).
 *
 * SECURITY (SEC-02 FIX):
 * Super user status is determined server-side via GET /api/auth/me.
 * The admin email list is never exposed to the frontend bundle.
 *
 * Usage:
 *   import { useAuth } from "@/components/AuthProvider"
 *   const { user, isAnonymous, signInWithGithub } = useAuth()
 */

"use client";

import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { createBrowserClient } from "@/lib/supabase-browser";
import type { User, Session } from "@supabase/supabase-js";

// ── Types ──────────────────────────────────────────────────────
interface AuthContextValue {
  user: User | null;
  session: Session | null;
  isLoading: boolean;
  isAnonymous: boolean;
  isSuperUser: boolean;
  signInWithGithub: () => Promise<void>;
  signInWithGoogle: () => Promise<void>;
  signInWithMagicLink: (email: string) => Promise<{ error?: string }>;
  signOut: () => Promise<void>;
}

// ── Context ────────────────────────────────────────────────────
const AuthContext = createContext<AuthContextValue | null>(null);

// ── Hook ───────────────────────────────────────────────────────
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth() must be used inside <AuthProvider>");
  }
  return context;
}

// ── Helper: get auth callback URL ──────────────────────────────
function getAuthCallbackUrl(): string {
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
  return `${window.location.origin}${basePath}/auth/callback`;
}

// ── Helper: fetch super user status from the server ────────────
async function fetchUserRole(): Promise<{ isSuperUser: boolean }> {
  try {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
    const res = await fetch(`${basePath}/api/auth/me`, { credentials: "same-origin" });
    if (!res.ok) return { isSuperUser: false };
    const data = await res.json();
    return { isSuperUser: data.isSuperUser === true };
  } catch {
    return { isSuperUser: false };
  }
}

// ── Provider Component ─────────────────────────────────────────
export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const [supabase]   = useState(() => createBrowserClient());
  const [user,       setUser]       = useState<User | null>(null);
  const [session,    setSession]    = useState<Session | null>(null);
  const [isLoading,  setIsLoading]  = useState(true);
  const [isSuperUser_, setIsSuperUser] = useState(false);

  // Derived state
  const isAnonymous = user?.is_anonymous === true;

  // ── Fetch super user status whenever user changes ─────────────
  useEffect(() => {
    if (!user || user.is_anonymous) {
      setIsSuperUser(false);
      return;
    }
    fetchUserRole().then(({ isSuperUser }) => setIsSuperUser(isSuperUser));
  }, [user]);

  // ── Initialize: check session, exchange OAuth code, or sign in anonymously ──
  useEffect(() => {
    async function init() {
      // ── Step 1: If there's a ?code= in the URL, exchange it for a session ──
      // This handles the case where Supabase redirects to the root page
      // (Site URL fallback) instead of /auth/callback. The PKCE code verifier
      // is stored in cookies by createBrowserClient, so we can exchange here.
      const url = new URL(window.location.href);
      const code = url.searchParams.get("code");

      if (code) {
        // Clean the code from the URL immediately (don't expose it to the user)
        url.searchParams.delete("code");
        window.history.replaceState({}, "", url.pathname + url.search);

        // Exchange the code for a real session
        const { data: { session: oauthSession }, error: oauthError } =
          await supabase.auth.exchangeCodeForSession(code);

        if (!oauthError && oauthSession?.user) {
          setSession(oauthSession);
          setUser(oauthSession.user);
          setIsLoading(false);
          return;
        }
        // If exchange fails, fall through to normal init
        console.error("OAuth code exchange failed:", oauthError?.message);
      }

      // ── Step 2: Check for existing session ──
      const { data: { session: existingSession } } = await supabase.auth.getSession();

      if (existingSession?.user) {
        setSession(existingSession);
        setUser(existingSession.user);
        setIsLoading(false);
        return;
      }

      // ── Step 3: No session → sign in anonymously (invisible to user) ──
      const { data, error } = await supabase.auth.signInAnonymously();
      if (!error && data.session) {
        setSession(data.session);
        setUser(data.session.user);
      }
      setIsLoading(false);
    }

    init();
  }, [supabase]);

  // ── Listen for auth state changes (login, logout, token refresh) ──
  useEffect(() => {
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event, newSession) => {
        setSession(newSession);
        setUser(newSession?.user ?? null);
      },
    );

    return () => subscription.unsubscribe();
  }, [supabase]);

  // ── Sign in with GitHub ──────────────────────────────────────
  const signInWithGithub = useCallback(async () => {
    await supabase.auth.signInWithOAuth({
      provider: "github",
      options: {
        redirectTo: getAuthCallbackUrl(),
      },
    });
  }, [supabase]);

  // ── Sign in with Google ──────────────────────────────────────
  const signInWithGoogle = useCallback(async () => {
    await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: getAuthCallbackUrl(),
        queryParams: {
          access_type: "offline",
          prompt: "consent",
        },
      },
    });
  }, [supabase]);

  // ── Sign in with Magic Link ──────────────────────────────────
  const signInWithMagicLink = useCallback(async (email: string) => {
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: {
        emailRedirectTo: getAuthCallbackUrl(),
      },
    });

    if (error) {
      return { error: error.message };
    }
    return {};
  }, [supabase]);

  // ── Sign out ─────────────────────────────────────────────────
  const signOut = useCallback(async () => {
    await supabase.auth.signOut();
    // Full page reload to reset all data and re-create anonymous session
    window.location.reload();
  }, [supabase]);

  // ── Provide the context ──────────────────────────────────────
  return (
    <AuthContext.Provider
      value={{
        user,
        session,
        isLoading,
        isAnonymous,
        isSuperUser: isSuperUser_,
        signInWithGithub,
        signInWithGoogle,
        signInWithMagicLink,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

