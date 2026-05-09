"use client";

import { useState } from "react";
import { BASE_PATH } from "@/lib/config";

type State = "idle" | "loading" | "success" | "error";

export default function CustomNewsForm() {
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
    <div className="bg-surface border border-line rounded-2xl p-5 shadow-card space-y-4">

      {/* Header */}
      <div className="flex items-center gap-2.5">
        <div className="w-6 h-6 rounded-lg bg-accent-soft border border-accent-border flex items-center justify-center shrink-0">
          <svg className="w-3 h-3 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125" />
          </svg>
        </div>
        <div>
          <h3 className="text-sm font-semibold leading-none">Custom Headline</h3>
          <p className="text-[11px] text-muted mt-0.5">
            Inject any ticker + headline — runs the full AI pipeline, bypasses cache
          </p>
        </div>
      </div>

      {/* Inputs */}
      <div className="flex gap-2">
        <input
          type="text"
          value={ticker}
          onChange={e => setTicker(e.target.value.toUpperCase().replace(/[^A-Z]/g, ""))}
          placeholder="AAPL"
          maxLength={6}
          disabled={state === "loading"}
          className="w-20 shrink-0 px-3 py-2 rounded-xl text-sm font-mono font-bold text-primary bg-surface-2 border border-line focus:border-accent-border focus:outline-none transition-colors placeholder:text-muted disabled:opacity-50"
        />
        <textarea
          value={headline}
          onChange={e => setHeadline(e.target.value)}
          placeholder='e.g. "Apple beats Q3 earnings by 18%, raises full-year guidance"'
          rows={2}
          disabled={state === "loading"}
          className="flex-1 px-3 py-2 rounded-xl text-sm text-primary bg-surface-2 border border-line focus:border-accent-border focus:outline-none transition-colors resize-none placeholder:text-muted leading-relaxed disabled:opacity-50"
        />
      </div>

      {/* Footer row: hint + button */}
      <div className="flex items-center gap-3">
        <p className="text-[10px] text-muted flex-1">
          {headline.length > 0 && headline.length <= 10 && (
            <span className="text-yellow-500">Headline too short — add more context</span>
          )}
          {headline.length === 0 && "Tip: be specific — vague headlines get low confidence scores"}
        </p>

        <button
          onClick={inject}
          disabled={!canSubmit}
          className={[
            "shrink-0 flex items-center gap-1.5 px-4 py-2 rounded-xl text-[12px] font-semibold",
            "transition-all duration-200 border",
            state === "loading"
              ? "bg-surface-2 border-line text-muted cursor-not-allowed"
              : state === "success"
              ? "bg-positive-soft border-positive-border text-positive cursor-default"
              : state === "error"
              ? "bg-negative-soft border-negative-border text-negative cursor-default"
              : canSubmit
              ? "bg-accent border-accent text-white hover:opacity-90 cursor-pointer shadow-glow"
              : "bg-surface-2 border-line text-muted cursor-not-allowed",
          ].join(" ")}
        >
          {state === "loading" && (
            <>
              <span className="w-3 h-3 rounded-full border-2 border-muted/40 border-t-muted animate-spin" />
              Injecting…
            </>
          )}
          {state === "success" && (
            <>
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
              </svg>
              Injected
            </>
          )}
          {state === "error" && (
            <>
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
              {errMsg.slice(0, 24)}
            </>
          )}
          {state === "idle" && (
            <>
              <svg className="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
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
