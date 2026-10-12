// Files that depend on configuration: robots.txt and /.well-known/security.txt (RFC 9116).
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const dist = resolve(dirname(fileURLToPath(import.meta.url)), "../dist");
const site = (process.env.SITE_URL ?? "https://app.example.com").replace(/\/$/, "");
const contact = process.env.PUBLIC_SECURITY_EMAIL ?? "security@example.com";

await writeFile(join(dist, "robots.txt"), `User-agent: *\nAllow: /\n\nSitemap: ${site}/sitemap-index.xml\n`);

// security.txt must expire within a year; each deploy refreshes it, so it never silently goes stale.
const expires = new Date(Date.now() + 300 * 86400_000).toISOString();
await mkdir(join(dist, ".well-known"), { recursive: true });
await writeFile(
  join(dist, ".well-known/security.txt"),
  `Contact: mailto:${contact}\nExpires: ${expires}\nPreferred-Languages: en\nCanonical: ${site}/.well-known/security.txt\nPolicy: ${site}/legal/terms\n`,
);
console.log("wrote robots.txt and .well-known/security.txt");
