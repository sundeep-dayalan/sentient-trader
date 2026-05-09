"use client";

import { useState } from "react";
import { SIMULATION_SCENARIOS } from "@/lib/constants";
import { BASE_PATH }            from "@/lib/config";

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
        await fetch(`${BASE_PATH}/api/simulate`, {
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
    <div className="glass-panel rounded-lg p-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">

        <div className="flex-1 min-w-0">
          <div className="mb-2 flex items-center gap-2.5">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-accent-border bg-accent-soft">
              <svg className="h-3.5 w-3.5 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
              </svg>
            </div>
            <h3 className="text-sm font-semibold">Simulate Market Shock</h3>
          </div>

          <p className="text-xs leading-relaxed text-muted">
            Injects {SIMULATION_SCENARIOS.length} historical high-impact headlines into the full AI pipeline.
            Watch real-time analysis, trade decisions, and Alpaca order execution even when markets are closed.
          </p>

          {isRunning && (
            <div className="mt-3 space-y-2">
              <div className="flex justify-between font-mono text-[10px] text-muted">
                <span>Injecting scenarios...</span>
                <span>{progress} / {SIMULATION_SCENARIOS.length}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full border border-line bg-surface-2 p-0.5">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-accent to-cyan transition-all duration-300"
                  style={{ width: `${pct}%` }}
                />
              </div>
              {next && (
                <p className="truncate text-[10px] text-muted">
                  Next:{" "}
                  <span className="font-mono font-medium text-accent">{next.ticker}</span>
                  {" - "}{next.headline.slice(0, 52)}...
                </p>
              )}
            </div>
          )}
        </div>

        <button
          onClick={run}
          disabled={isRunning}
          className={[
            "flex shrink-0 items-center justify-center gap-2 rounded-lg border px-4 py-2.5 text-[12px] font-semibold",
            "transition-all duration-200",
            isRunning
              ? "bg-surface-2 border-line text-muted cursor-not-allowed"
              : "cyber-button bg-accent border-accent text-white hover:opacity-90 cursor-pointer",
          ].join(" ")}
        >
          {isRunning ? (
            <>
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-line border-t-muted" />
              Running
            </>
          ) : (
            <>
              <svg className="h-3 w-3" viewBox="0 0 24 24" fill="currentColor">
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
