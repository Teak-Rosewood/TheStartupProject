// Prepare generated content: rendered legal documents (with their versions) and the plan list.
// Legal text comes from `make legal-render` (docs/legal + your company.toml), so the site, the API
// and the consent records always agree on which version of a document is current.
import { cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "smol-toml";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repo = resolve(root, "../../..");
const legalSrc = process.env.LEGAL_DIR ?? join(repo, "python/apps/api/legal");
const plansFile = process.env.PLANS_FILE ?? join(repo, "python/apps/api/plans.toml");

if (!existsSync(join(legalSrc, "documents.json"))) {
  console.error(`No rendered legal documents at ${legalSrc}.\nRun \`make legal-render\` from the repository root first.`);
  process.exit(1);
}

const manifest = JSON.parse(await readFile(join(legalSrc, "documents.json"), "utf8"));
const out = join(root, "src/content/legal");
await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
for (const doc of Object.values(manifest.documents)) {
  const body = await readFile(join(legalSrc, `${doc.id}.md`), "utf8");
  const front = `---\ntitle: ${JSON.stringify(doc.title)}\nversion: ${JSON.stringify(doc.version)}\ndocId: ${JSON.stringify(doc.id)}\n---\n\n`;
  await writeFile(join(out, `${doc.id}.md`), front + body);
}
await mkdir(join(root, "src/data"), { recursive: true });
await cp(join(legalSrc, "documents.json"), join(root, "src/data/documents.json"));

let plans = [];
if (existsSync(plansFile)) {
  plans = (parse(await readFile(plansFile, "utf8")).plan ?? []).map((p) => ({
    code: p.code, name: p.name, kind: p.kind, amount: p.amount, currency: p.currency ?? "INR",
    interval: p.interval, credits: p.credits ?? 0, features: p.features ?? {},
  }));
}
await writeFile(join(root, "src/data/plans.json"), JSON.stringify(plans, null, 2));
console.log(`synced ${Object.keys(manifest.documents).length} legal documents and ${plans.length} plans (${(await readdir(out)).length} files)`);
