import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Prefix all _next/ asset URLs with /sentient-trader so the portfolio proxy
  // rule /sentient-trader/* can forward them to sentienttrader.netlify.app.
  // No basePath — pages are served at root, avoiding redirect loops.
  assetPrefix: "/sentient-trader",
};

export default nextConfig;
