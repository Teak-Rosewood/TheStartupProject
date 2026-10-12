// First-party analytics: events go to your own API (/api/events), only after consent.
import { analyticsAllowed } from "./consent";

const AID = "tsp_aid";
const FIRST_TOUCH = "tsp_ft";
const UTM = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"] as const;

function anonymousId(): string {
  let id = localStorage.getItem(AID);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(AID, id);
  }
  return id;
}

/** Where this visitor came from, remembered from their first visit (only with consent). */
export function firstTouch(): Record<string, string> {
  if (!analyticsAllowed()) return {};
  try {
    const saved = localStorage.getItem(FIRST_TOUCH);
    if (saved) return JSON.parse(saved) as Record<string, string>;
    const q = new URLSearchParams(location.search);
    const ft: Record<string, string> = {};
    for (const k of UTM) if (q.get(k)) ft[k] = q.get(k)!.slice(0, 100);
    if (q.get("ref")) ft.ref = q.get("ref")!.slice(0, 16);
    if (document.referrer && !document.referrer.startsWith(location.origin)) ft.referrer = new URL(document.referrer).hostname;
    ft.landing_path = location.pathname;
    localStorage.setItem(FIRST_TOUCH, JSON.stringify(ft));
    return ft;
  } catch {
    return {};
  }
}

export function track(name: string, properties: Record<string, string | number | boolean> = {}): void {
  if (!analyticsAllowed()) return;
  try {
    const body = JSON.stringify({ events: [{ name, anonymous_id: anonymousId(), path: location.pathname, properties }] });
    if (!navigator.sendBeacon?.("/api/events", new Blob([body], { type: "application/json" }))) {
      void fetch("/api/events", { method: "POST", headers: { "content-type": "application/json" }, body, keepalive: true });
    }
  } catch {
    /* analytics must never break the page */
  }
}

export function initTracking(): void {
  const start = () => {
    firstTouch();
    track(document.body.dataset.event ?? "page_viewed");
  };
  if (analyticsAllowed()) start();
  document.addEventListener("tsp:consent", (e) => {
    if ((e as CustomEvent<{ analytics: boolean }>).detail.analytics) start();
  });
  document.querySelectorAll<HTMLElement>("[data-track]").forEach((el) =>
    el.addEventListener("click", () => track("cta_clicked", { cta: el.dataset.track ?? "unknown" })),
  );
}
