"use client";

import { useState } from "react";
import AgentMonologue from "@/components/AgentMonologue";
import LiveTicker     from "@/components/LiveTicker";
import PnLChart       from "@/components/PnLChart";
import SimulateButton from "@/components/SimulateButton";
import StatsBar       from "@/components/StatsBar";
import SystemStatus   from "@/components/SystemStatus";
import ThemeToggle    from "@/components/ThemeToggle";
import { Trade }      from "@/lib/types";

const STACK = ["LangGraph", "Groq LLaMA 3.1 8B", "Alpaca", "Redis Streams", "Supabase", "Fly.io"];

interface DashboardClientProps {
  initialTrades: Trade[];
}

export default function DashboardClient({ initialTrades }: DashboardClientProps) {
  const [selectedTrade,     setSelectedTrade]     = useState<Trade | null>(initialTrades[0] ?? null);
  const [simulationMessage, setSimulationMessage] = useState<string | null>(null);

  return (
    <div className="min-h-screen bg-background">

      {/* ── Top navigation bar ─────────────────────────────────────────── */}
      <header className="sticky top-0 z-50 h-14 border-b border-line bg-surface/80 backdrop-blur-xl">
        <div className="max-w-[1400px] mx-auto px-4 md:px-6 h-full flex items-center gap-4">

          {/* Logo */}
          <div className="flex items-center gap-2.5 shrink-0">
            <div className="w-7 h-7 rounded-lg bg-accent-soft border border-accent-border flex items-center justify-center">
              <span className="text-accent text-[11px] font-bold font-mono">ST</span>
            </div>
            <span className="font-semibold text-sm">
              Sentient<span className="text-accent">Trader</span>
            </span>
          </div>

          <div className="w-px h-4 bg-line" />

          <div className="flex items-center gap-1.5">
            <span className="pulse-dot w-1.5 h-1.5 rounded-full bg-positive" />
            <span className="text-[11px] font-semibold text-positive tracking-wider">LIVE</span>
          </div>

          <div className="ml-auto flex items-center gap-3">
            <SystemStatus />
            <ThemeToggle />
          </div>
        </div>
      </header>

      {/* ── Page content ───────────────────────────────────────────────── */}
      <main className="max-w-[1400px] mx-auto px-4 md:px-6 py-6 space-y-5">

        <StatsBar trades={initialTrades} />

        {simulationMessage && (
          <div className="px-4 py-3 bg-positive-soft border border-positive-border rounded-xl text-sm text-positive font-medium">
            {simulationMessage}
          </div>
        )}

        {/* Two-column layout */}
        <div className="grid grid-cols-1 lg:grid-cols-[400px_1fr] gap-4">

          <LiveTicker
            initialTrades={initialTrades}
            onTradeSelect={setSelectedTrade}
            selectedId={selectedTrade?.id ?? null}
          />

          <div className="flex flex-col gap-4">
            <AgentMonologue trade={selectedTrade} />
            <PnLChart />
            <SimulateButton
              onStart={()    => setSimulationMessage(null)}
              onComplete={() => setSimulationMessage(
                "✓ 5 scenarios injected into the live pipeline. Results appear in the feed within seconds."
              )}
            />
          </div>
        </div>
      </main>

      {/* ── Footer ─────────────────────────────────────────────────────── */}
      <footer className="max-w-[1400px] mx-auto px-4 md:px-6 py-5 border-t border-line">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-muted font-medium mr-1">Stack</span>
          {STACK.map(s => (
            <span key={s} className="text-[11px] text-muted px-2 py-0.5 bg-surface-2 border border-line rounded-md">
              {s}
            </span>
          ))}
        </div>
      </footer>
    </div>
  );
}
