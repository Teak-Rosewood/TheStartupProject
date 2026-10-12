# 08 · Observability and cost control

## Logs
Containers log to CloudWatch Logs with **14-day retention** by default (log storage is a surprise cost; raise it per log group only if needed). `tsp.server` writes one JSON object per line with a request ID, so Logs Insights queries work.

## Alarms (SNS email)
| Alarm | Why |
|---|---|
| EC2 status check failed | host is unhealthy; the ASG replaces it, you get told |
| CPU above 80% for 15 min | time to scale |
| `/data` disk above 80% | Postgres is about to run out of space (published by the CloudWatch agent) |
| Backup older than 26 hours, or no backup metric | backups silently stopped (the sidecar publishes `HoursSinceLastBackup`; missing data counts as a breach) |
| CloudFront 5xx rate above 5% | users are seeing errors (this alarm and its topic are in us-east-1) |
| Host memory above 90% | outgrown the instance |

## Budgets
An AWS Budget for the monthly limit you choose, alerting at 50, 80 and 100% (actual) and 100% (forecast), plus Cost Anomaly Detection. The cost drivers to watch are public IPv4, NAT (not used), CloudWatch logs, data transfer out, and SES/SMS volume.

## Tracing and dashboards
Not included at stage 0. Add X-Ray or OpenTelemetry later when a request crosses more than the API and its data stores.
