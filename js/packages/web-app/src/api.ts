export class ApiError extends Error {
  constructor(
    public status: number,
    public type: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

export interface OrgSummary { id: string; name: string; slug: string; role: string; plan?: string }
export interface Me { sub: string; email: string | null; name: string | null; organizations: OrgSummary[] }
export interface Plan {
  code: string; name: string; kind: string; amount: number; currency: string;
  interval: string | null; credits: number | null; features: Record<string, unknown>;
}
export interface Billing {
  plan: string | null; features: Record<string, unknown>; subscription_status: string | null;
  valid_until: string | null; credits: number;
}
export interface Checkout { provider: string; kind: string; checkout: Record<string, unknown> }
export interface ApiKey {
  id: string; name: string; prefix: string; scopes: string[]; last_used_at: string | null; revoked_at: string | null;
}
export interface ConsentStatus { up_to_date: boolean; required: { id: string; version: string; url: string }[] }
export interface Invoice { id: string; number: string; total: number; currency: string; issued_at: string }

type Token = () => string | undefined;

/** Thin typed client. The API's error envelope is {error: {type, message, details}} (older routes use detail). */
export class Api {
  constructor(
    private token: Token,
    private base = "/api",
    private fetcher: typeof fetch = (...a) => fetch(...a),
  ) {}

  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {};
    const t = this.token();
    if (t) headers.Authorization = `Bearer ${t}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const res = await this.fetcher(`${this.base}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    const data = text ? JSON.parse(text) : undefined;
    if (!res.ok) {
      const e = data?.error ?? data?.detail ?? {};
      throw new ApiError(
        res.status,
        e.type ?? "http_error",
        e.message ?? (typeof e === "string" ? e : res.statusText),
        e.details,
      );
    }
    return data as T;
  }

  me = () => this.request<Me>("GET", "/me");
  orgs = () => this.request<OrgSummary[]>("GET", "/orgs");
  createOrg = (name: string) => this.request<OrgSummary>("POST", "/orgs", { name });

  plans = () => this.request<Plan[]>("GET", "/billing/plans");
  billing = (org: string) => this.request<Billing>("GET", `/orgs/${org}/billing`);
  checkout = (org: string, plan_code: string) =>
    this.request<Checkout>("POST", `/orgs/${org}/billing/checkout`, { plan_code });
  confirm = (org: string, body: Record<string, string | undefined>) =>
    this.request<{ verified: boolean }>("POST", `/orgs/${org}/billing/confirm`, body);
  cancelSubscription = (org: string) => this.request<unknown>("POST", `/orgs/${org}/billing/cancel`, {});

  invoices = (org: string) => this.request<Invoice[]>("GET", `/orgs/${org}/invoices`);
  /** The PDF route needs the bearer token, so it is fetched as a blob rather than linked. */
  async invoicePdf(org: string, id: string): Promise<Blob> {
    const t = this.token();
    const res = await this.fetcher(`${this.base}/orgs/${org}/invoices/${id}/pdf`, {
      headers: t ? { Authorization: `Bearer ${t}` } : {},
    });
    if (!res.ok) throw new ApiError(res.status, "http_error", res.statusText);
    return res.blob();
  }

  apiKeys = (org: string) => this.request<ApiKey[]>("GET", `/orgs/${org}/api-keys`);
  createKey = (org: string, name: string, scopes: string[]) =>
    this.request<{ id: string; key: string }>("POST", `/orgs/${org}/api-keys`, { name, scopes });
  revokeKey = (org: string, id: string) => this.request<void>("DELETE", `/orgs/${org}/api-keys/${id}`);

  consents = () => this.request<ConsentStatus>("GET", "/me/consents");
  acceptConsents = (documents: Record<string, string>) => this.request<unknown>("POST", "/me/consents", { documents });
  requestExport = () => this.request<{ id: string; status: string; url: string | null }>("POST", "/me/export");
  requestDelete = () => this.request<{ scheduled_for: string }>("POST", "/me/delete");
  deletionStatus = () => this.request<{ pending: boolean; scheduled_for: string | null }>("GET", "/me/delete");
  cancelDelete = () => this.request<{ cancelled: boolean }>("POST", "/me/delete/cancel");

  emailPrefs = () => this.request<{ email: string | null; marketing: boolean }>("GET", "/me/email-preferences");
  setMarketing = (marketing: boolean) =>
    this.request<{ marketing: boolean }>("PUT", "/me/email-preferences", { marketing });
}
