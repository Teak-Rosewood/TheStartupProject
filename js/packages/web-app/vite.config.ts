import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Served from /app/ by CloudFront; the API is same-origin under /api, so there is no CORS to configure.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  test: { environment: "jsdom", globals: true },
});
