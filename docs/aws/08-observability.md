# 08 · Observability and cost control

## Logs
Containers log to CloudWatch Logs with **14-day retention** by default (log storage is a surprise cost; raise it per log group only if needed). `tsp.server` writes one JSON object per line with a request ID, so Logs Insights queries work.

**Retention for Indian production use.** Indian CERT-In directions expect security-relevant ICT logs to be kept for a rolling 180 days, inside India **(verify applicability to your business)**. 14 days is the cost-saving default for development; before production, raise the retention of the API, database and host log groups (or export them to an S3 bucket in `ap-south-1`, which is cheaper than long CloudWatch retention). The retention is a config value (`log_retention_days`); `config/prod.py` sets 180. Exporting logs to S3 with a lifecycle rule is the cheaper option once log volume grows. see the [retention schedule](../legal/data-retention.md).

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
