"""Read `pgbackrest info --output=json` on stdin; publish hours since the last backup to CloudWatch.

With no backup yet, publishes nothing (the alarm treats missing data as breaching).
Set TSP_METRIC_NAMESPACE / TSP_METRIC_DIMENSION to enable publishing; otherwise prints the value.
"""

import json
import os
import sys
import time

info = json.load(sys.stdin)
backups = info[0].get("backup", []) if info else []
if not backups:
    sys.exit(0)

hours = (time.time() - max(b["timestamp"]["stop"] for b in backups)) / 3600
namespace = os.environ.get("TSP_METRIC_NAMESPACE")
if not namespace:
    print(f"hours_since_last_backup={hours:.2f}")
    sys.exit(0)

import boto3  # noqa: E402

boto3.client("cloudwatch").put_metric_data(
    Namespace=namespace,
    MetricData=[
        {
            "MetricName": "HoursSinceLastBackup",
            "Dimensions": [{"Name": "Environment", "Value": os.environ.get("TSP_METRIC_DIMENSION", "prod")}],
            "Value": hours,
            "Unit": "None",
        }
    ],
)
