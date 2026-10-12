# Paywall (`tsp.paywall`)

Metered ("one recipe a week free") or hard paywalls that hold without blocking many readers who share one network. Package: `python/packages/paywall`.

## How a request is decided
In this order: **verified search crawler**, **subscriber**, **item already unlocked**, **valid gift link**, **free quota**, **wall**. `Decision` carries `allowed`, `reason`, `remaining` (-1 when the item is not metered), `wall` (`hard` or `soft`), `require_account` and the cookie to set.

- A `Policy` says how many different items a reader may open per window (`free_items` per `window_days`), for which item types, and whether a free account is required after N anonymous reads. Reopening an item already counted in the window is free.
- `wall="soft"` still returns `allowed=False`; your page decides whether to show a dismissible prompt or a truncated preview. The server never enforces a soft wall by itself.
- **Anonymous meter:** a first-party, HMAC-signed cookie `pw_m` (HttpOnly, SameSite=Lax, about 13 months). A missing, forged or expired cookie is replaced with a new meter.
- **Cookie cleared on purpose:** a coarse device key (your /24 network or IPv6 /48, browser family and first language, keyed hash) is kept in the cache for 24 hours with the meters seen under it. A visitor with no valid cookie inherits the reads of earlier meters under the same key, and keeps that link in later requests.
- **Shared networks are never walled for their neighbours:** once more than `shared_after` (default 3) different meters appear under one key in a day, the key is ignored and every reader counts on their own meter.
- Signed-in readers count by account plus their anonymous meter, so signing up does not reset the count.

### Know the trade-off
A new person arriving without a cookie on a network the paywall has seen fewer than `shared_after` times looks the same as someone who cleared cookies. So the first few distinct visitors from a small office can be treated as one reader. A higher `shared_after` protects more shared networks and lets more cookie-clearers through. Fully anonymous metering cannot be both exact and fair; for strict control, set `require_account_after` and meter accounts instead.

## Crawlers
Googlebot and Bingbot are accepted only after reverse DNS ends in the engine's documented hostnames and the forward lookup returns the same address (`verify_crawler`). A crawler user agent alone proves nothing. Use `paywall_jsonld(headline, selector)` to emit `isAccessibleForFree: false` markup, so search engines treat the gate as a paywall and not cloaking. DNS is injectable (`Resolver`) and checked on every request; add a cache in front if crawlers are frequent.

## Gifts and pay-per-item
- `create_gift(session, user_id, item, max_uses, days)` returns a token for a link such as `…/recipes/x?g=TOKEN`. Only its hash is stored; each reader uses one slot once; at most 10 links per giver per day.
- `add_credits` (call it from your payments receipt hook when someone buys a credit pack) and `unlock` spend one credit to open an item for good. These credits belong to the reader in one publisher's catalogue and are separate from the organization credits in `tsp.payments`.

## Privacy
Reads are personal data for signed-in users: they are exported and erased with the account (`register_with_compliance`). Anonymous meters are not linked to a person. IP addresses are never stored; the cache holds only a keyed hash with a one-day lifetime. Add the `pw_m` cookie to your cookie notice as strictly necessary for the paywall, and mention the device key in the privacy policy.

## HTTP routes (`/api/paywall`)
| Route | Who | What |
|---|---|---|
| `GET /status?org_id&item&type` | anyone | the decision without counting a read |
| `POST /read` | anyone | the decision, and counts the read; set the `pw_m` cookie. Responses are `Cache-Control: private, no-store` |
| `POST /gifts` | signed in | creates a gift link token |
| `POST /unlock` | signed in | spends one credit; 402 when none are left |

Pass `?g=TOKEN` to `status` or `read` to redeem a gift. Do not cache pages that include paywalled content at the edge without varying on the cookie.

## In the sample API
Set `PAYWALL_SECRET` (32 or more random characters; keep it in Secrets Manager) to turn the routes on. Without it the module is off. The subscriber check (`Paywall(entitled=...)`) is not wired in the sample, because who counts as a subscriber depends on your catalogue; give it a function `(session, user_id, org_id) -> bool` built on `tsp.payments`. Policies are rows in `pw_policies` (`Paywall.set_policy`); there is no admin screen for them yet.

## Not built
Admin views (policies, read counts per day, shared-network detections), a bypass for people behind privacy relays, and edge enforcement.
