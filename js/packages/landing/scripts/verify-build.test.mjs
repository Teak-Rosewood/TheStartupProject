// The verifier must fail when the site is wrong, or it protects nothing.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const run = (dist) => spawnSync("node", [join(root, "scripts/verify-build.mjs")], { env: { ...process.env, DIST_DIR: dist }, encoding: "utf8" });

async function mutated(edit) {
  const dir = await mkdtemp(join(tmpdir(), "dist-"));
  await cp(join(root, "dist"), dir, { recursive: true });
  await edit(dir);
  return dir;
}
const patch = (file, fn) => async (dir) => writeFile(join(dir, file), fn(await readFile(join(dir, file), "utf8")));

test("an untouched build passes", async () => {
  const r = run(join(root, "dist"));
  assert.equal(r.status, 0, r.stderr);
});

const cases = [
  ["inline script (CSP)", "index.html", (h) => h.replace("</body>", "<script>alert(1)</script></body>"), /inline script/],
  ["missing canonical", "index.html", (h) => h.replace(/<link rel="canonical"[^>]*>/, ""), /canonical/],
  ["two h1 elements", "pricing/index.html", (h) => h.replace("<h2>Get started</h2>", "<h1>Again</h1>"), /exactly one <h1>/],
  ["third-party tracker", "index.html", (h) => h.replace("</head>", '<script src="https://www.google-analytics.com/analytics.js"></script></head>'), /third-party/],
  ["broken internal link", "index.html", (h) => h.replace("</main>", '<a href="/nowhere">x</a></main>'), /broken link \/nowhere/],
  ["image without alt", "index.html", (h) => h.replace("</main>", '<img src="/favicon.svg"></main>'), /without alt/],
  ["unfilled legal placeholder", "legal/terms/index.html", (h) => h.replace("</article>", "{{company_name}}</article>"), /unfilled/],
  ["stale legal version", "legal/privacy/index.html", (h) => h.replace(/Version [0-9a-f]{12}/g, "Version 000000000000"), /current version/],
  ["short description", "pricing/index.html", (h) => h.replace(/(<meta name="description" content=")[^"]*/, "$1too short"), /description/],
];
for (const [name, file, fn, expected] of cases) {
  test(`fails on ${name}`, async () => {
    const dir = await mutated(patch(file, fn));
    try {
      const r = run(dir);
      assert.equal(r.status, 1, `expected failure for ${name}`);
      assert.match(r.stderr, expected);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
}

test("fails when security.txt has expired", async () => {
  const dir = await mutated(patch(".well-known/security.txt", (t) => t.replace(/Expires: .*/, "Expires: 2020-01-01T00:00:00.000Z")));
  try {
    const r = run(dir);
    assert.equal(r.status, 1);
    assert.match(r.stderr, /security\.txt/);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
