"use client";

/**
 * DashboardClient — the interactive shell of the dashboard.
 *
 * Separated from page.tsx because Next.js requires server components
 * and client components to live in different files when the server
 * component passes fetched data down to a client component.
 *
 * State owned here:
 *   - selectedTrade:      which trade row is expanded in the Monologue panel
 *   - simulationMessage:  success banner shown after simulation completes
 */

import { useState } from "react";
import AgentMonologue from "@/components/AgentMonologue";
import LiveTicker     from "@/components/LiveTicker";
import PnLChart       from "@/components/PnLChart";
import SimulateButton from "@/components/SimulateButton";
import StatsBar       from "@/components/StatsBar";
import { Trade }      from "@/lib/types";

interface DashboardClientProps {
  initialTrades: Trade[];
}

export default function DashboardClient({ initialTrades }: DashboardClientProps) {
  const [selectedTrade,      setSelectedTrade]      = useState<Trade | null>(initialTrades[0] ?? null);
  const [simulationMessage,  setSimulationMessage]  = useState<string | null>(null);

  return (
    <main className="min-h-screen bg-background p-4 md:p-6 max-w-7xl mx-auto">

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <header className="flex items-center gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            SENTIENT<span className="text-accent"> TRADER</span>
          </h1>
          <p className="text-[11px] text-muted">
            AI-powered real-time market news analysis · LangGraph + Groq + Alpaca
          </p>
        </div>

        <div className="ml-auto flex items-center gap-2 px-3 py-1.5 bg-surface border border-line rounded-md">
          <span className="pulse-dot w-2 h-2 rounded-full bg-positive" />
          <span className="text-[11px] text-muted font-bold tracking-widest">LIVE</span>
        </div>
      </header>

      {/* ── Stats Bar ───────────────────────────────────────────────────── */}
      <StatsBar trades={initialTrades} />

      {/* ── Simulation success message ───────────────────────────────────── */}
      {simulationMessage && (
        <div className="mb-4 px-4 py-2.5 bg-accent/10 border border-accent/30 rounded-md text-[11px] text-accent">
          {simulationMessage}
        </div>
      )}

      {/* ── Main layout: live feed left, detail panels right ───────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-4">

        {/* Left column — live trade feed */}
        <div>
          <LiveTicker
            initialTrades={initialTrades}
            onTradeSelect={setSelectedTrade}
            selectedId={selectedTrade?.id ?? null}
          />
        </div>

        {/* Right column — reasoning panel + chart + simulate */}
        <div className="flex flex-col gap-4">
          <AgentMonologue trade={selectedTrade} />
          <PnLChart />
          <SimulateButton
            onStart={()    => setSimulationMessage(null)}
            onComplete={() => setSimulationMessage(
              "✓ All 5 scenarios injected into the pipeline. Results will appear in the live feed within seconds."
            )}
          />
        </div>
      </div>

      {/* ── Footer ──────────────────────────────────────────────────────── */}
      <footer className="mt-10 pt-4 border-t border-line text-center text-[10px] text-muted space-x-3">
        <span>Alpaca Paper Trading</span>
        <span>·</span>
        <span>Groq LLaMA 3.1 8B</span>
        <span>·</span>
        <span>LangGraph</span>
        <span>·</span>
        <span>Upstash Kafka + Redis</span>
        <span>·</span>
        <span>Supabase Realtime</span>
        <span>·</span>
        <span>Fly.io + Vercel</span>
      </footer>
    </main>
  );
}
