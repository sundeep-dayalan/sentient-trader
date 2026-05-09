"use client";

/**
 * PnLChart — Portfolio equity curve over the last 7 days.
 *
 * Fetches data from /api/portfolio (server-side Alpaca call) so the
 * secret key never touches the browser. Refreshes every 60 seconds.
 *
 * Shows the starting equity, current equity, and total P&L delta.
 * The line turns red if the portfolio is down from day 1.
 */

import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { PortfolioPoint } from "@/lib/types";

const formatUSD  = (v: number) => `$${v.toLocaleString("en-US", { minimumFractionDigits: 0 })}`;
const formatDate = (ts: string) => new Date(ts).toLocaleDateString("en-US", { month: "short", day: "numeric" });

export default function PnLChart() {
  const [data,    setData]    = useState<PortfolioPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);

  const fetchPortfolio = async () => {
    try {
      const res  = await fetch("/sentient-trader/api/portfolio");
      const json = await res.json();
      setData(json.history ?? []);
      setError(null);
    } catch {
      setError("Could not load portfolio data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPortfolio();
    const interval = setInterval(fetchPortfolio, 60_000);
    return () => clearInterval(interval);
  }, []);

  const latestEquity  = data.length > 0 ? data[data.length - 1].equity : 0;
  const startEquity   = data.length > 0 ? data[0].equity               : 0;
  const pnl           = latestEquity - startEquity;
  const isPositive    = pnl >= 0;
  const lineColor     = isPositive ? "#00ff88" : "#ff4466";

  return (
    <div className="bg-surface border border-line rounded-lg overflow-hidden">

      {/* Header */}
      <div className="flex items-center gap-3 px-5 py-4 border-b border-line">
        <span className="text-[11px] font-bold tracking-widest text-muted uppercase">
          Paper Portfolio (7D)
        </span>
        {data.length > 0 && (
          <>
            <span className="ml-auto text-sm font-bold text-primary">
              {formatUSD(latestEquity)}
            </span>
            <span className={`text-xs font-bold ${isPositive ? "text-positive" : "text-negative"}`}>
              {isPositive ? "+" : ""}{formatUSD(pnl)}
            </span>
          </>
        )}
      </div>

      <div className="p-4 h-44">
        {loading && (
          <div className="h-full flex items-center justify-center text-muted text-sm">
            Loading portfolio...
          </div>
        )}
        {error && (
          <div className="h-full flex items-center justify-center text-negative text-sm">
            {error}
          </div>
        )}
        {!loading && !error && data.length === 0 && (
          <div className="h-full flex items-center justify-center text-muted text-sm">
            No portfolio data yet. Make a trade first.
          </div>
        )}
        {!loading && !error && data.length > 0 && (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" vertical={false} />
              <XAxis
                dataKey="timestamp"
                tickFormatter={formatDate}
                tick={{ fill: "#888899", fontSize: 10 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
                tick={{ fill: "#888899", fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                width={38}
              />
              <Tooltip
                contentStyle={{
                  background:   "#12121a",
                  border:       "1px solid #1e1e2e",
                  borderRadius: "6px",
                  fontSize:     "11px",
                  color:        "#e0e0f0",
                }}
                formatter={(v: number) => [formatUSD(v), "Equity"]}
                labelFormatter={(ts) => new Date(ts).toLocaleString()}
              />
              <Line
                type="monotone"
                dataKey="equity"
                stroke={lineColor}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, fill: "#00d9ff" }}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
