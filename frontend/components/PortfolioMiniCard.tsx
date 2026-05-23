import { useEffect, useMemo, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { PortfolioPoint } from '@/lib/types';

const fmtMoney = (v: number) => `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;

function sparklinePoints(data: PortfolioPoint[], width: number, height: number) {
  if (data.length < 2) return '';
  const values = data.map((p) => p.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min;
  const pad = 6;
  const plotH = height - pad * 2;
  return values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * width;
      const y = range === 0 ? height / 2 : pad + plotH - ((v - min) / range) * plotH;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}

export default function PortfolioMiniCard() {
  const [data, setData] = useState<PortfolioPoint[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetch_ = async () => {
      try {
        const json = await apiFetch<{ history?: PortfolioPoint[] }>('/portfolio');
        setData(json.history ?? []);
      } catch {
        setData([]);
      } finally {
        setLoading(false);
      }
    };
    fetch_();
    const id = setInterval(fetch_, 60_000);
    return () => clearInterval(id);
  }, []);

  const latest = data.at(-1)?.equity ?? 0;
  const start = data[0]?.equity ?? latest;
  const pnl = latest - start;
  const isUp = pnl >= 0;
  const points = useMemo(() => sparklinePoints(data, 176, 40), [data]);

  return (
    <div className="rounded-xl border border-line bg-surface-2 p-3.5">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-muted">Portfolio</p>
        <div className="flex items-center gap-1.5 text-[10px] font-semibold text-positive">
          <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-positive" />
          LIVE
        </div>
      </div>

      <div className="mt-3">
        <p className="font-mono text-xl font-bold leading-none text-primary">
          {loading && latest === 0 ? '—' : fmtMoney(latest)}
        </p>
        <p
          className={`mt-1 font-mono text-[12px] font-semibold ${isUp ? 'text-positive' : 'text-negative'}`}
        >
          {isUp ? '▲' : '▼'} {fmtMoney(Math.abs(pnl))} all time
        </p>
      </div>

      <div className="mt-3 h-10 overflow-hidden rounded-lg border border-line bg-surface">
        {points ? (
          <svg
            viewBox="0 0 176 40"
            className="h-full w-full"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <defs>
              <linearGradient id="spk-fill" x1="0" y1="0" x2="0" y2="1">
                <stop
                  offset="0%"
                  stopColor={isUp ? 'var(--positive)' : 'var(--negative)'}
                  stopOpacity="0.15"
                />
                <stop
                  offset="100%"
                  stopColor={isUp ? 'var(--positive)' : 'var(--negative)'}
                  stopOpacity="0"
                />
              </linearGradient>
            </defs>
            <polyline
              points={points}
              fill="none"
              stroke={isUp ? 'var(--positive)' : 'var(--negative)'}
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="3"
              vectorEffect="non-scaling-stroke"
            />
          </svg>
        ) : (
          <div className="flex h-full items-center justify-center text-[10px] text-muted">
            {loading ? 'Loading…' : 'No data'}
          </div>
        )}
      </div>
    </div>
  );
}
