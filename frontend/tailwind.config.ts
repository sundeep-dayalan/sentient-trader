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
        // All tokens resolve from CSS custom properties so both themes work
        // without any dark: prefix in components.
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
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      borderRadius: {
        "2xl": "16px",
        "3xl": "20px",
      },
      boxShadow: {
        card:    "0 18px 44px var(--panel-shadow), inset 0 1px 0 rgba(255,255,255,0.04)",
        "card-md":"0 24px 70px var(--panel-shadow), inset 0 1px 0 rgba(255,255,255,0.05)",
        glow:    "0 0 24px var(--accent-soft)",
        "glow-positive": "0 0 24px var(--positive-soft)",
        "glow-cyan": "0 0 24px var(--cyan-soft)",
      },
    },
  },
  plugins: [],
};

export default config;
