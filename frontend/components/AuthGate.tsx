/**
 * AuthGate — The "sign in" modal that appears when an anonymous user
 * tries to use a feature that requires authentication.
 *
 * This is "The Wall" from the auth strategy:
 *   1. User lands → auto-signed in anonymously → can browse freely
 *   2. User uses their 1 free simulation → allowed
 *   3. User tries a 2nd simulation → this modal pops up
 *   4. User signs in with GitHub/Google/Magic Link
 *   5. User gets 2 more simulations per day
 *
 * Props:
 *   isOpen   — whether the modal is visible
 *   onClose  — called when the user closes the modal
 *   reason   — why the modal appeared ("auth_required" or "limit_reached")
 */

"use client";

import { useState } from "react";
import { useAuth } from "./AuthProvider";

interface AuthGateProps {
  isOpen: boolean;
  onClose: () => void;
  reason?: "auth_required" | "limit_reached";
}

export default function AuthGate({ isOpen, onClose, reason = "auth_required" }: AuthGateProps) {
  const { signInWithGithub, signInWithGoogle, signInWithMagicLink } = useAuth();
  const [email, setEmail]           = useState("");
  const [magicLinkSent, setMagicLinkSent] = useState(false);
  const [magicLinkError, setMagicLinkError] = useState("");
  const [isSending, setIsSending]   = useState(false);

  if (!isOpen) return null;

  // ── Handle magic link submission ──────────────────────────────
  async function handleMagicLink() {
    if (!email.trim() || isSending) return;
    setIsSending(true);
    setMagicLinkError("");

    const result = await signInWithMagicLink(email.trim());

    if (result.error) {
      setMagicLinkError(result.error);
    } else {
      setMagicLinkSent(true);
    }
    setIsSending(false);
  }

  // ── Title and description based on why we're showing this ─────
  const title =
    reason === "limit_reached"
      ? "Daily limit reached"
      : "Glad you're enjoying the app!";

  const description =
    reason === "limit_reached"
      ? "You've used all your simulations for today. Sign in to get more."
      : "Sign in to keep generating simulations for free.";

  return (
    <div
      className="modal-backdrop fixed inset-0 z-[200] flex items-center justify-center px-4 py-6 backdrop-blur-sm"
      role="presentation"
      onMouseDown={onClose}
    >
      <div
        className="glass-panel w-full max-w-sm rounded-xl p-6"
        role="dialog"
        aria-modal="true"
        aria-label="Sign in"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* ── Close button ─────────────────────────────────────── */}
        <div className="flex items-start justify-between gap-3 mb-5">
          <div>
            <h2 className="text-base font-bold text-primary">{title}</h2>
            <p className="mt-1 text-sm text-muted">{description}</p>
          </div>
          <button
            onClick={onClose}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-line bg-surface-2 text-muted transition-all hover:border-accent-border hover:text-accent"
            aria-label="Close"
          >
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* ── Social login buttons ─────────────────────────────── */}
        <div className="space-y-2.5">
          {/* GitHub */}
          <button
            onClick={signInWithGithub}
            className="flex w-full items-center justify-center gap-3 rounded-xl border border-line bg-surface-2 px-4 py-3 text-sm font-semibold text-primary transition-all hover:border-accent-border hover:bg-hover"
          >
            <svg className="h-5 w-5" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z" />
            </svg>
            Continue with GitHub
          </button>

          {/* Google */}
          <button
            onClick={signInWithGoogle}
            className="flex w-full items-center justify-center gap-3 rounded-xl border border-line bg-surface-2 px-4 py-3 text-sm font-semibold text-primary transition-all hover:border-accent-border hover:bg-hover"
          >
            <svg className="h-5 w-5" viewBox="0 0 24 24">
              <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
              <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
              <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
              <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
            </svg>
            Continue with Google
          </button>
        </div>

        {/* ── Divider ──────────────────────────────────────────── */}
        <div className="my-4 flex items-center gap-3">
          <div className="flex-1 border-t border-line" />
          <span className="text-xs font-medium text-muted">or</span>
          <div className="flex-1 border-t border-line" />
        </div>

        {/* ── Magic Link ───────────────────────────────────────── */}
        {magicLinkSent ? (
          <div className="rounded-xl border border-positive-border bg-positive-soft px-4 py-3 text-center">
            <p className="text-sm font-semibold text-positive">Check your email!</p>
            <p className="mt-1 text-xs text-positive opacity-80">
              We sent a sign-in link to <strong>{email}</strong>
            </p>
          </div>
        ) : (
          <div className="space-y-2.5">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleMagicLink()}
              placeholder="you@example.com"
              disabled={isSending}
              className="w-full rounded-xl border border-line bg-surface-2 px-4 py-3 text-sm text-primary outline-none transition-colors placeholder:text-muted focus:border-accent-border disabled:opacity-50"
            />
            <button
              onClick={handleMagicLink}
              disabled={!email.trim() || isSending}
              className={[
                "flex w-full items-center justify-center gap-2 rounded-xl border px-4 py-3 text-sm font-semibold transition-all",
                email.trim() && !isSending
                  ? "bg-accent border-accent text-white hover:opacity-90 cursor-pointer"
                  : "bg-surface-2 border-line text-muted cursor-not-allowed",
              ].join(" ")}
            >
              {isSending ? (
                <>
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  Sending...
                </>
              ) : (
                <>
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75" />
                  </svg>
                  Send Magic Link
                </>
              )}
            </button>
            {magicLinkError && (
              <p className="text-xs text-negative text-center">{magicLinkError}</p>
            )}
          </div>
        )}

        {/* ── Footer note ──────────────────────────────────────── */}
        <p className="mt-4 text-center text-[11px] text-muted leading-relaxed">
          No password needed. One-click login.
          <br />
          We only use your email to identify your account.
        </p>
      </div>
    </div>
  );
}
