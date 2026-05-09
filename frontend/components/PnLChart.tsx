"use client";

import { useEffect, useState } from "react";
import {
  CartesianGrid, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { PortfolioPoint } from "@/lib/types";
import { BASE_PATH }      from "@/lib/config";

const fmt  = (v: number) => `$${v.toLocaleString("en-US", { minimumFractionDigits: 0 })}`;
const fmtD = (ts: string) => new Date(ts).toLocaleDateString("en-US", { month: "short", day: "numeric" });

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number }>; label?: string }) {
  if (!active || !payload?.[0]) return null;
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2.5 text-xs shadow-card-md">
      <p className="mb-1 text-muted">{label ? new Date(label).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}</p>
      <p className="font-mono text-sm font-bold text-primary">{fmt(payload[0].value)}</p>
    </div>
  );
}

export default function PnLChart() {
  const [data,    setData]    = useState<PortfolioPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);

  const fetchPortfolio = async () => {
    try {
      const res  = await fetch(`${BASE_PATH}/api/portfolio`);
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
    const id = setInterval(fetchPortfolio, 60_000);
    return () => clearInterval(id);
  }, []);

  const latest = data.at(-1)?.equity ?? 0;
  const start  = data[0]?.equity     ?? 0;
  const pnl    = latest - start;
  const isUp   = pnl >= 0;
  const lineColor = isUp ? "var(--positive)" : "var(--negative)";

  return (
    <div className="glass-panel flex h-full min-h-[220px] flex-col overflow-hidden rounded-lg">

      {/* Header */}
      <div className="flex shrink-0 items-center gap-3 border-b border-line px-4 py-3">
        <div>
          <p className="text-[10px] font-semibold uppercase text-muted">Paper Portfolio</p>
          <p className="mt-0.5 text-xs text-muted">7-day equity curve</p>
        </div>
        {data.length > 0 && (
          <div className="ml-auto text-right">
            <p className="font-mono text-lg font-bold leading-tight text-primary">{fmt(latest)}</p>
            <p className={`font-mono text-xs font-bold ${isUp ? "text-positive" : "text-negative"}`}>
              {isUp ? "+" : "-"}{fmt(Math.abs(pnl))} all time
            </p>
          </div>
        )}
      </div>

      {/* Chart area */}
      <div className="min-h-[160px] flex-1 px-2 py-3">
        {loading && (
          <div className="flex h-full items-center justify-center gap-1">
            {[0, 1, 2].map(i => (
              <div
                key={i}
                className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted"
                style={{ animationDelay: `${i * 120}ms` }}
              />
            ))}
          </div>
        )}
        {error && (
          <div className="flex h-full items-center justify-center text-sm text-negative">{error}</div>
        )}
        {!loading && !error && data.length === 0 && (
          <div className="flex h-full items-center justify-center text-sm text-muted">
            No portfolio history yet. Make a trade first.
          </div>
        )}
        {!loading && !error && data.length > 0 && (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="var(--chart-grid)"
                vertical={false}
              />
              <XAxis
                dataKey="timestamp"
                tickFormatter={fmtD}
                tick={{ fill: "var(--chart-tick)", fontSize: 10 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tickFormatter={v => `$${(v / 1000).toFixed(0)}k`}
                tick={{ fill: "var(--chart-tick)", fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                width={42}
              />
              <Tooltip content={<ChartTooltip />} />
              <Line
                type="monotone"
                dataKey="equity"
                stroke={lineColor}
                strokeWidth={3}
                dot={false}
                activeDot={{ r: 5, fill: "var(--accent)", stroke: "var(--surface)", strokeWidth: 2 }}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
