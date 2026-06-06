/**
 * PostHog analytics client for the BROWSER.
 *
 * Initializes posthog-js once with the public project key. The key is a
 * write-only "phc_..." token, so it is safe to ship in the frontend bundle
 * (same trust model as VITE_SUPABASE_ANON_KEY).
 *
 * Analytics is OPT-OUT by config: if VITE_PUBLIC_POSTHOG_KEY is missing
 * (e.g. local dev without a key), initialization is skipped and every
 * PostHog call becomes a no-op — nothing breaks.
 *
 * Usage:
 *   import { initPostHog } from "@/lib/posthog"
 *   initPostHog()              // call once at app start
 *
 *   import { usePostHog } from "posthog-js/react"
 *   const posthog = usePostHog()
 *   posthog.capture("simulate_clicked")
 */

import posthog from 'posthog-js';

const POSTHOG_KEY = import.meta.env.VITE_PUBLIC_POSTHOG_KEY;
const POSTHOG_HOST =
  import.meta.env.VITE_PUBLIC_POSTHOG_HOST || 'https://us.i.posthog.com';

let initialized = false;

export function initPostHog(): typeof posthog | null {
  if (initialized) return posthog;
  if (!POSTHOG_KEY) {
    // No key configured — skip analytics entirely (no-op).
    return null;
  }

  posthog.init(POSTHOG_KEY, {
    api_host: POSTHOG_HOST,
    // Reverse-proxy-friendly default; PostHog picks the right ingestion host.
    ui_host: 'https://us.posthog.com',
    // We call identify() ourselves from AuthProvider once a real (non-anonymous)
    // user is known, so don't auto-create person profiles for every visitor.
    person_profiles: 'identified_only',
    capture_pageview: true,
    capture_pageleave: true,
    autocapture: true,
  });

  initialized = true;
  return posthog;
}

export { posthog };
