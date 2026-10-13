"""Start the browser render worker when pages are waiting for it.

The worker is a Fargate Spot task that exits when idle (``tsp.crawl.render_worker``). Every minute the
``crawl.render_scale`` job counts the jobs waiting on the ``crawl.render`` queue and, when there is work
and fewer than ``max_tasks`` render tasks are running, starts one more with ``ecs:RunTask``.

The API only needs ``ecs:RunTask`` on this task definition, ``ecs:ListTasks`` on the cluster and
``iam:PassRole`` on the task's two roles (the CDK ``Crawl`` construct grants exactly those).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

log = logging.getLogger("tsp.crawl.scaler")
SCALE_JOB = "crawl.render_scale"
RENDER_QUEUE = "crawl.render"
METRIC_NAMESPACE = "TSP/Crawl"


@dataclass(frozen=True)
class RenderTaskSpec:
    cluster: str
    task_definition: str  # family: RunTask then uses the newest active revision
    subnets: list[str]
    security_groups: list[str]
    max_tasks: int = 1
    pages_per_task: int = 20  # one more task for every this many waiting pages, up to ``max_tasks``

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RenderTaskSpec | None:
        """From the ``CRAWL_RENDER_*`` variables the CDK ``Crawl`` construct sets; None if not configured."""
        e = os.environ if env is None else env
        if e.get("CRAWL_RENDER") != "fargate" or not e.get("CRAWL_RENDER_CLUSTER"):
            return None
        return cls(
            cluster=e["CRAWL_RENDER_CLUSTER"],
            task_definition=e["CRAWL_RENDER_TASK_DEFINITION"],
            subnets=e["CRAWL_RENDER_SUBNETS"].split(","),
            security_groups=e["CRAWL_RENDER_SECURITY_GROUPS"].split(","),
            max_tasks=int(e.get("CRAWL_RENDER_MAX_TASKS", "1")),
        )


class RenderScaler:
    """``ecs`` is a boto3 ECS client (blocking calls run in a thread)."""

    def __init__(self, ecs: Any, spec: RenderTaskSpec, cloudwatch: Any = None):
        self._ecs, self.spec, self._cw = ecs, spec, cloudwatch
        self.start_failures = 0  # failed start attempts in the latest ``ensure``

    def _running(self) -> int:
        s = self.spec
        n, token = 0, None
        while True:  # pending tasks count too: desiredStatus RUNNING covers both
            kw: dict[str, Any] = {
                "cluster": s.cluster,
                "family": s.task_definition,
                "desiredStatus": "RUNNING",
            }
            if token:
                kw["nextToken"] = token
            out = self._ecs.list_tasks(**kw)
            n += len(out.get("taskArns", []))
            token = out.get("nextToken")
            if not token:
                return n

    def _start(self, count: int) -> int:
        s = self.spec
        out = self._ecs.run_task(
            cluster=s.cluster,
            taskDefinition=s.task_definition,
            count=count,
            capacityProviderStrategy=[{"capacityProvider": "FARGATE_SPOT", "weight": 1}],
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": s.subnets,
                    "securityGroups": s.security_groups,
                    "assignPublicIp": "ENABLED",  # public subnet, no NAT: needed to reach the web and the API
                }
            },
        )
        for f in out.get("failures", []):
            self.start_failures += 1
            log.warning("render task not started: %s (%s)", f.get("reason"), f.get("detail"))
        return len(out.get("tasks", []))

    async def ensure(self, waiting: int) -> int:
        """Start render tasks for ``waiting`` queued pages. Returns how many were started.

        Nothing is started when the queue is empty or ``max_tasks`` are already running. AWS errors are
        logged and counted as zero started; the next run (a minute later) tries again."""
        self.start_failures = 0
        if waiting <= 0:
            return 0
        s = self.spec
        try:
            running = await asyncio.to_thread(self._running)
            want = min(max(1, math.ceil(waiting / s.pages_per_task)), s.max_tasks) - running
            if want <= 0:
                return 0
            return await asyncio.to_thread(self._start, want)
        except Exception:  # botocore ClientError and friends
            self.start_failures += 1
            log.exception("could not start a render task")
            return 0

    async def publish(self, queue_age_seconds: float, dead_jobs: int) -> None:
        """Send the three numbers the alarms watch to CloudWatch (namespace ``TSP/Crawl``)."""
        if self._cw is None:
            return
        data = [
            {"MetricName": "RenderQueueAgeSeconds", "Value": queue_age_seconds, "Unit": "Seconds"},
            {"MetricName": "RenderJobsDead", "Value": dead_jobs, "Unit": "Count"},
            {"MetricName": "RenderStartFailures", "Value": self.start_failures, "Unit": "Count"},
        ]
        try:
            await asyncio.to_thread(
                self._cw.put_metric_data, Namespace=METRIC_NAMESPACE, MetricData=data
            )
        except Exception:
            log.exception("could not publish render metrics")


async def waiting_render_jobs(session: Any) -> int:
    """Render jobs that are queued and due (not those a worker already holds)."""
    row = await session.execute(
        text(
            "SELECT count(*) FROM jobs WHERE queue = :q AND status = 'queued' AND run_at <= now()"
        ),
        {"q": RENDER_QUEUE},
    )
    return int(row.scalar_one())


async def render_stats(session: Any) -> tuple[float, int]:
    """(age in seconds of the oldest due queued render job, render jobs that died in the last 24 hours)."""
    row = (
        await session.execute(
            text(
                "SELECT COALESCE(EXTRACT(EPOCH FROM now() - "
                "(min(run_at) FILTER (WHERE status = 'queued' AND run_at <= now()))), 0), "
                "count(*) FILTER (WHERE status = 'dead' AND finished_at > now() - interval '24 hours') "
                "FROM jobs WHERE queue = :q"
            ),
            {"q": RENDER_QUEUE},
        )
    ).one()
    return float(row[0]), int(row[1])


def register_render_scaler(registry: Any, scaler: RenderScaler) -> None:
    """``crawl.render_scale`` every minute."""

    @registry.job(SCALE_JOB)
    async def scale(ctx: Any, payload: dict[str, Any]) -> None:
        started = await scaler.ensure(await waiting_render_jobs(ctx.session))
        if started:
            log.info("started %s render task(s)", started)
        await scaler.publish(*await render_stats(ctx.session))

    registry.schedule("crawl-render-scale", "every 1m", SCALE_JOB)
