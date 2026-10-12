#!/bin/bash
# End-to-end proof of the backup path: Postgres + pgBackRest sidecar -> S3 (S3Mock), then a
# point-in-time restore into an empty data directory. Exits non-zero on any mismatch.
#   docker build -t tsp-postgres:test docker/postgres && docker/postgres/test_backup.sh
set -euo pipefail
IMG=${IMG:-tsp-postgres:test}
N=tspbk$$
W=$(mktemp -d)
cleanup() { docker rm -f $N-pg $N-bk $N-minio $N-restore >/dev/null 2>&1 || true
  docker volume rm -f $N-data $N-run >/dev/null 2>&1 || true; docker network rm $N >/dev/null 2>&1 || true; rm -rf "$W"; }
trap cleanup EXIT

docker network create $N >/dev/null
docker run -d --name $N-minio --network $N --hostname $N-minio adobe/s3mock >/dev/null
for i in $(seq 1 60); do docker run --rm --network $N curlimages/curl -sk -o /dev/null -w '%{http_code}' https://$N-minio:9191/ 2>/dev/null | grep -q 200 && break; sleep 1; done

for i in $(seq 1 30); do docker run --rm --network $N curlimages/curl -sk -X PUT -o /dev/null -w '%{http_code}' https://$N-minio:9191/tsp-backups 2>/dev/null | grep -q 200 && break; sleep 1; done

ENVS=(-e PGBACKREST_STANZA=main -e PGBACKREST_PG1_PATH=/var/lib/postgresql/data -e PGBACKREST_PG1_SOCKET_PATH=/var/run/postgresql
  -e PGBACKREST_REPO1_TYPE=s3 -e PGBACKREST_REPO1_PATH=/pgbackrest -e PGBACKREST_REPO1_S3_BUCKET=tsp-backups
  -e PGBACKREST_REPO1_S3_ENDPOINT=$N-minio:9191 -e PGBACKREST_REPO1_S3_REGION=us-east-1 -e PGBACKREST_REPO1_S3_URI_STYLE=path
  -e PGBACKREST_REPO1_S3_KEY=test -e PGBACKREST_REPO1_S3_KEY_SECRET=test -e PGBACKREST_REPO1_STORAGE_VERIFY_TLS=n
  -e PGBACKREST_REPO1_RETENTION_FULL=4 -e PGBACKREST_START_FAST=y)
VOLS=(-v $N-data:/var/lib/postgresql/data -v $N-run:/var/run/postgresql)

docker run -d --name $N-pg --network $N "${ENVS[@]}" "${VOLS[@]}" -e POSTGRES_PASSWORD=pw "$IMG" postgres \
  -c listen_addresses=127.0.0.1 -c unix_socket_directories=/var/run/postgresql \
  -c wal_level=replica -c archive_mode=on -c "archive_command=pgbackrest archive-push %p" >/dev/null
docker run -d --name $N-bk --network $N --user postgres "${ENVS[@]}" "${VOLS[@]}" "$IMG" tsp-pg-backup.sh >/dev/null

psql() { docker exec -u postgres $N-pg psql -qAt -h /var/run/postgresql "$@"; }
for i in $(seq 1 60); do psql -c "select 1" >/dev/null 2>&1 && break; sleep 1; done

echo "== waiting for first full backup"
for i in $(seq 1 90); do
  docker exec -u postgres $N-bk pgbackrest info --output=json 2>/dev/null | grep -q '"backup":\[{' && break; sleep 2
done
docker exec -u postgres $N-bk pgbackrest info | head -12

psql -c "create extension vector" -c "create table t(i int, v halfvec(3))" \
  -c "insert into t select g, ('[' || g || ',0,1]')::halfvec from generate_series(1,100) g"
psql -c "select pg_switch_wal()" >/dev/null
sleep 3
TARGET=$(psql -c "select to_char(now(),'YYYY-MM-DD HH24:MI:SS.US+00')")
sleep 2
psql -c "insert into t select g, ('[' || g || ',0,1]')::halfvec from generate_series(101,150) g" -c "select pg_switch_wal()" >/dev/null
sleep 5
echo "rows before restore: $(psql -c 'select count(*) from t')  target=$TARGET"

echo "== disaster: stop and wipe data"
docker rm -f $N-pg $N-bk >/dev/null
docker volume rm -f $N-data >/dev/null
docker volume create $N-data >/dev/null

echo "== point-in-time restore to $TARGET"
docker run --rm --user root "${VOLS[@]}" "$IMG" chown postgres:postgres /var/lib/postgresql/data
docker run --rm --network $N --user postgres "${ENVS[@]}" "${VOLS[@]}" "$IMG" \
  pgbackrest restore --type=time "--target=$TARGET" --target-action=promote
docker run -d --name $N-restore --network $N "${ENVS[@]}" "${VOLS[@]}" "$IMG" postgres \
  -c listen_addresses=127.0.0.1 -c unix_socket_directories=/var/run/postgresql \
  -c archive_mode=off >/dev/null
for i in $(seq 1 60); do docker exec -u postgres $N-restore psql -qAt -h /var/run/postgresql -c "select 1" >/dev/null 2>&1 && break; sleep 1; done
sleep 3
COUNT=$(docker exec -u postgres $N-restore psql -qAt -h /var/run/postgresql -c "select count(*) from t")
echo "rows after restore: $COUNT"
NEAR=$(docker exec -u postgres $N-restore psql -qAt -h /var/run/postgresql -c "select i from t order by v <=> '[7,0,1]' limit 1")
echo "nearest vector to [7,0,1] after restore: $NEAR"
[ "$NEAR" = 7 ] || { echo "FAIL: vector column did not survive the restore"; exit 1; }
[ "$COUNT" = 100 ] && echo "PASS: restored to the target time (100 rows; later 50 excluded) and the vector column is searchable" || { echo "FAIL: expected 100"; docker logs $N-restore | tail -20; exit 1; }
