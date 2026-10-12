# 06 · NoSQL (DynamoDB)

DynamoDB in **on-demand** mode costs $0 at idle and scales without capacity planning. It is the right place for high-volume, key-based data: sessions, events, user preferences, rate-limit counters, anything you read by key.

## Table design
Use one table per service with generic keys (`pk`, `sk`) and design access patterns first. `tsp.nosql` provides a small typed helper over this pattern.

- Partition key `pk` and sort key `sk`; model each entity as `pk=USER#<id>`, `sk=PROFILE` or `sk=ORDER#<ts>`.
- A TTL attribute (`expires_at`) lets DynamoDB delete old items for free.
- Add a global secondary index only for a query you actually run.

## Safety
- **Point-in-time recovery** is on (35 days).
- Deletion protection is on and the table is retained if the stack is deleted.
- Access is by IAM role on the ECS task, scoped to this table. No access keys.

## Cost
On-demand is billed per request. If your traffic becomes steady and high, provisioned capacity with autoscaling becomes cheaper (roughly when sustained utilization is high and predictable). Switch by changing `nosql.billing_mode`.

## Local development and tests
Run `amazon/dynamodb-local` and pass `endpoint_url` to `tsp.nosql`.
