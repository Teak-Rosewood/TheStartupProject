# ADR 0001: Run Postgres and Valkey on the single host at stage 0

- Status: accepted
- Date: 2026-10-12

## Context
The stack must cost close to nothing at idle without causing user-visible cold starts. Aurora Serverless v2 pauses and resumes in about 15 seconds; RDS t4g.micro costs about $13/month before storage.

## Decision
Run Postgres (with pgBackRest continuous backup to S3) and Valkey as containers beside the API on one Graviton EC2 host. Managed RDS/ElastiCache are config switches (`database.mode`, `cache.mode`) used from stage 1.

## Consequences
Cheapest option with no cold starts. We own backups and upgrades, so the monthly restore drill is mandatory, and the database must move off the host before a second host is added (see `docs/aws/10-scaling-playbook.md`).
