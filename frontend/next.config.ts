import type { NextConfig } from "next";

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
            key: "Content-Security-Policy-Report-Only",
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
              "connect-src 'self' https://*.supabase.co wss://*.supabase.co https://*.upstash.io",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: blob:",
              "font-src 'self' data:",
            ].join("; "),
          },
        ],
      },
    ];
  },
};

export default nextConfig;
