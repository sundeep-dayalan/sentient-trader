"use client";

import { useEffect, useState, useRef } from "react";
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

function dotColor(s: S) {
  return {
    ok:      "bg-positive",
    stale:   "bg-warning",
    error:   "bg-negative",
    loading: "bg-muted animate-pulse",
    unknown: "bg-muted opacity-40",
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
  const [isOpen, setIsOpen] = useState(false);
  const [, setTick] = useState(0);
  const dropdownRef = useRef<HTMLDivElement>(null);

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
    const poll  = setInterval(load, 5_000);
    const clock = setInterval(() => setTick(t => t + 1), 60_000);
    return () => { clearInterval(poll); clearInterval(clock); };
  }, []);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const hasError = SERVICES.some(s => status[s.key] === "error");
  const hasWarning = SERVICES.some(s => status[s.key] === "stale" || status[s.key] === "unknown");
  const isLoading = SERVICES.some(s => status[s.key] === "loading");

  const mainColor = hasError ? "bg-negative" : hasWarning ? "bg-warning" : isLoading ? "bg-muted animate-pulse" : "bg-positive";
  const mainBorder = hasError ? "border-negative-border" : hasWarning ? "border-warning-border" : isLoading ? "border-line" : "border-positive-border";
  const mainBg = hasError ? "bg-negative-soft" : hasWarning ? "bg-warning-soft" : isLoading ? "bg-surface-2" : "bg-positive-soft";
  const mainText = hasError ? "text-negative" : hasWarning ? "text-warning" : isLoading ? "text-muted" : "text-positive";
  const mainLabel = hasError ? "System Outage" : hasWarning ? "Degraded Performance" : isLoading ? "Checking Status..." : "All systems operational";

  return (
    <div className="hidden lg:block relative" ref={dropdownRef}>
      <button 
        onClick={() => setIsOpen(!isOpen)}
        className={`flex items-center gap-2 rounded-xl border ${mainBorder} ${mainBg} px-3 py-2 transition-colors hover:brightness-110 cursor-pointer w-full focus:outline-none`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${mainColor}`} />
        <span className={`text-xs font-semibold ${mainText} whitespace-nowrap`}>{mainLabel}</span>
        {status.lastTradeAt && (
          <span className={`text-[11px] ${mainText} opacity-70 whitespace-nowrap`}>· {relTime(status.lastTradeAt)}</span>
        )}
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full mt-2 w-56 rounded-xl border border-line bg-surface p-3 shadow-card-md z-50">
          <div className="flex flex-col gap-3">
            {SERVICES.map(({ key, label }) => (
              <div key={key} className="flex items-center justify-between gap-3">
                <span className="text-xs text-secondary">{label}</span>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] uppercase tracking-wider text-muted opacity-80">{status[key]}</span>
                  <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColor(status[key])}`} />
                </div>
              </div>
            ))}
            {status.lastTradeAt && (
              <div className="mt-1 border-t border-line pt-3 flex justify-between items-center">
                <span className="text-[10px] text-muted uppercase tracking-wider">Last Trade</span>
                <span className="text-xs text-secondary">
                  {relTime(status.lastTradeAt)}
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
