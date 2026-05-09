import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sentient Trader",
  description: "AI-powered real-time market news trading agent",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background text-primary antialiased font-mono">
        {children}
      </body>
    </html>
  );
}
