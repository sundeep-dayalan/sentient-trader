import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import AuthProvider from "@/components/AuthProvider";
import DashboardClient from "@/DashboardClient";
import "./globals.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, "")}>
      <AuthProvider>
        <DashboardClient initialTrades={[]} initialStats={null} />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
