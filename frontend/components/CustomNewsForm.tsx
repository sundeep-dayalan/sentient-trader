"use client";

import { useState } from "react";
import { BASE_PATH } from "@/lib/config";

type State = "idle" | "loading" | "success" | "error";

interface CustomNewsFormProps {
  variant?: "panel" | "modal";
}

export default function CustomNewsForm({ variant = "panel" }: CustomNewsFormProps) {
  const [ticker,   setTicker]   = useState("");
  const [headline, setHeadline] = useState("");
  const [state,    setState]    = useState<State>("idle");
  const [errMsg,   setErrMsg]   = useState("");

  const canSubmit = ticker.trim().length > 0 && headline.trim().length > 10 && state !== "loading";

  async function inject() {
    if (!canSubmit) return;
    setState("loading");
    setErrMsg("");

    try {
      const res = await fetch(`${BASE_PATH}/api/simulate`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          ticker:   ticker.trim().toUpperCase(),
          headline: headline.trim(),
          source:   "custom",
        }),
      });

      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.error ?? `HTTP ${res.status}`);
      }

      setState("success");
      setHeadline("");
      // Reset to idle after 4s so the form can be reused
      setTimeout(() => setState("idle"), 4000);
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : "Unknown error");
      setState("error");
      setTimeout(() => setState("idle"), 4000);
    }
  }

  return (
    <div className={variant === "modal" ? "space-y-4" : "glass-panel h-full rounded-lg p-3.5"}>

      {/* Header */}
      <div className="flex items-start gap-3">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-accent-border bg-accent-soft">
          <svg className="h-3.5 w-3.5 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125" />
          </svg>
        </div>
        <div>
          <h3 className="text-sm font-semibold leading-tight">Signal Injector</h3>
          <p className="text-[11px] text-muted">
            {variant === "modal" ? "Send a ticker and headline through the live AI pipeline." : "Manual headline test"}
          </p>
        </div>
      </div>

      {/* Inputs */}
      <div className="mt-3 flex flex-col gap-2">
        <input
          type="text"
          value={ticker}
          onChange={e => setTicker(e.target.value.toUpperCase().replace(/[^A-Z]/g, ""))}
          placeholder="AAPL"
          maxLength={6}
          disabled={state === "loading"}
          className="w-full rounded-lg border border-line bg-surface-2 px-3 py-2 font-mono text-sm font-bold text-primary outline-none transition-colors placeholder:text-muted focus:border-accent-border disabled:opacity-50"
        />
        <textarea
          value={headline}
          onChange={e => setHeadline(e.target.value)}
          placeholder="Apple beats Q3 earnings by 18%, raises full-year guidance"
          rows={2}
          disabled={state === "loading"}
          className="min-h-[58px] resize-none rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm leading-relaxed text-primary outline-none transition-colors placeholder:text-muted focus:border-accent-border disabled:opacity-50"
        />
      </div>

      {/* Footer row: hint + button */}
      <div className="mt-2.5 flex flex-col gap-2">
        <p className="min-h-3 text-[10px] text-muted">
          {headline.length > 0 && headline.length <= 10 && (
            <span className="text-warning">Headline too short. Add more context.</span>
          )}
        </p>

        <button
          onClick={inject}
          disabled={!canSubmit}
          className={[
            "flex w-full shrink-0 items-center justify-center gap-1.5 rounded-lg border px-4 py-2 text-[12px] font-semibold",
            "transition-all duration-200",
            state === "loading"
              ? "bg-surface-2 border-line text-muted cursor-not-allowed"
            : state === "success"
              ? "bg-positive-soft border-positive-border text-positive cursor-default"
            : state === "error"
              ? "bg-negative-soft border-negative-border text-negative cursor-default"
              : canSubmit
              ? "cyber-button bg-accent border-accent text-white hover:opacity-90 cursor-pointer"
              : "bg-surface-2 border-line text-muted cursor-not-allowed",
          ].join(" ")}
        >
          {state === "loading" && (
            <>
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-line border-t-muted" />
              Injecting...
            </>
          )}
          {state === "success" && (
            <>
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
              </svg>
              Injected
            </>
          )}
          {state === "error" && (
            <>
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
              {errMsg.slice(0, 24)}
            </>
          )}
          {state === "idle" && (
            <>
              <svg className="h-3 w-3" viewBox="0 0 24 24" fill="currentColor">
                <path d="M8 5.14v14l11-7-11-7z" />
              </svg>
              Inject
            </>
          )}
        </button>
      </div>
    </div>
  );
}
