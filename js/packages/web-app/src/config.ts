export interface AppConfig {
  issuer: string;
  clientId: string;
  hostedLoginUrl: string;
  razorpayScript: string;
}

/** Deploy-time settings live in /app/config.json (written from stack outputs), so one build serves any environment. */
export async function loadConfig(fetcher: typeof fetch = fetch): Promise<AppConfig> {
  const res = await fetcher(`${import.meta.env.BASE_URL}config.json`, { cache: "no-store" });
  if (!res.ok) throw new Error(`config.json: HTTP ${res.status}`);
  const cfg = (await res.json()) as Partial<AppConfig>;
  for (const k of ["issuer", "clientId", "hostedLoginUrl"] as const) {
    if (!cfg[k] || /POOL_ID|WEB_CLIENT_ID/.test(cfg[k]!)) throw new Error(`config.json: "${k}" is not set`);
  }
  return cfg as AppConfig;
}
