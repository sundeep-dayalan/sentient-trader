"use client";

import { useEffect, useRef, useState } from "react";
import {
  CartesianGrid, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { PortfolioPoint } from "@/lib/types";

const fmt  = (v: number) => `$${v.toLocaleString("en-US", { minimumFractionDigits: 0 })}`;
const fmtD = (ts: string) => new Date(ts).toLocaleDateString("en-US", { month: "short", day: "numeric" });

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number }>; label?: string }) {
  if (!active || !payload?.[0]) return null;
  return (
    <div className="bg-surface border border-line rounded-xl px-3 py-2.5 shadow-card-md text-xs">
      <p className="text-muted mb-1">{label ? new Date(label).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}</p>
      <p className="font-bold font-mono text-primary text-sm">{fmt(payload[0].value)}</p>
    </div>
  );
}

export default function PnLChart() {
  const [data,    setData]    = useState<PortfolioPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);
  const [lineColor, setLineColor] = useState("#10b981");
  const themeRef = useRef(false);

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
    const id = setInterval(fetchPortfolio, 60_000);
    return () => clearInterval(id);
  }, []);

  // Read accent colors from CSS vars so they match the active theme
  useEffect(() => {
    const update = () => {
      const style = getComputedStyle(document.documentElement);
      const pos = style.getPropertyValue("--positive").trim() || "#10b981";
      const neg = style.getPropertyValue("--negative").trim() || "#ef4444";
      const latest = data.at(-1)?.equity ?? 0;
      const start  = data[0]?.equity     ?? 0;
      setLineColor((latest - start) >= 0 ? pos : neg);
    };
    update();
    themeRef.current = true;
  }, [data]);

  const latest = data.at(-1)?.equity ?? 0;
  const start  = data[0]?.equity     ?? 0;
  const pnl    = latest - start;
  const isUp   = pnl >= 0;

  return (
    <div className="bg-surface border border-line rounded-2xl overflow-hidden shadow-card">

      {/* Header */}
      <div className="flex items-center gap-3 px-5 py-4 border-b border-line">
        <div>
          <p className="text-[10px] font-semibold text-muted uppercase tracking-wider">Paper Portfolio</p>
          <p className="text-xs text-muted mt-0.5">7-day equity curve</p>
        </div>
        {data.length > 0 && (
          <div className="ml-auto text-right">
            <p className="text-lg font-bold font-mono text-primary leading-tight">{fmt(latest)}</p>
            <p className={`text-xs font-bold font-mono ${isUp ? "text-positive" : "text-negative"}`}>
              {isUp ? "↑ +" : "↓ "}{fmt(Math.abs(pnl))} all time
            </p>
          </div>
        )}
      </div>

      {/* Chart area */}
      <div className="px-2 py-4" style={{ height: 200 }}>
        {loading && (
          <div className="h-full flex items-center justify-center gap-1">
            {[0, 1, 2].map(i => (
              <div
                key={i}
                className="w-1.5 h-1.5 rounded-full bg-muted animate-bounce"
                style={{ animationDelay: `${i * 120}ms` }}
              />
            ))}
          </div>
        )}
        {error && (
          <div className="h-full flex items-center justify-center text-negative text-sm">{error}</div>
        )}
        {!loading && !error && data.length === 0 && (
          <div className="h-full flex items-center justify-center text-muted text-sm">
            No portfolio history yet — make a trade first.
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
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, fill: "var(--accent)", strokeWidth: 0 }}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
