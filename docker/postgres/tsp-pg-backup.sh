#!/bin/bash
# Backup sidecar. Creates the pgBackRest stanza, then: a full backup if none exists, otherwise one
# backup per UTC day after 02:00 (full on Sunday, differential other days). Publishes backup age
# to CloudWatch every cycle so a silent failure raises an alarm. Configure through PGBACKREST_* env.
set -uo pipefail

export PGHOST=/var/run/postgresql
until pg_isready -q; do sleep 2; done
until pgbackrest stanza-create; do echo "stanza-create failed, retrying"; sleep 10; done

last_day=""
while true; do
  info=$(pgbackrest info --output=json 2>/dev/null || echo '[]')
  have_backup=$(printf '%s' "$info" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(1 if d and d[0].get("backup") else 0)')
  today=$(date -u +%F)
  type=""
  if [ "$have_backup" = 0 ]; then
    type=full
  elif [ "$last_day" != "$today" ] && [ "$(date -u +%H)" -ge 2 ]; then
    if [ "$(date -u +%u)" = 7 ]; then type=full; else type=diff; fi
  fi
  if [ -n "$type" ]; then
    if pgbackrest backup --type="$type"; then last_day="$today"; else echo "backup ($type) failed"; fi
    info=$(pgbackrest info --output=json 2>/dev/null || echo '[]')
  fi
  printf '%s' "$info" | python3 /usr/local/bin/tsp-pg-metrics.py || true
  sleep 600
done
