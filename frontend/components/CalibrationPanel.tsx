import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { Calibration, CalibrationBucket } from '@/lib/types';

/**
 * Outcome-driven calibration: of signals where the committee said BUY/SELL with
 * a given conviction, what was the average forward return and how often was the
 * call directionally right? All data comes from the already-persisted
 * signal_outcomes table via GET /calibration — no extra cost.
 */
function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%`;
}

function rate(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${Math.round(value * 100)}%`;
}

function edgeClass(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'text-muted';
  if (value > 0) return 'text-positive';
  if (value < 0) return 'text-negative';
  return 'text-muted';
}

export default function CalibrationPanel() {
  const [data, setData] = useState<Calibration | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    apiFetch<{ calibration: Calibration }>('/calibration')
      .then((json) => {
        if (cancelled) return;
        setData(json.calibration);
        setError(null);
      })
      .catch(() => {
        if (cancelled) return;
        setError('Calibration data is unavailable right now.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const buckets: CalibrationBucket[] = data?.buckets ?? [];

  return (
    <div className="glass-panel-subtle rounded-xl p-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-bold text-primary">Signal Calibration</h3>
          <p className="mt-0.5 text-[11px] text-muted">
            Forward returns by committee conviction · edge flips sign for SELL so + = correct
          </p>
        </div>
        {data && (
          <span className="shrink-0 rounded-full bg-accent/10 px-2 py-1 font-mono text-[11px] text-accent">
            {data.labeledSignals} labeled
          </span>
        )}
      </div>

      {loading && <p className="mt-4 text-xs text-muted">Loading calibration…</p>}
      {!loading && error && <p className="mt-4 text-xs text-muted">{error}</p>}
      {!loading && !error && buckets.length === 0 && (
        <p className="mt-4 text-xs text-muted">
          No labeled outcomes yet. Calibration appears once signals have forward returns.
        </p>
      )}

      {!loading && !error && buckets.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="text-[11px] uppercase tracking-wide text-muted">
                <th className="py-1.5 pr-3 font-semibold">Action</th>
                <th className="py-1.5 pr-3 font-semibold">Conviction</th>
                <th className="py-1.5 pr-3 text-right font-semibold">n</th>
                <th className="py-1.5 pr-3 text-right font-semibold">15m</th>
                <th className="py-1.5 pr-3 text-right font-semibold">1h</th>
                <th className="py-1.5 pr-3 text-right font-semibold">EOD edge</th>
                <th className="py-1.5 text-right font-semibold">Hit rate</th>
              </tr>
            </thead>
            <tbody className="font-mono text-xs">
              {buckets.map((b) => (
                <tr key={`${b.action}-${b.bucket}`} className="border-t border-[var(--dashboard-divider)]">
                  <td className="py-1.5 pr-3">
                    <span
                      className={`font-sans font-semibold ${
                        b.action === 'BUY' ? 'text-positive' : 'text-negative'
                      }`}
                    >
                      {b.action}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 text-muted">{b.bucket}</td>
                  <td className="py-1.5 pr-3 text-right text-primary">{b.signals}</td>
                  <td className="py-1.5 pr-3 text-right text-muted">{pct(b.avg_return_15m)}</td>
                  <td className="py-1.5 pr-3 text-right text-muted">{pct(b.avg_return_1h)}</td>
                  <td className={`py-1.5 pr-3 text-right font-semibold ${edgeClass(b.avg_edge_eod)}`}>
                    {pct(b.avg_edge_eod)}
                  </td>
                  <td className="py-1.5 text-right text-primary">{rate(b.win_rate_eod)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
