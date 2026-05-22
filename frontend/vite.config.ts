import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

function normalizeBasePath(value: string | undefined): string {
  if (!value) return "/";
  const trimmed = value.trim();
  if (!trimmed || trimmed === "/") return "/";
  return `/${trimmed.replace(/^\/+|\/+$/g, "")}/`;
}

export default defineConfig({
  base: normalizeBasePath(process.env.VITE_BASE_PATH),
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("/@supabase/")) return "supabase";
          if (id.includes("/@xyflow/")) return "flow";
          if (id.includes("/recharts") || id.includes("/victory-vendor")) return "charts";
          if (id.includes("/d3-")) return "d3";
          if (id.includes("/react") || id.includes("/scheduler")) return "react";
          return undefined;
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname),
    },
  },
  server: {
    port: 3000,
  },
});
