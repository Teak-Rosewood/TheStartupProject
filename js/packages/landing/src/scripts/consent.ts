// Cookie/analytics consent. Opt-in: nothing is stored or sent until the visitor says yes, and browser
// privacy signals (Global Privacy Control, Do Not Track) count as a "no" without asking.
export type Consent = { analytics: boolean; v: 1; ts: number };
const KEY = "tsp_consent";

const optedOutBySignal = () =>
  (navigator as unknown as { globalPrivacyControl?: boolean }).globalPrivacyControl === true || navigator.doNotTrack === "1";

export function getConsent(): Consent | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Consent) : null;
  } catch {
    return null;
  }
}

export function analyticsAllowed(): boolean {
  return !optedOutBySignal() && getConsent()?.analytics === true;
}

export function setConsent(analytics: boolean): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ analytics, v: 1, ts: Date.now() } satisfies Consent));
  } catch {
    /* private mode: the choice just will not persist */
  }
  document.dispatchEvent(new CustomEvent("tsp:consent", { detail: { analytics } }));
}

export function initBanner(): void {
  const banner = document.getElementById("cookie-banner");
  if (!banner) return;
  const show = () => banner.removeAttribute("hidden");
  const hide = () => banner.setAttribute("hidden", "");
  if (!getConsent() && !optedOutBySignal()) show();
  banner.querySelector<HTMLButtonElement>("[data-accept]")?.addEventListener("click", () => { setConsent(true); hide(); });
  banner.querySelector<HTMLButtonElement>("[data-reject]")?.addEventListener("click", () => { setConsent(false); hide(); });
  document.querySelectorAll<HTMLElement>("[data-cookie-settings]").forEach((el) =>
    el.addEventListener("click", () => { show(); banner.querySelector<HTMLButtonElement>("button")?.focus(); }),
  );
}
