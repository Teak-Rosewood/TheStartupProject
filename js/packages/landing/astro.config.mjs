import sitemap from "@astrojs/sitemap";
import { defineConfig } from "astro/config";

// SITE_URL is the public address (no trailing slash): canonical links, the sitemap and
// security.txt all derive from it.
const site = process.env.SITE_URL ?? "https://app.example.com";

export default defineConfig({
  site,
  output: "static",
  trailingSlash: "never",
  build: { format: "directory", inlineStylesheets: "never" }, // no inline CSS/JS: the CSP forbids it
  integrations: [sitemap()],
  devToolbar: { enabled: false },
  vite: { build: { assetsInlineLimit: 0 } }, // keep scripts in files so the CSP needs no inline allowance
});
