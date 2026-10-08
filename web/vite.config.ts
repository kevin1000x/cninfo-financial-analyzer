import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

export default defineConfig(({ mode }) => {
  // Unprefixed values are server-only; Vite only exposes VITE_* to the browser.
  const env = loadEnv(mode, process.cwd(), "");
  const apiBase = env.DEV_API_BASE || "http://127.0.0.1:8000";
  return {
    plugins: [react(), tailwindcss()],
    resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
    server: {
      port: 5173,
      proxy: {
        "/api/proxy": { target: apiBase, changeOrigin: true, rewrite: p => p.replace(/^\/api\/proxy/, "") },
        "/api/audit": {
          target: apiBase,
          changeOrigin: true,
          rewrite: p => p.replace(/^\/api\/audit/, "/audit"),
          // Preserve the user's Authorization header; add only the service credential.
          headers: env.FINAUDIT_API_TOKEN ? { "X-Finaudit-Token": env.FINAUDIT_API_TOKEN } : {},
        },
      },
    },
  };
});
