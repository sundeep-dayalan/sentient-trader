import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sentient Trader — AI Market Intelligence",
  description: "Autonomous AI trading agent powered by LangGraph + Groq + Alpaca",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/* Apply saved theme before first paint — prevents light/dark flash */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{if(localStorage.getItem('st-theme')==='light')document.documentElement.classList.add('light');}catch(e){}})();`,
          }}
        />
      </head>
      <body className="min-h-screen bg-background text-primary antialiased">
        {children}
      </body>
    </html>
  );
}
