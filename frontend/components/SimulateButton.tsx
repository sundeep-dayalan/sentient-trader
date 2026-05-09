"use client";

/**
 * SimulateButton — the recruiter trap.
 *
 * The market is closed on weekends. Without this button, a recruiter
 * opening the portfolio at 2pm Saturday would see a static dashboard
 * with zero activity — not impressive.
 *
 * What this button does:
 *   Injects 5 historical high-impact headlines into Kafka (via /api/simulate),
 *   staggered 800ms apart. The agent picks them up, runs them through
 *   LangGraph + Groq, and the results pop up in the live feed in real-time.
 *
 * The recruiter watches the AI think and trade live — without needing
 * the stock market to be open. This is the "jaw-dropping" moment.
 *
 * Data flow:
 *   Button click
 *     → fetch /api/simulate  (Next.js API route)
 *       → Upstash Kafka REST API  (publishes to market-news topic)
 *         → Agent Kafka consumer  (picks up the message)
 *           → LangGraph pipeline  (analyze → trade → log)
 *             → Supabase INSERT   (writes the trade record)
 *               → Realtime subscription  (LiveTicker updates on screen)
 */

import { useState } from "react";
import { SIMULATION_SCENARIOS } from "@/lib/constants";

interface SimulateButtonProps {
  onStart:    () => void;
  onComplete: () => void;
}

export default function SimulateButton({ onStart, onComplete }: SimulateButtonProps) {
  const [isRunning, setIsRunning] = useState(false);
  const [progress,  setProgress]  = useState(0);

  const runSimulation = async () => {
    setIsRunning(true);
    setProgress(0);
    onStart();

    for (let i = 0; i < SIMULATION_SCENARIOS.length; i++) {
      try {
        await fetch("/api/simulate", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(SIMULATION_SCENARIOS[i]),
        });
      } catch (err) {
        console.error("Failed to inject scenario:", err);
      }

      setProgress(i + 1);

      // Stagger injections so each headline gets its own moment on screen
      if (i < SIMULATION_SCENARIOS.length - 1) {
        await new Promise((resolve) => setTimeout(resolve, 800));
      }
    }

    setIsRunning(false);
    onComplete();
  };

  const nextScenario = SIMULATION_SCENARIOS[progress];

  return (
    <div className="bg-surface border border-line rounded-lg p-5">
      <div className="flex items-start gap-4">

        {/* Description + progress */}
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-bold text-primary mb-1">Simulate Market Shock</h3>
          <p className="text-[11px] text-muted leading-relaxed">
            Injects {SIMULATION_SCENARIOS.length} historical high-impact headlines into
            the full pipeline. Watch the AI analyze and trade in real-time — even
            when the market is closed.
          </p>

          {isRunning && (
            <div className="mt-3">
              <div className="flex justify-between text-[10px] text-muted mb-1">
                <span>Injecting scenarios...</span>
                <span>{progress} / {SIMULATION_SCENARIOS.length}</span>
              </div>
              <div className="h-1 bg-line rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent transition-all duration-300"
                  style={{ width: `${(progress / SIMULATION_SCENARIOS.length) * 100}%` }}
                />
              </div>
              {nextScenario && (
                <div className="mt-2 text-[10px] text-muted truncate">
                  Next: <span className="text-accent">{nextScenario.ticker}</span>
                  {" — "}{nextScenario.headline.slice(0, 55)}...
                </div>
              )}
            </div>
          )}
        </div>

        {/* The button */}
        <button
          onClick={runSimulation}
          disabled={isRunning}
          className={[
            "shrink-0 px-5 py-2 rounded text-[11px] font-bold tracking-wider",
            "border transition-all duration-200",
            isRunning
              ? "border-muted/30 text-muted cursor-not-allowed"
              : "border-accent text-accent hover:bg-accent hover:text-background cursor-pointer",
          ].join(" ")}
        >
          {isRunning ? "RUNNING..." : "▶ SIMULATE"}
        </button>
      </div>
    </div>
  );
}
