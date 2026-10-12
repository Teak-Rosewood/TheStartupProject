# K0 Kit blockers

**Goal:** clear the review's blockers so later phases deploy and their docs are checked. Source: [../../project-review-gaps.md](../review-2026-10.md) B1, B2, B3, B5. B4 (first real deploy) is a separate manual task after this.

**Depends on:** phases 1–7 done. **Branch:** `chore/k0-blockers`. **Size:** small, one pull request.

## Steps

1. **B1 branch triggers.** Decide `main` vs `master` (preferred: rename the default branch to `main` in GitHub settings, done by you, not Claude Code). If keeping `master`, change `on.push.branches` in `.github/workflows/deploy.yml` and `.github/workflows/security.yml` to `master`, and fix `docs/aws/11-multi-env.md`. Grep the repo for `branches: [main]` and `origin/main` and fix every hit consistently.
2. **B2 missing docs.** Write `docs/modules/product-ops.md` (flags, storage, metering: what each does, config keys, admin views, how they fit) and `docs/aws/16-ai-gateway.md` (the gateway routes, API-key scopes, metering names, plan rate limits, kill switch, costs). Base both on the code, not on guesses.
3. **B2 link checker.** Extend `docs/check_links.py` to also scan `python/packages/*/README.md` and `*.py` files for `docs/...md` references and fail when the target is missing. Add a test for the checker if one exists for it.
4. **B3 status pages.** Refresh `README.md` (module list from `python/packages/*/pyproject.toml` descriptions), `docs/startup/gaps.md` (remove items now done: AI mounted, quotas, web app), `CHANGELOG.md` (one entry per phase 1–7). Add `docs/check_modules.py` that fails when a package under `python/packages` is missing from `docs/aws/13-library-modules.md`; wire it into `make docs-check` and CI.
5. **B5 log retention.** In `python/packages/infra/.../config/prod.py` (find the prod config) set `log_retention_days=180`. Note in `docs/aws/` that S3 export with a lifecycle rule is the cheaper option later.

## Done when
- `make docs-check` fails on a deliberately broken `docs/...md` reference in a README and passes after the fix.
- CI is green; deploy and security workflows trigger on the real default branch.
- prod config synthesizes with 180-day retention (`cdk synth` test or the infra unit test).

## After the pull request: first live deploy (B4, manual)

Not a Claude Code task; do it yourself once K0 is merged, before phase 8 reaches production. From the review's B4:
1. Deploy the stack to a throwaway AWS account at stage 0 with the prod config.
2. Run each runbook in `docs/aws/12-runbooks.md` once (restore a backup, rotate a secret, roll back a deploy).
3. Record the real monthly cost after a week in `docs/aws/README.md`, replacing the estimates.
4. Delete the account's stack, or keep it as the dev environment from `docs/aws/11-multi-env.md` if the budget allows.

## Prompt

```
You are working in Teak-Rosewood/TheStartupProject on branch chore/k0-blockers.
Read docs/plans/K0-blockers.md (copied from my plans) and do steps 2–5 in order
(I have handled step 1's branch rename decision: <main|master>; apply step 1 accordingly).
Read the code before writing docs; do not invent features. Keep changes minimal.
Run the repo's checks (make docs-check, the python tests that touch infra and docs)
before committing. One commit per step, then open a pull request describing each fix.
```
