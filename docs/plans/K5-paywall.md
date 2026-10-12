# K5 Paywall (`tsp.paywall`)

**Goal:** metered and hard paywalls that hold without blocking many people behind one IP: meter policies, a signed anonymous meter, an account meter, a device signal with shared-network detection, entitlements from `tsp.payments`, verified crawler access with paywall markup, gift links, and pay-per-item credits.

**Depends on:** `tsp.payments`, `tsp.cache`, `tsp.accounts`. **Branch:** `feat/k5-paywall`. **Size:** medium.

## Data model
| Table | Columns |
|---|---|
| `pw_policies` | id, org_id, name, free_items int, window_days int, wall (`hard`/`soft`), applies_to (item types), require_account_after int nullable, active |
| `pw_reads` | id, org_id, user_id nullable, meter_id (anon id from cookie) nullable, item_id, at; index (org_id, user_id, at), (org_id, meter_id, at) |
| `pw_gifts` | token (random, hashed), org_id, item_id, giver_user_id, max_uses, uses, expires_at |
| `pw_credits` | user_id, org_id, balance; `pw_unlocks` (user_id, item_id, at, via (`credit`/`gift`/`subscription`)) |

## Interfaces
```python
decision = await Paywall.check(request, item, user=None) -> Decision
# Decision(allowed: bool, reason: Literal["entitled","free_quota","gift","unlocked","crawler","wall"],
#          remaining: int, wall: "hard"|"soft", set_cookie: str | None)
await Paywall.record_read(request, item, user, decision)
```
Order of evaluation: verified crawler → entitlement (active subscription in `tsp.payments`) → unlocked item → gift token → free quota → wall.

Meter identity:
- Signed-in: count by `user_id`.
- Anonymous: an HMAC-signed first-party cookie `pw_m` holding `{meter_id, issued_at}`; quota counted on `meter_id`. Missing or tampered cookie → a new meter, but see device signal.
- Device signal (anti-reset): a coarse key from a hash of (IP /24 or /48, user-agent family, accept-language) counted in `tsp.cache` with a short TTL. Shared-network detection: if that key sees more than N distinct meter ids or accounts in 24 hours, treat it as a shared network (CGNAT, office, campus) and **ignore** the device signal for it. Never wall on IP alone.
- Option `require_account_after`: after N anonymous reads, require a free account (moves the meter to `user_id`).

Crawlers: verify Googlebot and Bingbot by reverse DNS + forward confirm (cached); verified crawlers see full content; provide `paywall_jsonld(item)` returning `isAccessibleForFree: false` and `hasPart.cssSelector` markup.

Gift links: `create_gift(user, item, max_uses=3, days=7)`; token in URL `?g=`; unlock recorded.

Pay-per-item: credits bought through `tsp.payments` one-time products; `unlock(user, item)` spends one credit.

Privacy: reads are personal data → compliance exporter/eraser; IP is never stored, only the hashed device key in cache with TTL; document the cookie in the cookie notice.

API: `GET /api/paywall/status?item=` (remaining, wall type), `POST /api/paywall/gifts`, `POST /api/paywall/unlock`. Admin: policies, read counts per day, shared-network detections.

## Tests
- Policy 1 per 7 days, hard: second item in the window walled; first item re-read allowed; new window resets.
- Clearing the cookie on one device still walls via device signal; 50 users from one IP with different cookies are **not** walled (shared network detected).
- Tampered cookie rejected.
- Subscriber always allowed; gift link allows up to `max_uses`; fake Googlebot UA without DNS match walled.

## Done when
A hard wall after 1 item per 7 days holds; many users behind one IP are not blocked; `docs/modules/paywall.md` written.

## Prompt
```
Repository: Teak-Rosewood/TheStartupProject. Branch: feat/k5-paywall.
Read docs/plans/K5-paywall.md, docs/plans/README.md, tsp.payments, tsp.cache, tsp.accounts.
Implement the brief. Write the CGNAT test first. Neutral example items (recipes).
Run checks; open a PR.
```
