import { firstTouch, track } from "./track";

export function initWaitlist(): void {
  const form = document.querySelector<HTMLFormElement>("form.waitlist");
  if (!form) return;
  const status = form.querySelector<HTMLElement>("[role=status]")!;
  const submit = form.querySelector<HTMLButtonElement>("button[type=submit]")!;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    status.className = "";
    status.textContent = "";
    const data = new FormData(form);
    submit.disabled = true;
    try {
      const res = await fetch("/api/leads", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          email: String(data.get("email") ?? ""),
          consent: data.get("consent") === "on",
          source: form.dataset.source ?? "web",
          website: String(data.get("website") ?? ""), // honeypot
          attribution: firstTouch(),
          captcha_token: String(data.get("cf-turnstile-response") ?? data.get("h-captcha-response") ?? "") || null,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        status.textContent = "Almost done: check your inbox and click the link to confirm.";
        form.reset();
        track("waitlist_submitted", { source: form.dataset.source ?? "web" });
      } else {
        status.className = "error";
        status.textContent = body?.error?.message ?? "Something went wrong. Please try again.";
      }
    } catch {
      status.className = "error";
      status.textContent = "Network problem. Please try again.";
    } finally {
      submit.disabled = false;
    }
  });
}
