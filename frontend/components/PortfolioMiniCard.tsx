"use client";

import { useEffect, useMemo, useState } from "react";
import { BASE_PATH } from "@/lib/config";
import { PortfolioPoint } from "@/lib/types";

const fmtMoney = (value: number) => `$${value.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;

function sparklinePoints(data: PortfolioPoint[], width: number, height: number) {
  if (data.length < 2) return "";

  const values = data.map(point => point.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min;
  const padding = 7;
  const plotHeight = height - padding * 2;

  return values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = range === 0
        ? height / 2
        : padding + plotHeight - ((value - min) / range) * plotHeight;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

export default function PortfolioMiniCard() {
  const [data, setData] = useState<PortfolioPoint[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchPortfolio = async () => {
      try {
        const res = await fetch(`${BASE_PATH}/api/portfolio`);
        const json = await res.json();
        setData(json.history ?? []);
      } catch {
        setData([]);
      } finally {
        setLoading(false);
      }
    };

    fetchPortfolio();
    const interval = setInterval(fetchPortfolio, 60_000);
    return () => clearInterval(interval);
  }, []);

  const latest = data.at(-1)?.equity ?? 0;
  const start = data[0]?.equity ?? latest;
  const pnl = latest - start;
  const isUp = pnl >= 0;
  const points = useMemo(() => sparklinePoints(data, 168, 44), [data]);

  return (
    <div className="rounded-lg border border-line bg-surface-2 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-semibold uppercase text-muted">Portfolio</span>
        <span className="flex items-center gap-1.5 text-[10px] font-semibold text-positive">
          <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-positive" />
          LIVE
        </span>
      </div>

      <div className="mt-3 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-2xl font-semibold leading-none text-primary">
            {loading && latest === 0 ? "$--" : fmtMoney(latest)}
          </p>
          <p className={`mt-1 font-mono text-[11px] font-semibold ${isUp ? "text-positive" : "text-negative"}`}>
            {isUp ? "+" : "-"}{fmtMoney(Math.abs(pnl))}
          </p>
        </div>
        <div className="min-w-0 text-right">
          <p className="text-[10px] text-muted">equity</p>
          <p className="text-[10px] text-muted">7 day</p>
        </div>
      </div>

      <div className="mt-3 h-12 overflow-hidden rounded-md border border-line bg-surface">
        {points ? (
          <svg viewBox="0 0 168 44" className="h-full w-full" preserveAspectRatio="none" aria-hidden="true">
            <line x1="0" x2="168" y1="22" y2="22" stroke="var(--border)" strokeDasharray="4 5" />
            <polyline
              points={points}
              fill="none"
              stroke={isUp ? "var(--positive)" : "var(--negative)"}
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="4"
              vectorEffect="non-scaling-stroke"
            />
          </svg>
        ) : (
          <div className="flex h-full items-center justify-center text-[10px] text-muted">
            {loading ? "Loading equity..." : "No portfolio data"}
          </div>
        )}
      </div>
    </div>
  );
}
