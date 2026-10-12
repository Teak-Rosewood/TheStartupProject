# 10 · Scaling playbook

Each stage is a config change in `python/apps/infra/config/prod.py` plus a deploy. The code path for every stage is built and tested (synth) from the start, so moving up is never a rewrite.

| Stage | Move when | Config | What changes |
|---|---|---|---|
| 0 | — | defaults | one host runs api + postgres + valkey |
| 1 | DB CPU or memory alarms, swap use, or before a second host | `database.mode="rds"`, `cache.mode="elasticache"` | managed database and cache in isolated subnets; the API receives the new `DB_*` secrets and `CACHE_URL` automatically; application code is unchanged |
| 2 | host CPU above 60% sustained, or you need zero-downtime deploys | `compute.load_balancer=True` | internal ALB, CloudFront VPC origin (origin no longer public), multi-AZ auto scaling group with 2 or more hosts, target-tracking autoscaling. Requires stage 1 first, because Postgres cannot stay on a host that may be replaced or duplicated |
| 3 | revenue or a team | add `DevStage` | separate dev AWS account, see [multi-env](11-multi-env.md) |
| 4 | high availability or read-heavy load | `database.mode="aurora"`, Multi-AZ, optional Fargate for services | Aurora cluster; see the RDS vs Aurora table |

## Signals to watch
- Host: CPU, memory (CloudWatch agent), disk, network credits for burstable `t4g` (credit balance hitting 0 means you have outgrown it).
- Database: connections, CPU, free memory, replication lag, storage.
- Edge: CloudFront 4xx/5xx rates, origin latency.
- Money: Budgets and Cost Explorer grouped by service.

## Cheaper while you stay small
- 1-year Compute Savings Plan on the host once usage is stable.
- Smaller log retention, ECR lifecycle rules.
- Keep one public IPv4. Avoid NAT gateways and interface endpoints until you need them.
