# 12 · Runbooks

## Roll back a bad deploy
1. Open the Actions tab → *Deploy* → *Run workflow* with the previous good `image_tag`.
2. If the cause was a migration, restore data only if the migration destroyed something (see below). Otherwise ship a forward fix.

## Shell access to the host
```bash
aws ssm start-session --target <instance-id> --region ap-south-1
```
There is no SSH and no open port 22.

## Host died or was replaced
The ASG starts a new instance that attaches `/data`, takes over the Elastic IP, and starts the ECS tasks. Expect a few minutes of downtime. Check:
```bash
aws ecs describe-services --cluster <cluster> --services api postgres valkey
```
If `/data` did not attach (for example the old instance is stuck), detach it manually in the EC2 console and let the new instance retry.

## Restore the database (point in time)
1. Stop the API service (set desired count 0) so nothing writes.
2. Stop the Postgres service.
3. Run the restore task with the target time: `tsp-db restore --time "2026-01-31 14:05:00+00"` (wraps `pgBackRest restore --type=time`).
4. Start Postgres, check, then start the API.
Always restore into a *new* data directory first when unsure, and compare.

## Postgres volume full
Alarm at 80%. Increase the volume size in config and deploy (EBS grows online; run `resize2fs`/`xfs_growfs` through the documented SSM command), then find the cause (WAL not archiving fills the disk first).

## Rotate secrets
- Database password: update the `HostSecret` in Secrets Manager, change the role password inside Postgres (`ALTER ROLE ... PASSWORD`), then force a new ECS deployment of the API.
- Origin-verify header: set a new `TSP_ORIGIN_VERIFY` GitHub secret (`openssl rand -hex 24`) and redeploy; CloudFront and the API container pick it up in the same deploy.
- Google/Apple client secrets: update the Secrets Manager secret and redeploy the backend stack.

## Cost spike
Check Cost Explorer grouped by service and usage type: public IPv4, data transfer, CloudWatch logs, and SES/SMS are the usual causes. Cost Anomaly Detection also emails you.
