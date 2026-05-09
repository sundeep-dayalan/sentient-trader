"use client";

import { useEffect, useState } from "react";

type ServiceStatus = "ok" | "stale" | "error" | "unknown" | "loading";

interface StatusData {
  alpaca:      ServiceStatus;
  supabase:    ServiceStatus;
  groq:        ServiceStatus;
  redis:       ServiceStatus;
  agent:       ServiceStatus;
  lastTradeAt: string | null;
}

const SERVICES: { key: keyof Omit<StatusData, "lastTradeAt">; label: string }[] = [
  { key: "alpaca",   label: "ALPACA"   },
  { key: "supabase", label: "SUPABASE" },
  { key: "groq",     label: "GROQ"     },
  { key: "redis",    label: "REDIS"    },
  { key: "agent",    label: "AGENT"    },
];

function dotClass(status: ServiceStatus) {
  switch (status) {
    case "ok":      return "bg-positive";
    case "stale":   return "bg-yellow-400";
    case "error":   return "bg-negative";
    case "loading": return "bg-muted animate-pulse";
    default:        return "bg-muted";
  }
}

function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const diffMs = Date.now() - new Date(iso).getTime();
  const diffM  = Math.floor(diffMs / 60_000);
  if (diffM < 1)  return "just now";
  if (diffM < 60) return `${diffM}m ago`;
  const diffH = Math.floor(diffM / 60);
  if (diffH < 24) return `${diffH}h ago`;
  return `${Math.floor(diffH / 24)}d ago`;
}

const LOADING_STATE: StatusData = {
  alpaca: "loading", supabase: "loading", groq: "loading",
  redis:  "loading", agent:    "loading", lastTradeAt: null,
};

export default function SystemStatus() {
  const [status, setStatus] = useState<StatusData>(LOADING_STATE);
  const [tick,   setTick]   = useState(0);

  useEffect(() => {
    async function fetchStatus() {
      try {
        const res  = await fetch("/sentient-trader/api/status");
        const data = await res.json();
        setStatus(data);
      } catch {
        setStatus({ ...LOADING_STATE, alpaca: "error", supabase: "error" });
      }
    }

    fetchStatus();
    const poll = setInterval(fetchStatus, 30_000);
    // Re-render relative time every minute
    const clock = setInterval(() => setTick(t => t + 1), 60_000);
    return () => { clearInterval(poll); clearInterval(clock); };
  }, []);

  // suppress tick warning — used only to force re-render for relative time
  void tick;

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] font-mono">
      <span className="text-muted tracking-widest uppercase">Systems</span>

      {SERVICES.map(({ key, label }) => (
        <span key={key} className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotClass(status[key])}`} />
          <span className="text-muted tracking-wider">{label}</span>
        </span>
      ))}

      {status.lastTradeAt && (
        <span className="text-muted/50 ml-1">
          · last trade {relativeTime(status.lastTradeAt)}
        </span>
      )}
    </div>
  );
}
