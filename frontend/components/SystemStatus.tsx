"use client";

import { useEffect, useState } from "react";

import { BASE_PATH } from "@/lib/config";

type S = "ok" | "stale" | "error" | "unknown" | "loading";

interface StatusData {
  alpaca: S; supabase: S; groq: S; redis: S; agent: S;
  lastTradeAt: string | null;
}

const SERVICES: { key: keyof Omit<StatusData, "lastTradeAt">; label: string }[] = [
  { key: "alpaca",   label: "Alpaca"   },
  { key: "supabase", label: "Supabase" },
  { key: "groq",     label: "Groq"     },
  { key: "redis",    label: "Redis"    },
  { key: "agent",    label: "Agent"    },
];

function dot(s: S) {
  return {
    ok:      "bg-positive",
    stale:   "bg-warning",
    error:   "bg-negative",
    loading: "bg-muted animate-pulse",
    unknown: "bg-muted opacity-50",
  }[s];
}

function relTime(iso: string): string {
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000);
  if (m < 1)  return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.floor(h / 24)}d ago`;
}

const INIT: StatusData = {
  alpaca: "loading", supabase: "loading", groq: "loading",
  redis:  "loading", agent:    "loading", lastTradeAt: null,
};

export default function SystemStatus() {
  const [status, setStatus] = useState<StatusData>(INIT);
  const [, setTick] = useState(0);

  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch(`${BASE_PATH}/api/status`);
        setStatus(await r.json());
      } catch {
        setStatus(s => ({ ...s, alpaca: "error", supabase: "error" }));
      }
    };
    load();
    const poll  = setInterval(load, 30_000);
    const clock = setInterval(() => setTick(t => t + 1), 60_000);
    return () => { clearInterval(poll); clearInterval(clock); };
  }, []);

  const allOk = SERVICES.every(s => status[s.key] === "ok");

  return (
    <div className="hidden items-center gap-1 lg:flex">

      {/* Compact summary dot when all ok */}
      {allOk ? (
        <div className="flex items-center gap-2 rounded-lg border border-positive-border bg-positive-soft px-3 py-2">
          <span className="h-1.5 w-1.5 rounded-full bg-positive" />
          <span className="text-[11px] font-semibold text-positive">Systems operational</span>
          {status.lastTradeAt && (
            <span className="text-[10px] text-positive opacity-75">last trade {relTime(status.lastTradeAt)}</span>
          )}
        </div>
      ) : (
        /* Individual dots when something is off */
        <div className="flex items-center gap-3">
          {SERVICES.map(({ key, label }) => (
            <div key={key} className="flex items-center gap-1.5 group relative">
              <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot(status[key])}`} />
              <span className="text-[11px] text-muted">{label}</span>
            </div>
          ))}
          {status.lastTradeAt && (
            <span className="border-l border-line pl-2 text-[11px] text-muted opacity-60">
              {relTime(status.lastTradeAt)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
