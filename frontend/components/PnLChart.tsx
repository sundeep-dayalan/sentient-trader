"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { PortfolioAccountSummary, PortfolioPoint, PortfolioSummary } from "@/lib/types";
import { BASE_PATH } from "@/lib/config";

type PeriodKey = "D" | "W" | "M" | "3M" | "6M" | "Y" | "5Y";
type ChartType = "line" | "area" | "bar";

const PERIODS: { key: PeriodKey; label: string }[] = [
  { key: "D", label: "D" },
  { key: "W", label: "W" },
  { key: "M", label: "M" },
  { key: "3M", label: "3M" },
  { key: "6M", label: "6M" },
  { key: "Y", label: "1Y" },
  { key: "5Y", label: "5Y" },
];
const PERIOD_POLL_MS: Record<PeriodKey, number> = {
  D: 15_000,
  W: 30_000,
  M: 60_000,
  "3M": 120_000,
  "6M": 120_000,
  Y: 120_000,
  "5Y": 300_000,
};

const CHART_TYPES: { value: ChartType; label: string }[] = [
  { value: "line", label: "Line chart" },
  { value: "area", label: "Area chart" },
  { value: "bar", label: "Bar chart" },
];

const fmt = (v: number) => `$${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;

function tickLabel(ts: string, period: PeriodKey) {
  const date = new Date(ts);
  if (period === "D") return date.toLocaleTimeString("en-US", { hour: "numeric" });
  if (period === "5Y") return date.toLocaleDateString("en-US", { year: "numeric" });
  if (period === "6M" || period === "Y") return date.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number }>; label?: string }) {
  if (!active || !payload?.[0]) return null;
  return (
    <div className="rounded-xl border border-[var(--dashboard-border)] bg-[var(--dashboard-card)] px-4 py-3 text-xs shadow-2xl backdrop-blur">
      <p className="mb-1 text-[var(--dashboard-subtle)]">{label ? new Date(label).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}</p>
      <p className="font-mono text-base font-bold text-[var(--dashboard-text)]">{fmt(payload[0].value)}</p>
    </div>
  );
}

export default function PnLChart() {
  const [data, setData] = useState<PortfolioPoint[]>([]);
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [account, setAccount] = useState<PortfolioAccountSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [period, setPeriod] = useState<PeriodKey>("W");
  const [chartType, setChartType] = useState<ChartType>("line");

  const fetchPortfolio = useCallback(async () => {
    try {
      const res = await fetch(`${BASE_PATH}/api/portfolio?range=${period}`, { cache: "no-store" });

      // Auth-protected route: anonymous users get 401/403 — show empty state
      if (res.status === 401 || res.status === 403) {
        setData([]);
        setError(null);
        setLoading(false);
        return;
      }

      const json = await res.json() as {
        history?: PortfolioPoint[];
        summary?: PortfolioSummary;
        account?: PortfolioAccountSummary;
        error?: string;
      };
      if (json.error) {
        throw new Error(json.error);
      }
      setData(json.history ?? []);
      setSummary(json.summary ?? null);
      setAccount(json.account ?? null);
      setError(null);
    } catch {
      setError("Could not load portfolio data");
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => {
    setLoading(true);
    setData([]);
    setSummary(null);
    setAccount(null);
    fetchPortfolio();
    const id = setInterval(fetchPortfolio, PERIOD_POLL_MS[period]);
    return () => clearInterval(id);
  }, [fetchPortfolio, period]);

  const latest = summary?.equity ?? data.at(-1)?.equity ?? 0;
  const start = data[0]?.equity ?? 0;
  const pnl = summary?.profitLoss ?? latest - start;
  const isUp = pnl >= 0;
  const pct = summary?.profitLossPct ?? (start > 0 ? pnl / start : 0);
  const accountLabel = account?.accountNumber ?? (account?.id ? `...${account.id.slice(-6)}` : null);
  const chartMargin = { top: 12, right: 10, left: 0, bottom: 0 };
  const xAxis = (
    <XAxis
      dataKey="timestamp"
      tickFormatter={value => tickLabel(String(value), period)}
      tick={{ fill: "var(--dashboard-subtle)", fontSize: 12 }}
      axisLine={false}
      tickLine={false}
      minTickGap={26}
    />
  );
  const yAxis = (
    <YAxis
      tickFormatter={v => `$${Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
      tick={{ fill: "var(--dashboard-subtle)", fontSize: 12 }}
      axisLine={false}
      tickLine={false}
      width={76}
      domain={["auto", "auto"]}
    />
  );
  const grid = <CartesianGrid strokeDasharray="7 7" stroke="var(--dashboard-chart-grid)" vertical={false} />;
  const tooltip = <Tooltip content={<ChartTooltip />} />;

  return (
    <section className="relative min-h-[360px] overflow-hidden rounded-2xl border border-[var(--dashboard-border)] bg-[var(--dashboard-chart)] p-5 shadow-[var(--dashboard-shadow)] md:min-h-[400px]">
      <div className="pointer-events-none absolute inset-0" style={{ background: "var(--dashboard-chart-overlay)" }} />
      <div className="relative z-10 flex flex-col gap-4">
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_auto]">
          <div className="min-w-0">
            {accountLabel && (
              <div className="mb-5 inline-flex max-w-full items-center gap-2 rounded-full bg-[var(--dashboard-control)] px-3 py-1.5 text-xs font-semibold text-[var(--dashboard-subtle)] shadow-sm">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-positive" />
                <span className="shrink-0 text-[var(--dashboard-text)]">Paper</span>
                <span className="truncate font-mono">{accountLabel}</span>
                {account?.status && <span className="shrink-0 uppercase">{account.status}</span>}
                {account?.currency && <span className="shrink-0">{account.currency}</span>}
              </div>
            )}

            <div>
              <h1 className="text-[34px] font-medium leading-none tracking-normal text-[var(--dashboard-text)] md:text-[42px]">
                Portfolio
              </h1>
            </div>

            <div className="mt-6 flex flex-wrap items-center gap-5">
              <p className="font-sans text-[28px] font-bold leading-none text-[var(--dashboard-text)] md:text-[32px]">
                {loading && latest === 0 ? "—" : fmt(latest)}
              </p>
              <p className={`inline-flex items-center gap-2 font-mono text-base font-semibold ${isUp ? "text-positive" : "text-negative"}`}>
                <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path d={isUp ? "M10 3.5 16.5 15h-13L10 3.5z" : "M10 16.5 3.5 5h13L10 16.5z"} />
                </svg>
                {fmt(Math.abs(pnl))} ({pct >= 0 ? "+" : ""}{(pct * 100).toFixed(2)}%)
              </p>
            </div>
          </div>

          <div className="flex flex-col items-start gap-4 lg:items-end">
            <div className="flex max-w-[340px] flex-wrap justify-end rounded-xl bg-[var(--dashboard-control)] p-1">
              {PERIODS.map(option => (
                <button
                  key={option.key}
                  onClick={() => setPeriod(option.key)}
                  className={[
                    "flex h-10 min-w-10 items-center justify-center rounded-full px-3 text-xs font-semibold transition",
                    option.key === period
                      ? "border border-cyan bg-[var(--dashboard-row)] text-[var(--dashboard-text)] shadow-glow-cyan"
                      : "text-[var(--dashboard-subtle)] hover:text-[var(--dashboard-text)]",
                  ].join(" ")}
                >
                  {option.label}
                </button>
              ))}
            </div>

            <label className="inline-flex h-12 items-center gap-2.5 rounded-xl bg-[var(--dashboard-control)] px-5 text-sm font-semibold text-[var(--dashboard-text)] shadow-sm">
              <svg className="h-5 w-5 text-cyan" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="m3 17 6-6 4 4 8-8" />
                <path d="M14 7h7v7" />
              </svg>
              <select
                aria-label="Chart type"
                value={chartType}
                onChange={event => setChartType(event.target.value as ChartType)}
                className="cursor-pointer appearance-none bg-transparent pr-1 text-sm font-semibold text-[var(--dashboard-text)] outline-none"
              >
                {CHART_TYPES.map(option => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[var(--dashboard-row)] text-[var(--dashboard-subtle)]">
                <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                  <path fillRule="evenodd" d="M5.23 7.23a.75.75 0 0 1 1.06 0L10 10.94l3.71-3.71a.75.75 0 1 1 1.06 1.06l-4.24 4.24a.75.75 0 0 1-1.06 0L5.23 8.29a.75.75 0 0 1 0-1.06z" clipRule="evenodd" />
                </svg>
              </span>
            </label>
          </div>
        </div>

        <div className="relative z-10 h-[170px] md:h-[220px]">
          {loading && (
            <div className="flex h-full items-center justify-center gap-1.5">
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
            <div className="flex h-full items-center justify-center text-sm text-[var(--dashboard-muted)]">
              No portfolio history yet.
            </div>
          )}
          {!loading && !error && data.length > 0 && (
            <ResponsiveContainer width="100%" height="100%">
              {chartType === "line" ? (
                <LineChart data={data} margin={chartMargin}>
                  {grid}
                  {xAxis}
                  {yAxis}
                  {tooltip}
                  <Line
                    type="monotone"
                    dataKey="equity"
                    stroke="var(--dashboard-chart-line)"
                    strokeWidth={4}
                    dot={false}
                    activeDot={{ r: 7, fill: "var(--dashboard-chart)", stroke: "var(--dashboard-chart-line)", strokeWidth: 4 }}
                    isAnimationActive={false}
                  />
                </LineChart>
              ) : chartType === "bar" ? (
                <BarChart data={data} margin={chartMargin}>
                  {grid}
                  {xAxis}
                  {yAxis}
                  {tooltip}
                  <Bar
                    dataKey="equity"
                    fill="var(--dashboard-chart-line)"
                    radius={[6, 6, 0, 0]}
                    maxBarSize={28}
                    isAnimationActive={false}
                  />
                </BarChart>
              ) : (
                <AreaChart data={data} margin={chartMargin}>
                  <defs>
                    <linearGradient id="paper-portfolio-fill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--dashboard-chart-fill-1)" stopOpacity="0.55" />
                      <stop offset="55%" stopColor="var(--dashboard-chart-fill-2)" stopOpacity="0.22" />
                      <stop offset="100%" stopColor="var(--dashboard-chart-fill-3)" stopOpacity="0" />
                    </linearGradient>
                  </defs>
                  {grid}
                  {xAxis}
                  {yAxis}
                  {tooltip}
                  <Area
                    type="monotone"
                    dataKey="equity"
                    stroke="var(--dashboard-chart-line)"
                    strokeWidth={4}
                    fill="url(#paper-portfolio-fill)"
                    fillOpacity={1}
                    dot={false}
                    activeDot={{ r: 7, fill: "var(--dashboard-chart)", stroke: "var(--dashboard-chart-line)", strokeWidth: 4 }}
                    isAnimationActive={false}
                  />
                </AreaChart>
              )}
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </section>
  );
}
