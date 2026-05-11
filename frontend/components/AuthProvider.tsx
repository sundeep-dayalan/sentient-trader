/**
 * AuthProvider — React context for authentication state.
 *
 * Wrap your app with <AuthProvider> to give every component access to:
 *   - user:           The current Supabase user object (or null)
 *   - isAnonymous:    Whether the user is anonymously signed in
 *   - isSuperUser:    Whether the user's email is in the super users list
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

// ── Helper: check if email is in the super users list ──────────
function checkSuperUser(user: User | null): boolean {
  if (!user || !user.email) return false;

  // Read from a public env var so the browser can check
  const superEmails = process.env.NEXT_PUBLIC_SUPER_USER_EMAILS ?? "";
  const allowedEmails = superEmails
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter((e) => e.length > 0);

  return allowedEmails.includes(user.email.toLowerCase());
}

// ── Helper: get auth callback URL ──────────────────────────────
function getAuthCallbackUrl(): string {
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
  return `${window.location.origin}${basePath}/auth/callback`;
}

// ── Provider Component ─────────────────────────────────────────
export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const [supabase]   = useState(() => createBrowserClient());
  const [user,       setUser]       = useState<User | null>(null);
  const [session,    setSession]    = useState<Session | null>(null);
  const [isLoading,  setIsLoading]  = useState(true);

  // Derived state
  const isAnonymous = user?.is_anonymous === true;
  const isSuperUser_ = checkSuperUser(user);

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
