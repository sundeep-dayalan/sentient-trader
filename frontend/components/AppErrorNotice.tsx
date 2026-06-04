import { AppErrorCopy } from '@/lib/errors';

const TONE_STYLES = {
  warning: {
    box: 'border-warning-border bg-warning-soft text-warning',
    dot: 'bg-warning',
  },
  danger: {
    box: 'border-negative-border bg-negative-soft text-negative',
    dot: 'bg-negative',
  },
  info: {
    box: 'border-cyan-border bg-cyan-soft text-cyan',
    dot: 'bg-cyan',
  },
} satisfies Record<AppErrorCopy['tone'], { box: string; dot: string }>;

interface AppErrorNoticeProps {
  error: AppErrorCopy;
  compact?: boolean;
  centered?: boolean;
  className?: string;
}

export default function AppErrorNotice({
  error,
  compact = false,
  centered = false,
  className = '',
}: AppErrorNoticeProps) {
  const styles = TONE_STYLES[error.tone];

  return (
    <div
      role="status"
      aria-live="polite"
      className={[
        'flex gap-2.5 rounded-xl border',
        compact ? 'px-3 py-2' : 'px-4 py-3',
        centered ? 'items-center justify-center text-center' : 'items-start',
        styles.box,
        className,
      ].join(' ')}
    >
      <span
        className={[
          'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
          centered ? 'hidden' : '',
          styles.dot,
        ].join(' ')}
      />
      <div className="min-w-0">
        <p className="text-xs font-semibold">{error.title}</p>
        <p className={compact ? 'mt-0.5 text-[11px] leading-4 opacity-80' : 'mt-1 text-xs leading-5 opacity-80'}>
          {error.message}
        </p>
        {error.detail && !compact && (
          <p className="mt-1 font-mono text-[10px] uppercase tracking-wide opacity-60">
            {error.detail}
          </p>
        )}
      </div>
    </div>
  );
}
