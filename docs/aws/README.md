# AWS backend stack

An all-AWS production backend that costs close to nothing while idle, does not cut corners where users would notice, and scales by changing configuration instead of re-architecting. It is defined with AWS CDK (Python), deployed only through CI, and built from the `tsp.*` Python modules in this repo.

> **Status:** design documented first; commands and numbers are verified against a real deployment in the final phase. Every price below is an estimate for planning. Check it in the [AWS Pricing Calculator](https://calculator.aws/) before you rely on it.

## Architecture (prod, stage 0)

```
users ──► CloudFront  (TLS, WAF, edge cache; your domain)
            ├─ default  ──► S3 bucket: React / React Native web build   (us-east-1)
            └─ /api/*   ──► one EC2 host :8000                          (ap-south-1)
                            security group: only the CloudFront origin-facing prefix list
                            app also requires a secret X-Origin-Verify header

EC2 t4g.small (Graviton, ECS agent, public subnet, no NAT, no SSH)
  ├─ api       FastAPI container (tsp.server + tsp.auth/db/cache/nosql/ai)
  ├─ postgres  127.0.0.1:5432, data on its own EBS volume, WAL + backups pushed to S3
  └─ valkey    127.0.0.1:6379, in-memory cache (Redis-compatible)

DynamoDB (on-demand)       reached through a free VPC gateway endpoint
Cognito user pool          email+password, Google, Apple, passkeys / one-time codes
SES                        verification and reset emails
Secrets Manager            database and social-login credentials
CloudWatch + SNS + Budgets alarms and cost alerts
```

Two regions on purpose. The request path (host, database, cache, DynamoDB, Cognito) runs in **ap-south-1 (Mumbai)** close to your users. The frontend bucket, CloudFront certificate and anything CloudFront requires live in **us-east-1**, which is both cheapest and mandatory for CloudFront certificates. CDK handles the cross-region wiring.

## Decisions and why

| Need | Choice | Why | Cost of changing later |
|---|---|---|---|
| Infra as code | CDK in Python | Same language as the library; reusable constructs in `tsp.infra`; no external state backend | n/a |
| Server | ECS on one EC2 Graviton host | ECS handles health checks, restarts, rolling deploys, secrets injection and IAM per task for free. Plain EC2 + Compose means rebuilding all of that. | Add a load balancer by flipping one flag |
| Load balancer | none for now | An ALB is about $20/mo. A single host does not need it yet. | One flag (`compute.load_balancer`), see the scaling playbook |
| SQL | Postgres container on the host | Cheapest option with no cold starts. Dedicated EBS volume, continuous backup to S3 (point-in-time recovery). | Move to RDS before a second host, with a short documented cutover |
| NoSQL | DynamoDB on-demand | $0 at idle, scales to zero effort, no servers | none |
| Cache | Valkey container on the host | $0 extra | Switch config to ElastiCache Serverless, no app change |
| Vector search (phase 8) | pgvector in the same Postgres | $0 extra, same backups, deletes and tenant filters as the rest of the data | Move large collections to S3 Vectors by config, see [17](17-vector-search.md) |
| Auth | Cognito (Essentials) | Free up to 10,000 monthly active users; handles password storage, social login, passkeys, MFA | Stays as is |
| Edge | CloudFront (+ S3) | TLS, caching, DDoS protection, one front door for static files and the API | Add WAF rules or a VPC origin as you grow |
| Config and secrets | Task environment for non-secrets, Secrets Manager for credentials | Secrets are injected by ECS and never appear in the task definition. Each secret costs about $0.40/mo; there are three at most (database, Google, Apple). | none |
| Environments | prod only (dev later) | Lowest cost now; code already builds one `Stage` per environment | Add `dev` account later, see [multi-env](11-multi-env.md) |

## Estimated monthly cost at idle (prod, stage 0)

| Item | Estimate | Notes |
|---|---|---|
| EC2 t4g.small (24/7) | $12–14 | cheaper with a 1-year Compute Savings Plan once stable |
| EBS: 8 GB root + 40–50 GB data (gp3) | $4–6 | data volume size is a config value |
| Public IPv4 (Elastic IP) | ~$3.7 | AWS charges for every public IPv4 address |
| S3 backups, frontend bucket, ECR images | $1–3 | lifecycle rules keep this small |
| CloudWatch logs and alarms | $1–3 | 14-day log retention by default |
| CloudFront | $0–15 | CloudFront has a free plan; a flat-rate paid plan includes WAF and more. Confirm current plan limits when you deploy. |
| Cognito, DynamoDB on-demand, SES | ~$0 | below free tiers; SES is about $0.10 per 1,000 emails |
| Secrets Manager | $0.40–1.20 | one secret for the database, plus one each for Google and Apple sign-in if enabled |
| CloudWatch custom metrics | ~$1–2 | disk, memory and backup age |
| Route 53 | $0 | your DNS stays at your registrar |
| **Total** | **about $22–45** | the high end includes a paid CloudFront plan |

What gets more expensive as you grow, and when: a second host plus ALB (+$20–40), RDS instead of the container (+$13 and up), ElastiCache Serverless (about +$6 minimum), SMS one-time codes (priced per message; prefer email codes and passkeys).

## Pages

| Page | Contents |
|---|---|
| [00 Account setup](00-account-setup.md) | AWS account hygiene, budgets, `cdk bootstrap`, GitHub OIDC |
| [01 Network](01-network.md) | VPC, no NAT, gateway endpoints |
| [02 Compute](02-compute.md) | ECS-on-EC2 host, data volume, replacement |
| [03 Edge and domain](03-edge-and-domain.md) | CloudFront, certificates, DNS at an external registrar |
| [04 Auth](04-auth.md) | Cognito, Google, Apple, passkeys, email |
| [05 SQL](05-sql.md) | Postgres on host, backups, RDS vs Aurora |
| [06 NoSQL](06-nosql.md) | DynamoDB |
| [07 Cache](07-cache.md) | Valkey, ElastiCache |
| [08 Observability](08-observability.md) | logs, alarms, budgets |
| [09 CI/CD](09-ci-cd.md) | every workflow and how to roll back |
| [10 Scaling playbook](10-scaling-playbook.md) | stages and exact config changes |
| [11 Multi-env](11-multi-env.md) | adding dev |
| [12 Runbooks](12-runbooks.md) | rollback, host replacement, restore, rotation |
| [13 Library modules](13-library-modules.md) | which `tsp.*` package does what |
| [14 Payments setup](14-payments.md) | gateway secrets, seller details, webhook URL |
| [15 Email setup](15-email.md) | SES domain, DNS records, production access |
| [17 Vector search](17-vector-search.md) | pgvector first, S3 Vectors and OpenSearch later; options and cost by size |

## Accepted stage-0 trade-offs

- A deploy or host replacement causes roughly 10–60 seconds of errors, because there is one host and no load balancer. Flip on the load balancer for zero-downtime deploys.
- The database shares a host with the API. Backups and a documented restore path cover host loss; the RPO is about one minute of WAL.
- CloudFront reaches the host over HTTP on a restricted port (prefix-list security group plus secret header). A private VPC origin replaces this when the load balancer is enabled.
