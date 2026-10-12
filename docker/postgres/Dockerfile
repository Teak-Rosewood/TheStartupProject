# PostgreSQL + pgBackRest (continuous WAL archiving and scheduled backups to S3).
# Used for both the database container and its backup sidecar. Built and pushed by CI.
FROM postgres:16-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends pgbackrest python3-boto3 \
 && rm -rf /var/lib/apt/lists/*
COPY tsp-pg-backup.sh tsp-pg-metrics.py /usr/local/bin/
RUN chmod +x /usr/local/bin/tsp-pg-backup.sh
