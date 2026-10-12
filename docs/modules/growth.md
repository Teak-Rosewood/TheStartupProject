# Growth foundations: `tsp.growth` and the landing site

## The landing site (`js/packages/landing`)
Astro, static, no framework JavaScript. Pages: home, pricing (from `plans.toml`), blog (Markdown with enforced title and description lengths), and every legal document rendered from `docs/legal` with its **version printed on the page**, so what visitors read is exactly what the consent records refer to.

```bash
cd js && npm ci
make landing          # renders legal, builds, verifies, runs the verifier's own tests
cd js && npm run dev -w @tsp/landing
```
Configure with environment variables (all `PUBLIC_*` are visible in the browser): `SITE_URL`, `PUBLIC_SITE_NAME`, `PUBLIC_TAGLINE`, `PUBLIC_DESCRIPTION`, `PUBLIC_COMPANY`, `PUBLIC_ADDRESS`, `PUBLIC_SUPPORT_EMAIL`, `PUBLIC_SECURITY_EMAIL`, `PUBLIC_APP_URL`, `PUBLIC_CAPTCHA_SITE_KEY`, `PUBLIC_CAPTCHA_PROVIDER`. Replace the copy in `src/pages/index.astro` using [the positioning one-pager](../startup/07-marketing-playbook.md), and replace `public/og.png` with a real 1200×630 image.

**What the build verifies (and its tests prove it can fail):** every page has one `<h1>`, a title up to 70 characters and a 50–160 character description, a canonical URL on your domain, Open Graph tags and valid JSON-LD; **no inline scripts** (the site's Content-Security-Policy forbids them); **no third-party requests** except your captcha provider; images have alt text; internal links resolve; the footer links terms and privacy; each legal page shows the current document version and has no unfilled `{{placeholder}}`; `robots.txt` points at the sitemap; `security.txt` has a contact and an expiry within a year. CI then runs Lighthouse (performance ≥ 0.9, accessibility ≥ 0.95, SEO ≥ 0.95, best practices ≥ 0.9).

**Hosting.** The deploy workflow builds it with your legal documents and publishes it to the S3 + CloudFront site. CloudFront rewrites `/pricing` to `/pricing/index.html`; the future web app lives under `/app` with a single-page fallback; `/api/*` is never rewritten. Known limit: a missing page is answered by S3 (an XML error), not your 404 page, because a CloudFront custom error response would also mask real 403/404 answers from the API.

**Privacy by default.** Nothing is stored or sent until the visitor allows analytics; Global Privacy Control and Do Not Track count as "no" without asking; the banner offers equal "allow" and "essential only" buttons; the footer reopens it.

## Waitlist with double opt-in
`POST /api/leads` → a confirmation email → the link opens a page with a button (so mail scanners cannot confirm for anyone) → confirmed. Only confirmed addresses become marketing contacts (confirming records marketing consent in `tsp.notify`). Protections: a required consent checkbox whose exact wording is stored with the signup, a honeypot field, rate limits per IP (10/hour) and per address (3/day), Turnstile or hCaptcha when configured (fails closed), the same "check your inbox" answer whether or not the address was already listed, and deletion of signups nobody confirmed within 30 days.

## First-party events
`POST /api/events` takes up to 50 events:
```json
{"events": [{"name": "signup_completed", "anonymous_id": "…", "path": "/pricing", "properties": {"plan": "pro"}}]}
```
- Names are `snake_case` past-tense; properties are flat and small. **Personal data cannot get in:** keys such as email, phone, name, address, token, card are dropped, as are values that look like an email or phone number.
- A browser opt-out signal makes the endpoint store nothing. Rate limited at 120 requests a minute per address.
- After sign-in, `POST /api/events/identify` attaches the visitor's earlier anonymous events to the account.
- Events older than 400 days are deleted nightly ([retention](../legal/data-retention.md)).

## Attribution and referrals
Right after sign-up the app sends `POST /api/me/attribution` with the first-touch UTM parameters, referrer host and landing path (stored once on the user, never overwritten) and an optional referral code. `GET /me/referral` returns the user's code (8 characters, no look-alike letters) and how many people it brought. Self-referrals, unknown codes and repeat credits are ignored. What a referral earns is yours to decide (`on_referral` hook).

## Metrics page
Finance and superadmin see MRR, ARR, ARPU, churn, revenue and refunds (30 days), users, sign-ups, waitlist, sign-ups per day and the top events, in the staff console under *Metrics*. Definitions: [metrics](../startup/03-metrics.md).

## Analytics options
First-party events answer the funnel questions with no extra vendor. For session replay, A/B tests or richer dashboards, add PostHog (cloud free tier, or self-hosted) or Plausible, load it **only after analytics consent**, add it to [sub-processors](../legal/subprocessors.md) and widen the site's Content-Security-Policy (`edge.csp`) to allow its host.

## Security header policy
The site gets a strict Content-Security-Policy, HSTS, `X-Frame-Options: DENY`, a referrer policy and a Permissions-Policy from CloudFront; `/api/*` (including the staff console, which needs inline scripts) does not. Override the policy with `edge.csp` if you add a third-party script.
