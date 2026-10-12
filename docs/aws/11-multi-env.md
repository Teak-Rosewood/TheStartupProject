# 11 · Adding a dev environment later

Production is the only environment now, but the code is structured so a second one is an addition, not a refactor.

## What is already in place
- `EnvConfig` describes one environment (account, region, domain, sizes, modes).
- `AppStage(env_config)` builds the whole stack for one environment. `app.py` instantiates it once for prod.
- Parameter and resource names are prefixed with the environment name (`/tsp/prod/...`).

## Steps when you add `dev`
1. Create a second AWS account (AWS Organizations) and bootstrap it ([account setup](00-account-setup.md)).
2. Copy `config/prod.py` to `config/dev.py` with smaller values: a `t4g.micro` host, a short backup retention, `database.mode="host"`, and a dev domain such as `dev.example.com`.
3. Add `AppStage(dev_config)` to `app.py` and an `OidcStack` in the dev account.
4. Create a GitHub environment `dev` with its own role ARN variables.
5. Change `deploy.yml`: push to `main` deploys to `dev` automatically; version tags (with approval) deploy to `prod`.

Because accounts are separate, a mistake in dev cannot touch prod data, and the prod bill stays clean. Expect a second idle bill of roughly the host, volume and public IP (about $20/month), which is why it is deferred.
