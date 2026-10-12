// Fails the build if the site would hurt you in search, in a browser's security policy, or in privacy.
import { readFile, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = process.env.DIST_DIR ?? join(root, "dist");
const site = (process.env.SITE_URL ?? "https://app.example.com").replace(/\/$/, "");
const errors = [];
const warnings = [];
const fail = (page, msg) => errors.push(`${page}: ${msg}`);

async function* walk(dir) {
  for (const e of await readdir(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) yield* walk(p);
    else yield p;
  }
}

const pages = [];
for await (const f of walk(dist)) if (f.endsWith(".html")) pages.push(f);
if (pages.length === 0) { console.error("dist has no HTML: run the build first"); process.exit(1); }

const manifest = JSON.parse(await readFile(join(root, "src/data/documents.json"), "utf8"));
const known = new Set();
for await (const f of walk(dist)) known.add("/" + f.slice(dist.length + 1).replace(/\\/g, "/"));
const resolves = (href) => {
  const path = href.split("#")[0].split("?")[0];
  if (path === "" || path === "/") return true;
  return [path, path + ".html", path + "/index.html"].some((c) => known.has(c));
};

for (const file of pages) {
  const rel = "/" + file.slice(dist.length + 1).replace(/\\/g, "/");
  const html = await readFile(file, "utf8");
  const is404 = rel === "/404.html";
  const need = (cond, msg) => { if (!cond) fail(rel, msg); };

  need(/<html[^>]*\blang="/.test(html), "missing lang attribute");
  const title = html.match(/<title>([^<]*)<\/title>/)?.[1] ?? "";
  need(title.length > 0 && title.length <= 70, `title must be 1-70 characters (got ${title.length})`);
  const desc = html.match(/<meta name="description" content="([^"]*)"/)?.[1] ?? "";
  need(desc.length >= 50 && desc.length <= 160, `meta description must be 50-160 characters (got ${desc.length})`);
  need((html.match(/<h1[\s>]/g) ?? []).length === 1, "exactly one <h1> required");
  if (!is404) {
    const canonical = html.match(/<link rel="canonical" href="([^"]+)"/)?.[1];
    need(canonical?.startsWith(site), `canonical must start with ${site}`);
    for (const p of ["og:title", "og:description", "og:url", "og:image"]) need(html.includes(`property="${p}"`), `missing ${p}`);
  }
  // Content-Security-Policy: no inline scripts (JSON-LD data blocks are not executable)
  for (const m of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
    const attrs = m[1];
    if (/type="application\/ld\+json"/.test(attrs)) { try { JSON.parse(m[2]); } catch { fail(rel, "invalid JSON-LD"); } continue; }
    if (!/\bsrc="/.test(attrs)) fail(rel, "inline script found; the site's CSP forbids it");
  }
  if (/\son[a-z]+="/.test(html)) fail(rel, "inline event handler found");
  if (/<style[\s>]/.test(html)) warnings.push(`${rel}: inline <style> (allowed by the default CSP via style-src 'unsafe-inline')`);
  // privacy: nothing loads from third parties unless you configured a captcha
  for (const m of html.matchAll(/<(?:script|link|img|iframe)\b[^>]*(?:src|href)="(https?:\/\/[^"]+)"/g)) {
    const url = new URL(m[1]);
    const allowed = ["challenges.cloudflare.com", "js.hcaptcha.com", new URL(site).hostname];
    if (!allowed.includes(url.hostname) && !/rel="(canonical|alternate)"/.test(m[0])) fail(rel, `loads ${url.hostname} (third-party request before consent)`);
  }
  for (const m of html.matchAll(/<img\b[^>]*>/g)) if (!/\balt="/.test(m[0])) fail(rel, "image without alt text");
  for (const m of html.matchAll(/<a\b[^>]*\shref="(\/[^"]*)"/g)) if (!resolves(m[1]) && !m[1].startsWith("/api/")) fail(rel, `broken link ${m[1]}`);
  need(/href="\/legal\/terms"/.test(html) && /href="\/legal\/privacy"/.test(html), "footer must link to terms and privacy");
}

for (const [id, doc] of Object.entries(manifest.documents)) {
  const f = join(dist, "legal", id, "index.html");
  if (!existsSync(f)) { fail(`/legal/${id}`, "page missing"); continue; }
  const html = await readFile(f, "utf8");
  if (!html.includes(`Version ${doc.version}`)) fail(`/legal/${id}`, `does not show the current version ${doc.version}`);
  if (/\{\{\s*[a-z_]+\s*\}\}/.test(html)) fail(`/legal/${id}`, "unfilled {{placeholder}} in a published legal document");
}

const robots = existsSync(join(dist, "robots.txt")) ? await readFile(join(dist, "robots.txt"), "utf8") : "";
if (!robots.includes(`Sitemap: ${site}/sitemap-index.xml`)) errors.push("robots.txt must point at the sitemap");
if (!existsSync(join(dist, "sitemap-index.xml"))) errors.push("sitemap-index.xml missing");
const sec = existsSync(join(dist, ".well-known/security.txt")) ? await readFile(join(dist, ".well-known/security.txt"), "utf8") : "";
const expires = sec.match(/^Expires: (.+)$/m)?.[1];
if (!sec.includes("Contact:") || !expires || Date.parse(expires) < Date.now() || Date.parse(expires) > Date.now() + 366 * 86400_000)
  errors.push("security.txt needs a Contact and an Expires date within a year");
if (!existsSync(join(dist, "og.png"))) warnings.push("public/og.png (1200x630) is missing: link previews will have no image");
if (!existsSync(join(dist, "404.html"))) errors.push("404.html missing");

for (const w of warnings) console.warn("warning:", w);
if (errors.length) { console.error(errors.map((e) => `FAIL ${e}`).join("\n")); process.exit(1); }
console.log(`ok: ${pages.length} pages verified`);
