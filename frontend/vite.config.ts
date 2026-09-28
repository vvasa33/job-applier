import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiOrigin = process.env.JOBHUNTER_API_ORIGIN ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/health": apiOrigin,
      "/api": apiOrigin,
    },
  },
});
