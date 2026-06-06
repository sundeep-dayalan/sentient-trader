/**
 * CustomNewsForm — Manually inject a headline into the AI pipeline.
 *
 * SECURITY INTEGRATION:
 * - Calls FastAPI /simulate which now enforces auth + rate limits
 * - On 429 (rate limited): shows the specific error message
 * - On 429 with needsAuth=true: triggers the AuthGate modal
 * - On 401: triggers the AuthGate modal
 */

import { useEffect, useState } from 'react';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuth } from '@/components/AuthProvider';

type State = 'idle' | 'loading' | 'success' | 'error';

interface CustomNewsFormProps {
  variant?: 'panel' | 'modal';
  /** Called when the user needs to sign in (triggers AuthGate) */
  onAuthRequired?: () => void;
  onOpenSignals?: () => void;
  onOpenPipeline?: () => void;
}

export default function CustomNewsForm({
  variant = 'panel',
  onAuthRequired,
  onOpenSignals,
  onOpenPipeline,
}: CustomNewsFormProps) {
  const { isAnonymous, user, isLoading: authLoading } = useAuth();
  const [ticker, setTicker] = useState('');
  const [headline, setHeadline] = useState('');
  const [summary, setSummary] = useState('');
  const [articleUrl, setArticleUrl] = useState('');
  const [state, setState] = useState<State>('idle');
  const [errMsg, setErrMsg] = useState('');
  const [remaining, setRemaining] = useState<number | null>(null);

  const canSubmit = ticker.trim().length > 0 && headline.trim().length > 10 && state !== 'loading';

  // Load the real remaining count as soon as auth is ready (without consuming one),
  // so the badge reflects the live value on open and after a refresh.
  useEffect(() => {
    if (authLoading || !user) return;
    let cancelled = false;
    apiFetch<{ remaining?: number }>('/simulate/quota')
      .then((json) => {
        if (!cancelled && typeof json.remaining === 'number') setRemaining(json.remaining);
      })
      .catch(() => {
        /* non-blocking: fall back to the static hint */
      });
    return () => {
      cancelled = true;
    };
  }, [authLoading, user]);

  async function inject() {
    if (!canSubmit) return;
    setState('loading');
    setErrMsg('');

    try {
      const json = await apiFetch<{ remaining?: number }>('/simulate', {
        method: 'POST',
        body: JSON.stringify({
          ticker: ticker.trim().toUpperCase(),
          headline: headline.trim(),
          source: 'simulation',
          summary: summary.trim() || undefined,
          article_url: articleUrl.trim() || undefined,
        }),
      });

      setRemaining(json.remaining ?? null);
      setState('success');
      setHeadline('');
      setSummary('');
      setArticleUrl('');
    } catch (e) {
      if (e instanceof ApiError) {
        const detail =
          e.payload && typeof e.payload === 'object' && 'detail' in e.payload
            ? (e.payload.detail as { needsAuth?: boolean; errorMessage?: string } | string)
            : null;
        if (
          (e.status === 401 || (typeof detail === 'object' && detail?.needsAuth)) &&
          onAuthRequired
        ) {
          onAuthRequired();
          setState('idle');
          return;
        }
        setErrMsg(typeof detail === 'object' ? (detail?.errorMessage ?? e.message) : e.message);
      } else {
        setErrMsg(e instanceof Error ? e.message : 'Unknown error');
      }
      setState('error');
      setTimeout(() => setState('idle'), 6000);
    }
  }

  return (
    <div className={variant === 'modal' ? 'space-y-4' : 'glass-panel h-full rounded-xl p-4'}>
      {/* Header */}
      <div className="flex items-start gap-3">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-accent-border bg-accent-soft">
          <svg
            className="h-3.5 w-3.5 text-accent"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125"
            />
          </svg>
        </div>
        <div>
          <h3 className="text-sm font-semibold leading-tight">Try it with your own news</h3>
          <p className="text-[11px] text-muted">
            {variant === 'modal'
              ? 'Type a news headline and watch the AI decide whether to buy, sell, or pass.'
              : 'Test a headline yourself'}
          </p>
          {/* Show remaining simulations */}
          {remaining !== null && (
            <p className="mt-0.5 text-[10px] font-medium text-accent">
              {remaining} free tr{remaining !== 1 ? 'ies' : 'y'} left today
            </p>
          )}
        </div>
      </div>

      {variant === 'modal' && (
        <div className="mt-4 grid gap-2 sm:grid-cols-3">
          <StepHint number="1" label="You add the news" text="Pick a company and type a headline about it — like one you'd see in the news." />
          <StepHint number="2" label="The AI thinks it over" text="It reads the news, weighs the good and bad, and checks how risky a trade would be." />
          <StepHint number="3" label="You see the decision" text="Its call shows up under Signals, and Pipeline lets you watch how it got there." />
        </div>
      )}

      {/* Inputs */}
      <div className="mt-3 flex flex-col gap-2.5">
        {/* Ticker */}
        <div>
          <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-muted">
            Company symbol *
          </label>
          <input
            type="text"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase().replace(/[^A-Z]/g, ''))}
            placeholder="TSLA"
            maxLength={6}
            disabled={state === 'loading'}
            className="w-full rounded-xl border border-line bg-surface-2 px-3.5 py-2.5 font-mono text-sm font-bold text-primary outline-none transition-colors placeholder:text-muted focus:border-accent-border disabled:opacity-50"
          />
          <p className="mt-1 text-[10px] leading-relaxed text-muted">
            The short code for a company on the stock market — e.g. AAPL for Apple, TSLA for Tesla.
          </p>
        </div>

        {/* Headline */}
        <div>
          <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-muted">
            Headline *
          </label>
          <textarea
            value={headline}
            onChange={(e) => setHeadline(e.target.value)}
            placeholder="Apple beats Q3 earnings by 18%, raises full-year guidance"
            rows={2}
            maxLength={500}
            disabled={state === 'loading'}
            className="w-full resize-none rounded-xl border border-line bg-surface-2 px-3.5 py-2.5 text-sm leading-relaxed text-primary outline-none transition-colors placeholder:text-muted focus:border-accent-border disabled:opacity-50"
          />
          {headline.length > 0 && headline.length <= 10 && (
            <p className="mt-0.5 text-[10px] text-warning">Too short — add a bit more detail.</p>
          )}
          <p className="mt-1 text-[10px] leading-relaxed text-muted">
            Write it like a real news headline. The clearer and more specific it is, the better the AI can explain its thinking.
          </p>
        </div>

        {/* Summary */}
        <div>
          <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-muted">
            More details{' '}
            <span className="text-muted font-normal normal-case">
              (optional — a few sentences gives the AI more to work with)
            </span>
          </label>
          <textarea
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder="Apple reported Q3 revenue of $94.9B, up 5% year-over-year..."
            rows={4}
            maxLength={2000}
            disabled={state === 'loading'}
            className="w-full resize-none rounded-xl border border-line bg-surface-2 px-3.5 py-2.5 text-sm leading-relaxed text-primary outline-none transition-colors placeholder:text-muted focus:border-accent-border disabled:opacity-50"
          />
        </div>

        {/* Article URL */}
        <div>
          <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-muted">
            Link to the article <span className="text-muted font-normal normal-case">(optional)</span>
          </label>
          <input
            type="url"
            value={articleUrl}
            onChange={(e) => setArticleUrl(e.target.value)}
            placeholder="https://..."
            disabled={state === 'loading'}
            className="w-full rounded-xl border border-line bg-surface-2 px-3.5 py-2.5 text-sm text-primary outline-none transition-colors placeholder:text-muted focus:border-accent-border disabled:opacity-50"
          />
        </div>
      </div>

      {/* Submit button */}
      <div className="mt-1">
        <button
          onClick={inject}
          disabled={!canSubmit}
          className={[
            'flex w-full shrink-0 items-center justify-center gap-1.5 rounded-xl border px-4 py-2.5 text-[13px] font-semibold',
            'transition-all duration-200',
            state === 'loading'
              ? 'bg-surface-2 border-line text-muted cursor-not-allowed'
              : state === 'success'
                ? 'bg-positive-soft border-positive-border text-positive cursor-default'
                : state === 'error'
                  ? 'bg-negative-soft border-negative-border text-negative cursor-default'
                  : canSubmit
                    ? 'bg-accent border-accent text-white hover:opacity-90 cursor-pointer shadow-sm'
                    : 'bg-surface-2 border-line text-muted cursor-not-allowed',
          ].join(' ')}
        >
          {state === 'loading' && (
            <>
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-line border-t-muted" />
              Sending to the AI...
            </>
          )}
          {state === 'success' && (
            <>
              <svg
                className="h-3 w-3"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2.5}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
              </svg>
              Sent — the AI is on it
            </>
          )}
          {state === 'error' && (
            <>
              <svg
                className="h-3 w-3"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
              {errMsg.slice(0, 60)}
            </>
          )}
          {state === 'idle' && (
            <>
              <svg className="h-3 w-3" viewBox="0 0 24 24" fill="currentColor">
                <path d="M8 5.14v14l11-7-11-7z" />
              </svg>
              See what the AI decides
            </>
          )}
        </button>
      </div>

      {state === 'success' && (
        <div className="rounded-xl border border-positive-border bg-positive-soft p-3">
          <p className="text-xs font-bold text-positive">Done — the AI is working on it</p>
          <p className="mt-1 text-[11px] leading-relaxed text-positive opacity-80">
            Its decision will show up at the top of Signals in a moment. Open Pipeline to watch it
            think step by step, or tap “View trace” on the result to replay exactly how it decided.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {onOpenSignals && (
              <button
                type="button"
                onClick={onOpenSignals}
                className="rounded-lg border border-positive-border bg-surface px-3 py-1.5 text-xs font-semibold text-positive transition hover:brightness-110"
              >
                Open Signals
              </button>
            )}
            {onOpenPipeline && (
              <button
                type="button"
                onClick={onOpenPipeline}
                className="rounded-lg border border-positive-border bg-surface px-3 py-1.5 text-xs font-semibold text-positive transition hover:brightness-110"
              >
                Open Pipeline
              </button>
            )}
            <button
              type="button"
              onClick={() => setState('idle')}
              className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-semibold text-secondary transition hover:border-accent-border hover:text-accent"
            >
              New simulation
            </button>
          </div>
        </div>
      )}

      {/* Anonymous user hint */}
      {isAnonymous && state === 'idle' && (
        <p className="mt-2 text-center text-[10px] text-muted">
          {remaining === null
            ? '1 free try · Sign in for more'
            : `${remaining} free tr${remaining !== 1 ? 'ies' : 'y'} left · Sign in for more`}
        </p>
      )}
    </div>
  );
}

function StepHint({ number, label, text }: { number: string; label: string; text: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface-2 p-3">
      <div className="flex items-center gap-2">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-accent-soft text-[10px] font-bold text-accent">
          {number}
        </span>
        <p className="text-[11px] font-bold text-primary">{label}</p>
      </div>
      <p className="mt-1.5 text-[10px] leading-relaxed text-muted">{text}</p>
    </div>
  );
}
