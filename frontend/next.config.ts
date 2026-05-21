import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

const nextConfig: NextConfig = {
  // Prefix all _next/ asset URLs with /sentient-trader so the portfolio proxy
  // rule /sentient-trader/* can forward them to sentienttrader.netlify.app.
  // No basePath — pages are served at root, avoiding redirect loops.
  assetPrefix: "/sentient-trader",

  // ── Security Headers ────────────────────────────────────────────
  // These apply during local dev and are also picked up by Netlify.
  // CSP is in report-only mode to catch violations without breaking the app.
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              // ST-04 FIX: 'unsafe-eval' only in dev (HMR needs it), stripped in production.
              `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
              "connect-src 'self' https://*.supabase.co wss://*.supabase.co https://supabase.sundeepdayalan.in wss://supabase.sundeepdayalan.in",
              "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
              "img-src 'self' data: blob: https://lh3.googleusercontent.com https://avatars.githubusercontent.com",
              "font-src 'self' data: https://fonts.gstatic.com",
              "frame-ancestors 'none'",
            ].join("; "),
          },
        ],
      },
    ];
  },
};

export default nextConfig;
