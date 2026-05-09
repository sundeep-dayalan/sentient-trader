"use client";

/**
 * AgentMonologue — the "show your work" panel.
 *
 * Clicking a trade row in LiveTicker populates this panel with:
 *   - The raw headline that triggered the analysis
 *   - A visual sentiment bar (–1.0 to +1.0)
 *   - A confidence percentage bar
 *   - The AI's plain-English reasoning string
 *   - The Alpaca order ID (if a trade was actually executed)
 *
 * This is the centerpiece of the demo — it makes the AI's internal logic
 * visible to recruiters who expect a black box.
 */

import { Trade } from "@/lib/types";

interface AgentMonologueProps {
  trade: Trade | null;
}

export default function AgentMonologue({ trade }: AgentMonologueProps) {
  if (!trade) {
    return (
      <div className="bg-surface border border-line rounded-lg p-6 flex items-center justify-center min-h-[220px]">
        <p className="text-muted text-sm">← Click a trade to see the agent&apos;s reasoning</p>
      </div>
    );
  }

  const actionColor =
    trade.trade_action === "BUY"  ? "text-positive" :
    trade.trade_action === "SELL" ? "text-negative"  : "text-muted";

  // Map sentiment –1..1 to 0..100% for the position indicator
  const sentimentBarPct = `${((trade.sentiment_score + 1) / 2) * 100}%`;

  return (
    <div className="bg-surface border border-line rounded-lg overflow-hidden">

      {/* Header */}
      <div className="flex items-center gap-3 px-5 py-4 border-b border-line">
        <span className="text-[11px] font-bold tracking-widest text-muted uppercase">
          Agent Reasoning
        </span>
        <span className={`ml-auto text-lg font-bold ${actionColor}`}>
          {trade.trade_action}
        </span>
        <span className="text-accent font-bold text-lg">{trade.ticker}</span>
      </div>

      <div className="p-5 space-y-5">

        {/* The headline that triggered this analysis */}
        <div>
          <div className="text-[10px] text-muted tracking-widest mb-2">HEADLINE</div>
          <p className="text-sm text-primary leading-relaxed border-l-2 border-accent/50 pl-3">
            &ldquo;{trade.headline}&rdquo;
          </p>
        </div>

        {/* Sentiment score with visual slider */}
        <div>
          <div className="text-[10px] text-muted tracking-widest mb-2">
            SENTIMENT SCORE &nbsp;
            <span className={trade.sentiment_score >= 0 ? "text-positive" : "text-negative"}>
              {trade.sentiment_score >= 0 ? "+" : ""}{trade.sentiment_score.toFixed(3)}
            </span>
          </div>
          <div className="relative h-2 bg-line rounded-full overflow-hidden">
            {/* Neutral center line */}
            <div className="absolute left-1/2 top-0 bottom-0 w-px bg-muted/30" />
            {/* Score dot */}
            <div
              className={`absolute top-0 h-full w-1 rounded-full ${
                trade.sentiment_score >= 0 ? "bg-positive" : "bg-negative"
              }`}
              style={{ left: sentimentBarPct }}
            />
          </div>
          <div className="flex justify-between text-[9px] text-muted mt-1">
            <span>−1.0 BEARISH</span>
            <span>NEUTRAL</span>
            <span>BULLISH +1.0</span>
          </div>
        </div>

        {/* Confidence percentage bar */}
        <div>
          <div className="text-[10px] text-muted tracking-widest mb-2">
            CONFIDENCE &nbsp;
            <span className="text-accent">{(trade.confidence_score * 100).toFixed(1)}%</span>
          </div>
          <div className="h-2 bg-line rounded-full overflow-hidden">
            <div
              className="h-full bg-accent/70 rounded-full transition-all duration-700"
              style={{ width: `${trade.confidence_score * 100}%` }}
            />
          </div>
        </div>

        {/* The AI's plain-English reasoning */}
        <div>
          <div className="text-[10px] text-muted tracking-widest mb-2">AI REASONING</div>
          <p className="text-sm text-primary/90 leading-relaxed font-light">
            {trade.reasoning}
          </p>
        </div>

        {/* Alpaca order confirmation (only shown when a trade fired) */}
        {trade.order_id && (
          <div className="bg-line/40 rounded-md px-3 py-2.5">
            <div className="text-[10px] text-muted tracking-widest mb-1">
              ORDER EXECUTED VIA ALPACA
            </div>
            <div className="text-xs text-accent font-mono break-all">{trade.order_id}</div>
            <div className="text-[10px] text-muted mt-1">
              {trade.quantity} share(s) · Paper Trading · Market Order
            </div>
          </div>
        )}

        <div className="text-[10px] text-muted">
          Analyzed at {new Date(trade.created_at).toLocaleString()}
        </div>
      </div>
    </div>
  );
}
