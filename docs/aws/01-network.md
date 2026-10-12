# 01 · Network

## Layout
One VPC with a public subnet and a reserved isolated subnet in each of two Availability Zones.

- **Public subnets** hold the host (stage 0) and later the load balancer.
- **Isolated subnets** (no internet route) are created now but empty until RDS or ElastiCache is enabled.
- **No NAT gateway.** A NAT gateway costs about $32/mo plus data, and is the usual surprise bill. The host has its own public IP for outbound traffic (pulling images, calling LLM providers, Google/Apple token endpoints).
- **Gateway endpoints** for S3 and DynamoDB are free and keep that traffic off the internet.

## Security
- Host security group: inbound only the CloudFront origin-facing managed prefix list on port 8000. There is no SSH. Shell access uses **SSM Session Manager**.
- Postgres and Valkey listen on `127.0.0.1` only.
- Public IPv4 addresses cost money (about $3.65/mo each). The design uses exactly one: the host's Elastic IP.

## Why not private-subnet-only today
It would force a NAT gateway or interface endpoints for ECR, logs and SSM (each about $7+/mo per AZ). When the load balancer arrives (stage 2) the host moves behind it and the public IP can go away, since a VPC origin keeps CloudFront-to-ALB traffic private; at that point endpoints or NAT become worth it.

## Security group quota
The CloudFront origin-facing prefix list holds about 55 entries, and each counts as a rule against the default 60-rule security-group quota. The host's security group has exactly one such rule, so it fits. Do not add many more inbound rules to it; request a quota increase first.
