import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background:        "var(--bg)",
        surface:           "var(--surface)",
        "surface-2":       "var(--surface-2)",
        "surface-3":       "var(--surface-3)",
        line:              "var(--border)",
        hover:             "var(--hover)",
        selected:          "var(--selected)",
        accent:            "var(--accent)",
        "accent-soft":     "var(--accent-soft)",
        "accent-border":   "var(--accent-border)",
        cyan:              "var(--cyan)",
        "cyan-soft":       "var(--cyan-soft)",
        "cyan-border":     "var(--cyan-border)",
        warning:           "var(--warning)",
        "warning-soft":    "var(--warning-soft)",
        "warning-border":  "var(--warning-border)",
        positive:          "var(--positive)",
        "positive-soft":   "var(--positive-soft)",
        "positive-border": "var(--positive-border)",
        negative:          "var(--negative)",
        "negative-soft":   "var(--negative-soft)",
        "negative-border": "var(--negative-border)",
        primary:           "var(--text)",
        secondary:         "var(--text-2)",
        muted:             "var(--muted)",
      },
      fontFamily: {
        sans: ["'Plus Jakarta Sans'", "Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["'DM Mono'", "'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      borderRadius: {
        "2xl": "16px",
        "3xl": "24px",
      },
      boxShadow: {
        card:    "0 1px 3px var(--panel-shadow-soft), 0 8px 28px var(--panel-shadow)",
        "card-md": "0 2px 6px var(--panel-shadow-soft), 0 16px 48px var(--panel-shadow)",
        "card-lg": "0 4px 12px var(--panel-shadow-soft), 0 24px 64px var(--panel-shadow)",
        glow:    "0 0 0 3px var(--accent-soft)",
        "glow-positive": "0 0 0 3px var(--positive-soft)",
        "glow-cyan": "0 0 0 3px var(--cyan-soft)",
      },
    },
  },
  plugins: [],
};

export default config;
