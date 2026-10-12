# `tsp.audit` and `tsp.compliance`

The code side of the [legal templates](../legal/README.md): what the product must do, not just say.

## `tsp.audit` — append-only audit log
- `audit_log` table written with `record(session, "user.disabled", actor=Actor(sub, role), target=("user", id), request=request, reason="…")`. The row joins the caller's transaction, so it commits or rolls back with the change.
- A Postgres trigger rejects `UPDATE`, `DELETE` and `TRUNCATE`. (A database owner can still drop the trigger; give the application a separate, less privileged role as you grow.)
- Records the viewer IP (first `X-Forwarded-For` entry, valid because the origin is reachable only through CloudFront) and the request id that `tsp.server` puts on `request.state`.

## `tsp.compliance`
| Piece | What it does |
|---|---|
| `tsp-compliance render-legal` | fills `docs/legal/*.md` from `company.toml`, drops the template banner, rewrites cross-links to `/legal/<id>`, and writes `documents.json` (id, title, version, sha256, URL, whether consent is required). Fails on an undefined placeholder; `--strict` also fails while `(verify)` or optional markers remain. |
| Versions | default to the first 12 hex digits of the rendered text's checksum, so any edit asks users to accept again. Pin one in the config's `[versions]` table to keep it through editorial fixes. |
| Consent | `consents` rows (user, document, version, time, IP, user agent). `require_consent("terms","privacy")` returns `403 consent_required` with the stale documents; `POST /me/consents` records acceptance and rejects out-of-date versions with 409. |
| Export | `POST /me/export` runs every registered exporter, writes one JSON file to the private exports bucket (7-day expiry) and returns a presigned link. |
| Deletion | `POST /me/delete` starts a 30-day grace period: sessions end, the account is blocked (`account_pending_deletion`), `POST /me/delete/cancel` undoes it. After the grace period `run_due_deletions` runs every registered eraser, then deletes the Cognito user. Consent and request records are kept but detached from the person. |
| Public config | `GET /legal/documents`, `GET /legal/config` (documents plus cookie categories) for the website and consent banner. |

Other modules plug in without being imported: `compliance.register_exporter("notes", fn)` and `register_eraser`. Records the law requires you to keep (tax invoices) must be anonymized by their eraser, not deleted.

## Wiring (see `python/apps/api`)
```python
compliance = Compliance(manifest, store=S3ExportStore(bucket), identity=CognitoIdentity(pool_id))
consent, active = make_guards(compliance, current_user, db_session)
app.include_router(build_router(compliance, current_user, db_session), prefix="/api")
app.include_router(gated, prefix="/api", dependencies=[Depends(active), Depends(consent("terms", "privacy"))])
```
Apply migrations of both modules with `tsp-db migrate --module tsp.audit --module tsp.compliance` (the API container does it on start through `TSP_MIGRATE_MODULES`). Each module keeps its migrations on its own Alembic branch.

## Operations
- `run_due_deletions` has no scheduler yet: until `tsp.jobs` lands, run it from the admin console or a one-off task. Deadlines come from the privacy policy (30 days).
- Raise `log_retention_days` to 180 for production ([observability](../aws/08-observability.md)).
- Audit rows hold the pseudonymous user id, not email or name. The [retention schedule](../legal/data-retention.md) says how long they stay.
