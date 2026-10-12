# 07 · Cache (Valkey)

Valkey is the open-source, Redis-compatible store. The Python `redis` client talks to it unchanged.

## Stage 0: container on the host
A Valkey container runs beside the API with `maxmemory 256mb` and `allkeys-lru` eviction, and **no persistence**: this is a cache, so losing it on restart is expected. Do not store anything there you cannot rebuild. Use Postgres or DynamoDB for durable data.

The URL is `redis://127.0.0.1:6379/0` in the `CACHE_URL` environment variable of the API.

## Later: ElastiCache Serverless
Set `cache.mode: elasticache`. The stack creates a serverless Valkey cache in the isolated subnets and rewrites `CACHE_URL` to a `rediss://` (TLS) address. It has a small minimum monthly cost plus usage. The app needs no code change, because `tsp.cache` picks TLS from the URL scheme.

## What `tsp.cache` gives you
JSON get and set with TTL, a `@cached` decorator, a sliding-window rate limiter, and a distributed lock. With `cache.mode: none`, all of it becomes a safe no-op so the app still works.
