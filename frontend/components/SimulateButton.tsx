"use client";

import { useState } from "react";
import { SIMULATION_SCENARIOS } from "@/lib/constants";

interface SimulateButtonProps {
  onStart:    () => void;
  onComplete: () => void;
}

export default function SimulateButton({ onStart, onComplete }: SimulateButtonProps) {
  const [isRunning, setIsRunning] = useState(false);
  const [progress,  setProgress]  = useState(0);

  const run = async () => {
    setIsRunning(true);
    setProgress(0);
    onStart();

    for (let i = 0; i < SIMULATION_SCENARIOS.length; i++) {
      try {
        await fetch("/sentient-trader/api/simulate", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(SIMULATION_SCENARIOS[i]),
        });
      } catch (e) {
        console.error("Simulate error:", e);
      }
      setProgress(i + 1);
      if (i < SIMULATION_SCENARIOS.length - 1) {
        await new Promise(r => setTimeout(r, 800));
      }
    }

    setIsRunning(false);
    onComplete();
  };

  const pct  = (progress / SIMULATION_SCENARIOS.length) * 100;
  const next = SIMULATION_SCENARIOS[progress];

  return (
    <div className="bg-surface border border-line rounded-2xl p-5 shadow-card">
      <div className="flex items-start gap-4">

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2.5 mb-1.5">
            <div className="w-6 h-6 rounded-lg bg-accent-soft border border-accent-border flex items-center justify-center shrink-0">
              <svg className="w-3 h-3 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
              </svg>
            </div>
            <h3 className="text-sm font-semibold">Simulate Market Shock</h3>
          </div>

          <p className="text-xs text-muted leading-relaxed">
            Injects {SIMULATION_SCENARIOS.length} historical high-impact headlines into the full AI pipeline.
            Watch real-time analysis, trade decisions, and Alpaca order execution — even when markets are closed.
          </p>

          {isRunning && (
            <div className="mt-3 space-y-2">
              <div className="flex justify-between text-[10px] font-mono text-muted">
                <span>Injecting scenarios…</span>
                <span>{progress} / {SIMULATION_SCENARIOS.length}</span>
              </div>
              <div className="h-1 bg-surface-2 border border-line rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent rounded-full transition-all duration-300"
                  style={{ width: `${pct}%` }}
                />
              </div>
              {next && (
                <p className="text-[10px] text-muted truncate">
                  Next:{" "}
                  <span className="text-accent font-mono font-medium">{next.ticker}</span>
                  {" — "}{next.headline.slice(0, 52)}…
                </p>
              )}
            </div>
          )}
        </div>

        <button
          onClick={run}
          disabled={isRunning}
          className={[
            "shrink-0 flex items-center gap-2 px-4 py-2.5 rounded-xl text-[12px] font-semibold",
            "transition-all duration-200 border",
            isRunning
              ? "bg-surface-2 border-line text-muted cursor-not-allowed"
              : "bg-accent border-accent text-white hover:opacity-90 cursor-pointer shadow-glow",
          ].join(" ")}
        >
          {isRunning ? (
            <>
              <span className="w-3 h-3 rounded-full border-2 border-muted/40 border-t-muted animate-spin" />
              Running
            </>
          ) : (
            <>
              <svg className="w-3 h-3" viewBox="0 0 24 24" fill="currentColor">
                <path d="M8 5.14v14l11-7-11-7z" />
              </svg>
              Simulate
            </>
          )}
        </button>
      </div>
    </div>
  );
}
