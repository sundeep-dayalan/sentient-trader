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
        line:              "var(--border)",
        hover:             "var(--hover)",
        selected:          "var(--selected)",
        accent:            "var(--accent)",
        "accent-soft":     "var(--accent-soft)",
        "accent-border":   "var(--accent-border)",
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
        card:    "0 1px 3px 0 rgba(0,0,0,0.07), 0 1px 2px -1px rgba(0,0,0,0.05)",
        "card-md":"0 4px 6px -1px rgba(0,0,0,0.08), 0 2px 4px -2px rgba(0,0,0,0.05)",
        glow:    "0 0 20px var(--accent-soft)",
        "glow-positive": "0 0 16px var(--positive-soft)",
      },
    },
  },
  plugins: [],
};

export default config;
