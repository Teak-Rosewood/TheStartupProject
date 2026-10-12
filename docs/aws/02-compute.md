# 02 · Compute

## One host, managed by ECS
A single `t4g.small` (Graviton, arm64, 2 vCPU, 2 GB) runs the ECS agent and three tasks: `api`, `postgres`, `valkey`. ECS gives you:

- restart on crash, health checks, and a deployment circuit breaker that rolls back a failed API release automatically;
- secrets injected from Secrets Manager into containers, with an IAM role per task;
- logs shipped to CloudWatch;
- the same task definitions later behind a load balancer, on more hosts, or on Fargate.

Build images for **arm64** (`linux/arm64`) so they run on Graviton; CI builds natively on an arm64 runner.

Size guide: 2 GB is enough for a small API plus a small Postgres and Valkey. If memory pressure alarms fire, move to `t4g.medium` (a config value, a short restart) or start the move to RDS.

## The data volume
Postgres data lives on its own EBS volume mounted at `/data`. It outlives the instance (`RemovalPolicy.RETAIN`).

Because an EBS volume exists in one Availability Zone, the Auto Scaling group is pinned to **one AZ** at stage 0. On instance start, user-data does this in order:
1. associate the Elastic IP with the new instance;
2. attach `/data` (waiting for detach from the old instance if it is still shutting down);
3. mount it, then start the ECS agent.

If the host dies, the ASG launches a replacement that re-attaches the same volume and IP. Expect a few minutes of downtime. See [runbooks](12-runbooks.md).

## Two rules that must never be broken
1. The Postgres service uses `minimumHealthyPercent=0` and `maximumPercent=100`. During a deploy ECS stops the old Postgres before starting the new one. Two Postgres processes on one data directory corrupt it.
2. The API task is allowed to run two copies briefly (`maximumPercent=200`) only when there is a load balancer. Without it, the API also stops-then-starts so the host port is free.

## Updating the host
The ASG uses the ECS-optimized Amazon Linux AMI from the SSM public parameter. A new AMI is picked up when you run a deploy with the instance refresh option; data and IP are preserved by the same flow as a failure.
