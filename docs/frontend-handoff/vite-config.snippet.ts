// vite.config.ts excerpt for the cninfo-analyzer-web frontend.
//
// Goal: make the frontend code path-identical between local dev and
// Cloudflare Pages production. In both environments, the browser hits
//   /api/proxy/<...>
// In dev, this Vite proxy forwards to the FastAPI on :8000.
// In prod, the Pages Function at functions/api/proxy/[[path]].ts handles
// the same URL.
//
// The dev proxy deliberately does NOT inject Authorization. Run uvicorn
// without API_TOKEN locally so the backend goes anonymous. If you ever
// need to test the token path locally, add a `headers` block here that
// reads from a .env.local — but don't make that the default.

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api/proxy": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/proxy/, ""),
        // SSE is supported out of the box by http-proxy (which Vite uses).
        // No special flag is needed; the long-lived response stays open.
      },
    },
  },
});
