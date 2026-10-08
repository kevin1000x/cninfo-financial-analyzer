import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api/proxy": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/proxy/, ""),
        // SSE works out of the box with http-proxy; no special flag needed.
      },
      // finaudit 的问答服务 —— **另一个后端**，不是同一个。
      // 它是无状态请求–响应（没有任务、没有 SSE），所以走自己的路由；
      // 生产环境走自己的 Pages secret。finaudit 仓库里 `python -m service.api` 监听 8100。
      "/api/audit": {
        target: "http://localhost:8100",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/audit/, ""),
      },
    },
  },
});
