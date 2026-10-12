import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import type { ApiKey, Billing, ConsentStatus, Invoice, Plan } from "./api";
import { openRazorpay } from "./checkout";
import { useSession } from "./session";

const money = (minor: number, cur: string) =>
  new Intl.NumberFormat(undefined, { style: "currency", currency: cur }).format(minor / 100);

function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): [T | undefined, string | null, () => void] {
  const [data, setData] = useState<T>();
  const [err, setErr] = useState<string | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    fn().then(setData, (e) => setErr(String(e.message ?? e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n]);
  return [data, err, () => setN((x) => x + 1)];
}

export function Orgs() {
  const { me, api, reload } = useSession();
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const create = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api.createOrg(name);
      setName("");
      await reload();
    } catch (x) {
      setErr((x as Error).message);
    }
  };
  return (
    <section>
      <h1>Organizations</h1>
      <ul>
        {me.organizations.map((o) => (
          <li key={o.id}>
            <Link to={`/orgs/${o.id}`}>{o.name}</Link> <small>({o.role})</small>
          </li>
        ))}
      </ul>
      <form onSubmit={create}>
        <label>
          New organization <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} />
        </label>
        <button>Create</button>
      </form>
      {err && <p role="alert">{err}</p>}
    </section>
  );
}

export function Org() {
  const { orgId = "" } = useParams();
  const { me } = useSession();
  const org = me.organizations.find((o) => o.id === orgId);
  if (!org) return <p>Organization not found.</p>;
  return (
    <section>
      <h1>{org.name}</h1>
      <ul>
        <li><Link to={`/orgs/${orgId}/billing`}>Billing and invoices</Link></li>
        <li><Link to={`/orgs/${orgId}/keys`}>API keys</Link></li>
      </ul>
    </section>
  );
}

export function BillingPage() {
  const { orgId = "" } = useParams();
  const { api, cfg } = useSession();
  const [plans] = useLoad<Plan[]>(() => api.plans(), []);
  const [billing, err, refresh] = useLoad<Billing>(() => api.billing(orgId), [orgId]);
  const [invoices] = useLoad<Invoice[]>(() => api.invoices(orgId), [orgId]);
  const [msg, setMsg] = useState<string | null>(null);

  const buy = async (code: string) => {
    setMsg(null);
    try {
      const co = await api.checkout(orgId, code);
      const r = await openRazorpay(co, cfg.razorpayScript);
      if (!r) return;
      const { verified } = await api.confirm(orgId, {
        payment_id: r.razorpay_payment_id,
        signature: r.razorpay_signature,
        order_id: r.razorpay_order_id,
        subscription_id: r.razorpay_subscription_id,
      });
      setMsg(verified ? "Payment received. Your plan updates in a few seconds." : "We could not verify that payment.");
      setTimeout(refresh, 4000);
    } catch (e) {
      setMsg((e as Error).message);
    }
  };
  const pdf = async (id: string, number: string) => {
    const url = URL.createObjectURL(await api.invoicePdf(orgId, id));
    const a = Object.assign(document.createElement("a"), { href: url, download: `${number}.pdf` });
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section>
      <h1>Billing</h1>
      {err && <p role="alert">{err}</p>}
      {billing && (
        <p>
          Plan: <strong>{billing.plan ?? "free"}</strong> · credits: {billing.credits}
          {billing.subscription_status && <> · {billing.subscription_status}</>}
        </p>
      )}
      <h2>Plans</h2>
      <ul>
        {plans?.map((p) => (
          <li key={p.code}>
            {p.name}: {money(p.amount, p.currency)}
            {p.interval ? ` / ${p.interval}` : ""}{" "}
            <button onClick={() => buy(p.code)} disabled={billing?.plan === p.code}>
              {billing?.plan === p.code ? "Current plan" : "Choose"}
            </button>
          </li>
        ))}
      </ul>
      {msg && <p role="status">{msg}</p>}
      <h2>Invoices</h2>
      <ul>
        {invoices?.map((i) => (
          <li key={i.id}>
            {i.number} · {money(i.total, i.currency)} · {new Date(i.issued_at).toLocaleDateString()}{" "}
            <button onClick={() => pdf(i.id, i.number)}>PDF</button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function Keys() {
  const { orgId = "" } = useParams();
  const { api } = useSession();
  const [keys, err, refresh] = useLoad<ApiKey[]>(() => api.apiKeys(orgId), [orgId]);
  const [name, setName] = useState("");
  const [fresh, setFresh] = useState<string | null>(null);
  const create = async (e: FormEvent) => {
    e.preventDefault();
    const r = await api.createKey(orgId, name, ["chat"]);
    setFresh(r.key);
    setName("");
    refresh();
  };
  return (
    <section>
      <h1>API keys</h1>
      {err && <p role="alert">{err}</p>}
      {fresh && (
        <p role="status">
          Copy this key now; it is not shown again: <code>{fresh}</code>
        </p>
      )}
      <ul>
        {keys?.filter((k) => !k.revoked_at).map((k) => (
          <li key={k.id}>
            {k.name} <code>{k.prefix}…</code>{" "}
            <button onClick={async () => { await api.revokeKey(orgId, k.id); refresh(); }}>Revoke</button>
          </li>
        ))}
      </ul>
      <form onSubmit={create}>
        <label>Name <input value={name} onChange={(e) => setName(e.target.value)} required /></label>
        <button>Create key</button>
      </form>
    </section>
  );
}

export function Settings() {
  const { api, me } = useSession();
  const [consent, , refreshConsent] = useLoad<ConsentStatus>(() => api.consents(), []);
  const [prefs, , refreshPrefs] = useLoad(() => api.emailPrefs(), []);
  const [del, , refreshDel] = useLoad(() => api.deletionStatus(), []);
  const [exportUrl, setExportUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = (fn: () => Promise<unknown>) => async () => {
    setErr(null);
    try { await fn(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <section>
      <h1>Settings</h1>
      <p>Signed in as {me.email}</p>
      {err && <p role="alert">{err}</p>}

      <h2>Legal documents</h2>
      {consent?.up_to_date ? (
        <p>You have accepted the current terms and privacy policy.</p>
      ) : (
        <>
          <ul>{consent?.required.map((d) => <li key={d.id}><a href={d.url}>{d.id}</a> (version {d.version})</li>)}</ul>
          <button onClick={run(async () => {
            await api.acceptConsents(Object.fromEntries(consent!.required.map((d) => [d.id, d.version])));
            refreshConsent();
          })}>I have read and accept</button>
        </>
      )}

      <h2>Email</h2>
      <label>
        <input type="checkbox" checked={prefs?.marketing ?? false}
          onChange={run(async () => { await api.setMarketing(!prefs?.marketing); refreshPrefs(); })} />{" "}
        Product news and offers (account and billing emails are always sent)
      </label>

      <h2>Your data</h2>
      <button onClick={run(async () => setExportUrl((await api.requestExport()).url))}>Export my data</button>
      {exportUrl && <p><a href={exportUrl}>Download your export</a> (link expires in an hour)</p>}
      {del?.pending ? (
        <p>
          Your account is scheduled for deletion on {new Date(del.scheduled_for!).toLocaleDateString()}.{" "}
          <button onClick={run(async () => { await api.cancelDelete(); refreshDel(); })}>Cancel deletion</button>
        </p>
      ) : (
        <button onClick={run(async () => {
          if (confirm("Delete your account? Your data is erased after a 30 day grace period and you are signed out now.")) {
            await api.requestDelete();
            refreshDel();
          }
        })}>Delete my account</button>
      )}
    </section>
  );
}
