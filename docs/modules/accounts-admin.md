# `tsp.accounts` and `tsp.admin`

## `tsp.accounts` — users, teams, keys
Cognito proves *who someone is*. `tsp.accounts` keeps what your product needs to know about them.

| Table | Purpose |
|---|---|
| `users` | local profile keyed by Cognito `sub`; created on the first request; only a **verified** email is stored; `disabled_at` locks the account out of every route |
| `orgs`, `memberships` | organizations with roles `member` < `admin` < `owner`; the creator is the owner |
| `invites` | emailed invitations; only a hash of the token is stored, single use, 7-day expiry, and the accepting user's verified email must match the invited address |
| `api_keys` | customer keys `tsp_<prefix>_<secret>`; only a SHA-256 hash of the secret is stored; scopes, expiry, last use, revocation |

Rules enforced in the service (and tested): a non-member cannot even learn an organization exists (404, not 403); only owners create, demote or remove owners; the last owner cannot leave; invites can grant `member` or `admin` only; all mutations write to the audit log.

Routes (mounted under `/api`): `GET/PATCH /me`, `POST/GET /orgs`, `GET /orgs/{id}/members`, `PATCH/DELETE /orgs/{id}/members/{sub}`, `POST/DELETE …/invites`, `POST /invites/accept`, `POST/GET/DELETE …/api-keys`. Dependencies for your own routes: `api.account` (the signed-in local user), `api.org_member("admin")` (path parameter `org_id`), `api.api_key` (machine access with `Authorization: Bearer tsp_…` or `X-API-Key`).

> **Send the ID token to the API.** Cognito access tokens carry no email, so invitation matching and the stored email need the ID token as the bearer token. Everything else works with either.

Account deletion ([compliance](compliance.md)): `accounts.register_with_compliance(compliance)` exports the profile, organizations and keys the user created; erasing removes memberships, anonymizes the profile, deletes a sole-member organization, and **refuses** (a recorded failure) while the user is the only owner of an organization that still has members: transfer ownership first.

## `tsp.admin` — the staff console
Server-rendered (SQLAdmin) at **`/api/admin`**, in the same service, so it adds no infrastructure and reuses the CloudFront `/api/*` behavior and the origin check.

**Sign-in.** A separate Cognito pool (`<prefix>-staff`): no self sign-up, administrators create users, **TOTP MFA required**, 14-character passwords, 8-hour sessions. The console is a confidential OAuth client (authorization code + PKCE + state + nonce); the client secret lives in Secrets Manager and is injected into the container, and the session cookie is signed with a key derived from it. Customers can never become staff, because staff are not in their pool. The session cookie is `SameSite=Lax` (the return from Cognito is a cross-site navigation, which `Strict` would break), `Secure`, `HttpOnly`, and every form carries a CSRF token. Optional: restrict to your office or VPN with `admin.allowed_cidrs`.

**Roles** are the staff pool's groups; the highest one held applies.

| Role | Can view | Can do |
|---|---|---|
| `support` | users, orgs, memberships, invites, consents, data requests, API keys (no secrets), audit log | disable/enable a user, sign a user out, cancel a pending deletion |
| `finance` | users, orgs, memberships | (billing views arrive with payments) |
| `superadmin` | everything | everything, including executing an account deletion |

**Writes are never free-form.** The model views are read-only. Changes go through *Staff actions*: a form requiring the target, an action your role allows, a reason of at least 10 characters (put the ticket number), and a CSRF token. Each run is written to the append-only audit log with your name, role, IP and reason. **Opening a record's detail page is also audited** (`admin.viewed`); if the audit write fails, the page is not shown. There is no impersonation.

### Create the first staff user
After the first deploy:
```bash
POOL=$(aws cloudformation describe-stacks --stack-name tsp-prod-backend --region ap-south-1 \
  --query "Stacks[0].Outputs[?OutputKey=='StaffPoolId'].OutputValue" --output text)
aws cognito-idp admin-create-user --region ap-south-1 --user-pool-id $POOL --username you@example.com \
  --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true \
  --desired-delivery-mediums EMAIL
aws cognito-idp admin-add-user-to-group --region ap-south-1 --user-pool-id $POOL --username you@example.com --group-name superadmin
```
Open the console URL (stack output `StaffConsoleUrl`), sign in with the temporary password, set a new one, and register an authenticator app. Keep at least two `superadmin` users, and remove access the day someone leaves.

### Extending it
Add a `ModelView` in `tsp/admin/views.py` (read-only), grant it to roles in `roles.py`, and add reasoned write operations to `actions.py`. Later modules (payments, flags) register their own views the same way.

### Limits to know
- The audit log is append-only to the application, but a database owner can drop its trigger; give the app a less privileged database role as you grow.
- The console has no session list or force-logout across devices; rotating the staff client secret signs everyone out.
- Bulk CSV export is disabled on purpose (personal data); enable it for `superadmin` only if you must, and audit it.
