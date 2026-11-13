import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// docker compose では http://backend:8000、ホストで直接 npm run dev するときは http://localhost:8000
const target = process.env.VITE_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // ブラウザから見て /api も /ws も「localhost:5173」（同一オリジン）になるよう中継する。
    // → Cookie（セッション）がそのまま送られ、CORS の設定も不要になる。
    // changeOrigin: false（既定）で Host ヘッダを書き換えないので、Django には Host: localhost:5173 が届き、
    //   ブラウザが付ける Origin: http://localhost:5173 と一致する（CSRF と WebSocket の Origin 検証が素直に通る）。
    proxy: {
      "/api": { target, changeOrigin: false },
      "/ws": { target: target.replace(/^http/, "ws"), ws: true, changeOrigin: false },
    },
  },
});
