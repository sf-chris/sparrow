import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const backend =
    loadEnv(mode, ".", "SPARROW_").SPARROW_DEV_BACKEND ||
    "http://localhost:8888";
  return {
    plugins: [react()],
    server: {
      port: 3000,
      proxy: {
        // Preserve the browser Host so Sparrow can validate same-origin actions.
        "/api": { target: backend, changeOrigin: false },
        "/ws": {
          target: backend.replace(/^http/, "ws"),
          ws: true,
          changeOrigin: false,
        },
        "/art": { target: backend, changeOrigin: false },
      },
    },
  };
});
