# 05 · SQL (PostgreSQL)

## Stage 0: Postgres on the host
A Postgres container runs on the same EC2 host, bound to `127.0.0.1`. Data is on the dedicated EBS volume `/data`.

### Backups
- **WAL archiving and base backups to S3** with pgBackRest. This gives point-in-time recovery (restore to any second since the oldest backup) with about a one-minute data-loss window.
- Daily full or differential backup, retention set in config (default: 7 days of recovery plus 4 weekly fulls). The backup bucket is versioned, encrypted, and retained if the stack is deleted.
- Backups run in a sidecar container of the same image (`docker/postgres`): it creates the pgBackRest stanza, takes a full backup on Sundays and a differential the other days, and publishes the age of the newest backup to CloudWatch. `docker/postgres/test_backup.sh` proves backup and point-in-time restore end to end against an S3 mock.
- A second safety net: **EBS snapshots** of the data volume daily (Data Lifecycle Manager).
- A monthly CI job restores the latest backup into a throwaway database and runs a sanity query. A backup that was never restored is not a backup.

### Tuning
The container starts with conservative memory settings for 2 GB (`shared_buffers` about 256 MB, `max_connections` 50). Use a connection pool in the app (`tsp.db` does) rather than raising connections.

### Migrations
Alembic migrations run in CI after the image is built, as a one-off ECS task using the same image (`tsp-db migrate`). A failing migration fails the deploy before traffic switches. Migrations must be backwards compatible with the previous release (expand, deploy, then contract in a later release), because the old container can still be serving while the migration runs.

### Major version upgrades
Plan a window: take a backup, dump with `pg_dumpall` or use `pg_upgrade` against a copy of the volume, and test the restore first. A managed database (below) makes this easier, which is one reason to move when you outgrow stage 0.

## When to move off the host
Move to RDS when any of these happens: you want a second host or load balancer, the database competes with the API for memory, you need stricter durability (Multi-AZ), or the operational load of upgrades bothers you. Signals: database CPU or memory alarms, swap usage, slow queries while the API is quiet.

## RDS PostgreSQL vs Aurora PostgreSQL
Both are managed Postgres. RDS is a single instance (optionally with a standby). Aurora is a purpose-built storage layer shared by one writer and up to 15 readers.

| | RDS PostgreSQL | Aurora PostgreSQL (provisioned) | Aurora Serverless v2 |
|---|---|---|---|
| Cheapest entry | `db.t4g.micro` about $13–16/mo plus storage; reservations bring it down further | roughly 2x RDS at similar size, plus I/O charges unless I/O-Optimized | scales down to 0 ACU on current versions (about $0 compute idle) or to a floor of 0.5 ACU (about $45/mo) |
| Cold start | none | none | scale-to-zero resumes in about 15 s on first query |
| Storage | you pick size (gp3), grows by modify | automatic, up to 128 TiB, pay per GB used | same |
| Read scaling | read replicas (up to 5, async) | up to 15 readers on shared storage, replica lag usually milliseconds | same |
| Failover | Multi-AZ standby, typically 60–120 s | typically under 30 s | same |
| Backups | automated snapshots, PITR to 35 days | continuous, PITR, fast clone | same |
| Steady load cost | cheapest | higher | highest per unit of steady compute |
| Best for | one app, predictable load | high availability, read-heavy, large data | spiky or unpredictable load, many small environments |

Rule of thumb: stay on a container until you need a managed service, go to **RDS** first, and only move to Aurora when you need fast failover, many read replicas, or storage beyond what is comfortable on RDS. Aurora Serverless v2 pays off for dev environments or bursty workloads; at steady load it costs more than RDS.

### Runbook: host container → RDS (short cutover)
1. Set `database.mode: rds` and `database.keep_host_container: true`, then deploy. The stack creates the instance in the isolated subnets with its own Secrets Manager credential, and keeps the host container running beside it. Note the API switches to the RDS credentials in this deploy, so for zero data loss start logical replication (step 2) and cut over (step 3) in one window; for a small database simply use the dump path.
2. Start **logical replication** from the container to RDS (publication on the container, subscription on RDS), or for small databases take a dump during a short maintenance window.
3. Wait until lag is near zero, put the API in read-only/maintenance mode, wait for lag to hit zero, then remove `keep_host_container` and redeploy: the API now reads `DB_HOST`, `DB_USER` and `DB_PASSWORD` from the RDS secret.
4. Verify, keep the container's volume for a week, then remove it.

### Runbook: RDS → Aurora
Create an Aurora read replica of the RDS instance, wait for sync, promote it, then set `database.mode: aurora` so the API reads the new cluster's secret. Downtime is the promotion time (usually under a minute).
