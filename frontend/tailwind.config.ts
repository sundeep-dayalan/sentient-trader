import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Dark terminal aesthetic — these are used throughout the app as
        // Tailwind utility classes (e.g. bg-surface, text-accent, border-line)
        background: "#0a0a0f",
        surface:    "#12121a",
        line:       "#1e1e2e",  // named "line" to avoid collision with Tailwind's "border" utility
        accent:     "#00d9ff",  // cyan — numbers, highlights
        positive:   "#00ff88",  // green — BUY, gains
        negative:   "#ff4466",  // red   — SELL, losses
        muted:      "#888899",  // grey  — labels, secondary text
        primary:    "#e0e0f0",  // off-white — body text
      },
      fontFamily: {
        mono: ["'JetBrains Mono'", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
