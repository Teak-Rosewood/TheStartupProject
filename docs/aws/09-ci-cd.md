# 09 · CI/CD

Everything that changes production goes through GitHub Actions: tests, image builds, infrastructure, database migrations, frontend publishing, backup drills and drift checks. Nobody runs `cdk deploy` from a laptop for prod.

## Authentication to AWS
GitHub's OIDC identity provider is trusted by an IAM role (created by `OidcStack`, see [account setup](00-account-setup.md)). A workflow requests a short-lived token with `permissions: id-token: write` and assumes the role; no AWS keys are stored anywhere. Roles are scoped by repository and by GitHub **environment**:

| Role | Used by | Can do |
|---|---|---|
| diff (read-only) | pull requests | synth and `cdk diff` only |
| deploy | `master` / release tags in environment `prod` | assume the CDK bootstrap roles, push to ECR, run migration task, sync the frontend bucket |
| restore-drill | monthly drill | read backups, create a temporary instance or container |

## Workflows
| File | Trigger | Does |
|---|---|---|
| `python.yml` | PR, push | ruff, format check, mypy, pytest for every package; integration tests against real Postgres, Valkey and DynamoDB Local containers |
| `infra.yml` | PR touching infra | `cdk synth`, cdk-nag security rules, CDK assertion tests, `cdk diff` posted as a PR comment |
| `deploy.yml` | push to `master` (or a version tag, see below) | test gates → build arm64 image → push to ECR (scan on push) → `cdk deploy` → migrate → wait for stable → smoke test → publish frontend |
| `backup-drill.yml` | monthly schedule | restore the latest backup into a throwaway Postgres and run checks |
| `drift.yml` | weekly | `cdk diff`; opens an issue when production differs from code |

### Deploy order (and why)
0. **Registry first:** the `registry` and `oidc` stacks deploy before anything else so the repositories exist before images are pushed (a no-op after the first run).
1. **Gates first:** nothing deploys unless lint, types and tests pass.
2. **Build and push** the API image and, when `docker/postgres/**` changed, the Postgres + pgBackRest image, tagged with the commit SHA (never `latest`).
3. **`cdk deploy`** updates infrastructure and the ECS task definition to the new image.
4. **ECS replaces the API container.** On start it applies Alembic migrations under a Postgres advisory lock (`TSP_MIGRATE_ON_START=1`) *before* serving. If a migration fails the container exits, the deployment circuit breaker rolls back to the previous task definition, and the deploy fails. Migrations must stay compatible with the previous release (expand, then contract in a later release). With several replicas later, the advisory lock makes the others wait and then no-op.
5. **Wait for the service to be stable** (CloudFormation does this as part of `cdk deploy`).
6. **Smoke test** `https://<domain>/api/health` and `/api/ready` through CloudFront.
7. **Publish the frontend**: sync to S3 and invalidate `/index.html`.

### Approvals
Required reviewers on a GitHub environment need a public repository or a paid GitHub plan. If you have neither, deploy prod from **version tags** (`v1.2.3`) pushed by a maintainer, and protect the tag pattern. Both approaches are documented in `deploy.yml` comments.

## Rolling back
- **Bad API release:** re-run the deploy workflow on the previous commit SHA (or `workflow_dispatch` with an `image_tag`). Migrations are not auto-reverted, which is why they must be backward compatible.
- **Bad infrastructure change:** revert the commit; CI redeploys. CloudFormation also rolls back failed updates.
- **Bad data change:** restore from point-in-time backup, see [runbooks](12-runbooks.md).

## GitHub configuration
Repository **variables** (Settings → Secrets and variables → Actions → Variables):

| Name | Value |
|---|---|
| `AWS_ACCOUNT_ID` | the 12-digit account id |
| `AWS_DEPLOY_ROLE_ARN`, `AWS_DIFF_ROLE_ARN`, `AWS_DRILL_ROLE_ARN` | printed by `cdk deploy prod/tsp-prod-oidc` |
| `APP_DOMAIN` | your site, e.g. `app.example.com` (used by the smoke test) |
| `FRONTEND_DIR` | optional: path of the built frontend to publish, e.g. `js/app/dist` |

Repository **secret**: `TSP_ORIGIN_VERIFY` (create with `openssl rand -hex 24`). It is the shared secret CloudFront sends and the API requires. Rotating it is a new secret value plus a deploy.

Create the `prod` **environment** and put the deploy variables there if you want reviewers to approve each production deploy.

## First deployment (done once, by hand)
CI cannot do the first deploy because it needs your decisions and DNS records.
1. Complete [account setup](00-account-setup.md) and edit `python/apps/infra/config/prod.py` (domain, GitHub owner/repo, alarm email).
2. `cdk deploy 'prod/tsp-prod-oidc' 'prod/tsp-prod-registry'`, then set the GitHub variables above.
3. Build and push the first images (`bootstrap` tags are placeholders): follow the build commands in `deploy.yml` locally, or push a commit and let the workflow fail at the backend step after it has pushed the images.
4. `cdk deploy 'prod/tsp-prod-backend'`. Wait here only if you enabled `auth.custom_domain`: add the validation record at your registrar while the stack waits.
5. `cdk deploy 'prod/tsp-prod-edge'` and add its certificate-validation CNAME at your registrar ([edge and domain](03-edge-and-domain.md)). The stack completes when validation passes.
6. Point `app.<domain>` at the CloudFront domain from the stack output.
From then on every push to `master` deploys through `deploy.yml`.

## Repository setup
- Environment `prod` with the variables listed in account setup.
- Branch protection on `master`: require the `python` and `infra` checks, no force-push.
- Dependabot for Python, GitHub Actions and Docker base images.
- ECR lifecycle rule keeps the last 20 images; untagged images expire after 7 days.
