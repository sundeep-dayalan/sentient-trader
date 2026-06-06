import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { PostHogProvider } from 'posthog-js/react';
import AuthProvider from '@/components/AuthProvider';
import DashboardClient from '@/DashboardClient';
import { initPostHog } from '@/lib/posthog';
import './globals.css';

// Init analytics once before render. Returns null (no-op) when no key is set.
const posthogClient = initPostHog();

const app = (
  <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, '')}>
    <AuthProvider>
      <DashboardClient initialTrades={[]} initialStats={null} />
    </AuthProvider>
  </BrowserRouter>
);

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {posthogClient ? <PostHogProvider client={posthogClient}>{app}</PostHogProvider> : app}
  </React.StrictMode>,
);
