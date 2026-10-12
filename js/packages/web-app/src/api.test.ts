import { describe, expect, it, vi } from "vitest";
import { Api, ApiError } from "./api";
import { loadConfig } from "./config";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("Api", () => {
  it("sends the bearer token and JSON body", async () => {
    const f = vi.fn().mockResolvedValue(json({ id: "o1", name: "Acme", slug: "acme", role: "owner" }, 201));
    const api = new Api(() => "tok", "/api", f);
    await api.createOrg("Acme");
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/orgs");
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(JSON.parse(init.body)).toEqual({ name: "Acme" });
  });

  it("omits Authorization when signed out", async () => {
    const f = vi.fn().mockResolvedValue(json([]));
    await new Api(() => undefined, "/api", f).plans();
    expect(f.mock.calls[0][1].headers.Authorization).toBeUndefined();
  });

  it("turns the error envelope into ApiError", async () => {
    const f = vi.fn().mockResolvedValue(json({ error: { type: "consent_required", message: "accept terms", details: { documents: ["terms"] } } }, 403));
    const err = await new Api(() => "t", "/api", f).me().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 403, type: "consent_required", message: "accept terms", details: { documents: ["terms"] } });
  });

  it("understands the older {detail: {type, message}} errors", async () => {
    const f = vi.fn().mockResolvedValue(json({ detail: { type: "invalid_api_key", message: "bad" } }, 401));
    await expect(new Api(() => "t", "/api", f).me()).rejects.toMatchObject({ type: "invalid_api_key", message: "bad" });
  });

  it("returns undefined for 204", async () => {
    const f = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    await expect(new Api(() => "t", "/api", f).revokeKey("o", "k")).resolves.toBeUndefined();
  });
});

describe("loadConfig", () => {
  it("rejects the placeholder file shipped in public/", async () => {
    const f = vi.fn().mockResolvedValue(json({ issuer: "https://x/POOL_ID", clientId: "WEB_CLIENT_ID", hostedLoginUrl: "https://h" }));
    await expect(loadConfig(f)).rejects.toThrow(/not set/);
  });
  it("accepts real values", async () => {
    const cfg = { issuer: "https://i", clientId: "abc", hostedLoginUrl: "https://h", razorpayScript: "s" };
    await expect(loadConfig(vi.fn().mockResolvedValue(json(cfg)))).resolves.toEqual(cfg);
  });
});
