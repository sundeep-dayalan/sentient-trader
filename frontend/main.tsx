import React from "react";
import { createRoot } from "react-dom/client";
import AuthProvider from "@/components/AuthProvider";
import DashboardClient from "@/DashboardClient";
import "./globals.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthProvider>
      <DashboardClient initialTrades={[]} initialStats={null} />
    </AuthProvider>
  </React.StrictMode>,
);
