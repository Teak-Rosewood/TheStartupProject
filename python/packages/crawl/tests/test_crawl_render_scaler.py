import pytest
from botocore.exceptions import ClientError
from tsp.crawl import RenderScaler, RenderTaskSpec, register_render_scaler
from tsp.crawl.render_scaler import SCALE_JOB, waiting_render_jobs
from tsp.jobs import JobRegistry, Worker, enqueue

SPEC = RenderTaskSpec(
    "cluster-1", "app-crawl-render", ["subnet-a", "subnet-b"], ["sg-1"], max_tasks=3
)


class FakeEcs:
    def __init__(self, running=0, fail=None):
        self.running = [f"arn:task/{i}" for i in range(running)]
        self.fail, self.run_calls, self.list_calls = fail, [], []

    def list_tasks(self, **kw):
        self.list_calls.append(kw)
        if self.fail == "list":
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "ListTasks"
            )
        # two pages, to prove pagination is followed
        if "nextToken" not in kw:
            return {
                "taskArns": self.running[:1],
                "nextToken": "p2" if len(self.running) > 1 else None,
            }
        return {"taskArns": self.running[1:]}

    def run_task(self, **kw):
        self.run_calls.append(kw)
        if self.fail == "run":
            raise ClientError(
                {"Error": {"Code": "ClusterNotFoundException", "Message": "gone"}}, "RunTask"
            )
        if self.fail == "capacity":
            return {"tasks": [], "failures": [{"reason": "RESOURCE:CPU", "detail": "none"}]}
        return {
            "tasks": [{"taskArn": f"arn:task/new{i}"} for i in range(kw["count"])],
            "failures": [],
        }


async def test_no_waiting_pages_starts_nothing_and_does_not_even_call_aws():
    ecs = FakeEcs()
    assert await RenderScaler(ecs, SPEC).ensure(0) == 0 and not ecs.list_calls and not ecs.run_calls


async def test_starts_one_spot_task_in_the_public_subnets_when_pages_wait():
    ecs = FakeEcs()
    assert await RenderScaler(ecs, SPEC).ensure(5) == 1
    (call,) = ecs.run_calls
    assert (
        call["cluster"] == "cluster-1"
        and call["taskDefinition"] == "app-crawl-render"
        and call["count"] == 1
    )
    assert call["capacityProviderStrategy"] == [{"capacityProvider": "FARGATE_SPOT", "weight": 1}]
    net = call["networkConfiguration"]["awsvpcConfiguration"]
    assert net == {
        "subnets": ["subnet-a", "subnet-b"],
        "securityGroups": ["sg-1"],
        "assignPublicIp": "ENABLED",
    }
    assert ecs.list_calls[0] == {
        "cluster": "cluster-1",
        "family": "app-crawl-render",
        "desiredStatus": "RUNNING",
    }


async def test_skips_when_a_task_already_runs_and_the_backlog_is_small():
    ecs = FakeEcs(running=1)
    assert await RenderScaler(ecs, SPEC).ensure(5) == 0 and not ecs.run_calls


async def test_adds_tasks_for_a_big_backlog_but_never_beyond_max_tasks():
    ecs = FakeEcs(running=2)  # also follows the second list page
    assert len(ecs.running) == 2
    assert await RenderScaler(ecs, SPEC).ensure(1000) == 1  # wants 3 (the cap), has 2
    assert ecs.run_calls[0]["count"] == 1 and len(ecs.list_calls) == 2
    ecs = FakeEcs(running=3)
    assert await RenderScaler(ecs, SPEC).ensure(1000) == 0 and not ecs.run_calls
    ecs = FakeEcs()
    assert await RenderScaler(ecs, SPEC).ensure(41) == 3  # ceil(41 / 20) = 3


@pytest.mark.parametrize("fail", ["list", "run", "capacity"])
async def test_aws_problems_are_logged_not_raised_and_start_nothing(fail, caplog):
    assert await RenderScaler(FakeEcs(fail=fail), SPEC).ensure(5) == 0
    assert "render task" in caplog.text


def test_spec_from_env():
    env = {
        "CRAWL_RENDER": "fargate", "CRAWL_RENDER_CLUSTER": "c", "CRAWL_RENDER_TASK_DEFINITION": "fam",
        "CRAWL_RENDER_SUBNETS": "s1,s2", "CRAWL_RENDER_SECURITY_GROUPS": "g1", "CRAWL_RENDER_MAX_TASKS": "2",
    }  # fmt: skip
    assert RenderTaskSpec.from_env(env) == RenderTaskSpec("c", "fam", ["s1", "s2"], ["g1"], 2)
    assert RenderTaskSpec.from_env({}) is None
    assert RenderTaskSpec.from_env({**env, "CRAWL_RENDER": "none"}) is None


async def test_job_counts_only_due_queued_render_jobs_and_starts_a_task(db):
    ecs = FakeEcs()
    reg = JobRegistry()
    register_render_scaler(reg, RenderScaler(ecs, SPEC))
    assert reg.schedules["crawl-render-scale"][0] == "every 1m" and SCALE_JOB in reg.handlers
    async with db.session() as s:
        await enqueue(s, "crawl.render", {"a": 1}, queue="crawl.render")
        await enqueue(s, "crawl.render", {"a": 2}, queue="crawl.render", unique_key="k2")
        await enqueue(s, "crawl.fetch", {"a": 3})  # other queue: ignored
        await enqueue(s, SCALE_JOB)
        assert await waiting_render_jobs(s) == 2
    await Worker(reg, db).run_once()
    assert len(ecs.run_calls) == 1


class FakeCloudWatch:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def put_metric_data(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("throttled")


async def test_the_job_publishes_queue_age_dead_jobs_and_start_failures(db):
    from sqlalchemy import text

    ecs, cw = FakeEcs(fail="capacity"), FakeCloudWatch()
    reg = JobRegistry()
    register_render_scaler(reg, RenderScaler(ecs, SPEC, cw))
    async with db.session() as s:
        await enqueue(s, "crawl.render", {"a": 1}, queue="crawl.render")
        await s.execute(text("UPDATE jobs SET run_at = now() - interval '300 seconds'"))
        await enqueue(s, "crawl.render", {"a": 2}, queue="crawl.render", unique_key="dead")
        await s.execute(
            text("UPDATE jobs SET status = 'dead', finished_at = now() WHERE unique_key = 'dead'")
        )
        await enqueue(s, SCALE_JOB)
    await Worker(reg, db).run_once()
    (call,) = cw.calls
    assert call["Namespace"] == "TSP/Crawl"
    got = {m["MetricName"]: m["Value"] for m in call["MetricData"]}
    assert 295 <= got["RenderQueueAgeSeconds"] <= 330 and got["RenderJobsDead"] == 1
    assert got["RenderStartFailures"] == 1  # no capacity


async def test_an_empty_queue_publishes_zeros_and_metric_errors_are_not_fatal(db, caplog):
    cw = FakeCloudWatch(fail=True)
    scaler = RenderScaler(FakeEcs(), SPEC, cw)
    async with db.session() as s:
        from tsp.crawl.render_scaler import render_stats

        assert await render_stats(s) == (0.0, 0)
    await scaler.publish(0.0, 0)
    assert "could not publish" in caplog.text and len(cw.calls) == 1
    await RenderScaler(FakeEcs(), SPEC).publish(1, 1)  # no client configured: nothing happens
