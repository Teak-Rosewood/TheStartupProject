"""K6-B: submissions, preconditions, moderation workflow (recorded model replies), appeals."""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from engage_support import ORG
from sqlalchemy import select, text
from tsp.ai.models import ChatCompletionResponse, Choice, ChoiceMessage, Usage
from tsp.audit import Actor
from tsp.cms import Complaint, ComplaintsConfig, ComplaintsDesk, Level, Principal
from tsp.engage import (
    APPEAL_CATEGORY,
    Draft,
    Engage,
    EngageConfig,
    FactCheckHook,
    Length,
    Points,
    Submission,
    SubmissionError,
    Submissions,
    Verdict,
    build_submissions_router,
    build_workflow,
    default_moderators,
    push_prompts,
    register_with_compliance,
)
from tsp.jobs import JobRegistry, Worker
from tsp.pipeline import Price, Runner
from tsp.server import Settings, create_app, db_session

STAFF = Actor("bo", "reviewer")
CFG = EngageConfig(timezone="UTC", require_subscription=True)
DESK_CFG = ComplaintsConfig(categories=("accuracy", APPEAL_CATEGORY), levels=(Level("desk", 10),))
GOOD = (
    "I made it twice: with butter it browned in 3 minutes, with oil it stayed pale, so butter wins."
)


class FakeLLM:
    """Recorded replies per rubric dimension, chosen by what the prompt asks about and by marker
    words in the comment (a stand-in for a model: no network)."""

    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(create=self.create)

    async def create(self, model, messages, **params):
        text_ = " ".join(str(m["content"]) for m in messages)
        self.calls.append(text_)
        dim = (
            "civility"
            if "CIVILITY" in text_
            else "relevance"
            if "RELEVANCE" in text_
            else "substance"
        )
        comment = text_.split("Comment:")[-1]
        score, reason = 0.95, "Fine."
        if dim == "civility" and "idiot" in comment:
            score, reason = 0.05, "Please keep it civil: no insults."
        if dim == "civility" and "borderline" in comment:
            score, reason = 0.5, "Sharp tone."
        if dim == "relevance" and "football" in comment:
            score, reason = 0.1, "This is not about the recipe."
        if dim == "substance":
            score, reason = (0.85, "Specific.") if "twice" in comment else (0.3, "Bare opinion.")
        return ChatCompletionResponse(
            model=model, routed_via=f"fake/{model}", usage=Usage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
            choices=[Choice(message=ChoiceMessage(content=json.dumps({"score": score, "reason": reason})))],
        )  # fmt: skip


class Env:
    def __init__(self, db, mods=None, subscribed=True):
        self.db, self.llm = db, FakeLLM()
        self.wf = build_workflow(mods or default_moderators(["darn"]), CFG)
        self.runner = Runner(
            db,
            [self.wf],
            llm=self.llm,
            prices={"claude-haiku-5-5": Price(0.0, 0.0)},
            retry_delay=lambda a: timedelta(0),
        )
        reg = JobRegistry()
        self.runner.register(reg)
        self.worker = Worker(reg, db, poll_interval=0.01)
        self.subscribed = subscribed

    async def entitled(self, s, user, org):
        return self.subscribed

    def subs(self, s, clock=None, desk=True):
        kw = {"clock": clock} if clock else {}
        return Submissions(
            s, CFG, self.runner, self.wf, entitled=self.entitled,
            desk=ComplaintsDesk(s, config=DESK_CFG, **kw) if desk else None, **kw,
        )  # fmt: skip

    async def drain(self):
        for _ in range(60):
            if not await self.worker.run_once():
                return
        raise AssertionError("jobs never settled")


@pytest.fixture
async def env(db):
    e = Env(db)
    async with db.session() as s:
        await push_prompts(s)
    return e


async def make_eligible(db, clock, user="u1", item="pancakes"):
    """Onboarded, and has really read the item."""
    async with db.session() as s:
        e = Engage(s, CFG, clock=clock)
        for step in CFG.required_onboarding:
            await e.complete_onboarding(user, ORG, step)
        await e.receipts.start(user, ORG, item, "v1", expected_seconds=60)
        clock.at += timedelta(seconds=70)
        await e.receipts.ping(user, item, 70, 1.0, True)


async def send(env, clock, side="for", body=GOOD, user="u1", item="pancakes"):
    async with env.db.session() as s:
        sub = await env.subs(s, clock).submit(user, ORG, item, side, body, author_name="Asha")
        sid = sub.id
    await env.drain()
    async with env.db.session() as s:
        return await s.get(Submission, sid)


# -- preconditions ---------------------------------------------------------------------------------


async def test_every_missing_precondition_is_reported_by_name(env, db, clock):
    async with db.session() as s:
        subs = env.subs(s, clock)
        assert (await subs.can_submit(None, ORG, "pancakes")).reasons == ["not_signed_in"]
        e = await subs.can_submit("u1", ORG, "pancakes")
        assert (
            not e.ok
            and e.reasons == ["onboarding_incomplete", "not_read"]
            and e.onboarding_remaining == ["welcome", "interests"]
        )
        env.subscribed = False
        assert (await subs.can_submit("u1", ORG, "pancakes")).reasons == [
            "not_subscribed",
            "onboarding_incomplete",
            "not_read",
        ]
        with pytest.raises(SubmissionError, match="not allowed yet: not_subscribed"):
            await subs.submit("u1", ORG, "pancakes", "for", GOOD)
    await make_eligible(db, clock)
    env.subscribed = True
    async with db.session() as s:
        assert (await env.subs(s, clock).can_submit("u1", ORG, "pancakes")).ok
        assert (await env.subs(s, clock).can_submit("u1", ORG, "other-item")).reasons == [
            "not_read"
        ]  # reading is per item


async def test_without_an_entitlement_check_nobody_can_submit_unless_the_config_allows(db, clock):
    wf = build_workflow([Length()], CFG)
    runner = Runner(db, [wf], llm=FakeLLM())
    await make_eligible(db, clock)
    async with db.session() as s:
        closed = Submissions(s, CFG, runner, wf)
        assert (await closed.can_submit("u1", ORG, "pancakes")).reasons == ["not_subscribed"]
        open_ = Submissions(s, EngageConfig(timezone="UTC", require_subscription=False), runner, wf)
        assert (await open_.can_submit("u1", ORG, "pancakes")).ok


# -- moderation ---------------------------------------------------------------------------------------


async def test_an_evidenced_on_topic_take_is_accepted_and_paid(env, db, clock):
    await make_eligible(db, clock)
    sub = await send(env, clock)
    assert sub.status == "accepted" and sub.reasons == [] and sub.decided_at
    assert sub.points_awarded == 15 + 10  # base plus the evidence bonus (substance 0.85)
    assert sub.scores == {"civility": 0.95, "relevance": 0.95, "substance": 0.85}
    async with db.session() as s:
        assert await Points(s, CFG).balance("u1", ORG) >= 25
    assert len(env.llm.calls) == 3


async def test_an_uncivil_take_is_rejected_with_a_reason_the_author_can_read(env, db, clock):
    await make_eligible(db, clock)
    sub = await send(
        env, clock, body="Only an idiot would bake this, I made it twice and it was awful."
    )
    assert (
        sub.status == "rejected"
        and sub.reasons == ["Please keep it civil: no insults."]
        and sub.points_awarded == 0
    )
    assert (
        len(env.llm.calls) == 1
    )  # civility failed first: relevance and substance were never paid for
    async with db.session() as s:
        mine = await env.subs(s, clock).mine("u1")
    assert mine[0]["status"] == "rejected" and mine[0]["reasons"] == [
        "Please keep it civil: no insults."
    ]


async def test_cheap_checks_run_first_and_save_the_model_calls(env, db, clock):
    await make_eligible(db, clock)
    short = await send(env, clock, side="against", body="nope, bad")
    assert short.status == "rejected" and "at least 20 characters" in short.reasons[0]
    blocked = await send(
        env, clock, side="neutral", body="Darn, this was twice as salty as I expected it to be."
    )
    assert (
        blocked.status == "rejected"
        and "language we do not allow" in blocked.reasons[0]
        and "darn" not in blocked.reasons[0].lower()
    )
    assert env.llm.calls == []


async def test_an_off_topic_take_is_rejected_and_a_borderline_one_goes_to_a_person(env, db, clock):
    await make_eligible(db, clock)
    off = await send(
        env, clock, side="for", body="Anyway did you see the football last night, twice as good."
    )
    assert off.status == "rejected" and off.reasons == ["This is not about the recipe."]
    border = await send(
        env,
        clock,
        side="against",
        body="Honestly borderline harsh but I cooked it twice and it was dry.",
    )
    assert (border.status, border.needs_review, border.reasons) == (
        "pending",
        True,
        ["Sharp tone."],
    )
    async with db.session() as s:
        subs = env.subs(s, clock)
        assert [q.id for q in await subs.review_queue(ORG)] == [border.id]
        with pytest.raises(SubmissionError, match="say why"):
            await subs.review(ORG, border.id, STAFF, accept=False)
        done = await subs.review(ORG, border.id, STAFF, accept=True)
        assert (done.status, done.needs_review, done.points_awarded) == ("accepted", False, 25)
        with pytest.raises(SubmissionError, match="not waiting"):
            await subs.review(ORG, border.id, STAFF, accept=True)


async def test_a_fact_check_hook_can_fail_or_flag(db, clock):
    async def check(d: Draft):
        if "miracle" in d.body:
            return Verdict("fail", "The claim that it cures illness is not supported.")
        if "studies" in d.body:
            return Verdict("flag", "A source is cited; a person should check it.")
        return None

    env = Env(db, [Length(), FactCheckHook(check)])
    await make_eligible(db, clock)
    assert (
        await send(
            env, clock, side="a", body="This miracle recipe cures every illness, I tried it twice."
        )
    ).status == "rejected"
    flagged = await send(
        env, clock, side="b", body="Butter browns faster, studies show, and I saw it twice."
    )
    assert (flagged.status, flagged.needs_review) == ("pending", True)
    assert (
        await send(env, clock, side="c", body="I tried it twice and it came out fluffy both times.")
    ).status == "accepted"


def test_duplicate_moderator_names_are_refused():
    with pytest.raises(ValueError, match="two moderators"):
        build_workflow([Length(), Length()])


# -- editing and the one-take rule -----------------------------------------------------------------------


async def test_a_rejected_take_can_be_edited_and_moderated_again_but_an_accepted_one_is_locked(
    env, db, clock
):
    await make_eligible(db, clock)
    first = await send(env, clock, body="idiot idiot, but I did cook it twice for the record.")
    assert first.status == "rejected"
    again = await send(env, clock, body=GOOD)
    assert (
        again.id == first.id and again.status == "accepted" and again.reasons == []
    )  # same take, new text
    async with db.session() as s:
        with pytest.raises(SubmissionError, match="can no longer be edited"):
            await env.subs(s, clock).submit("u1", ORG, "pancakes", "for", GOOD + " More.")
        other_side = await env.subs(s, clock).submit(
            "u1", ORG, "pancakes", "against", GOOD
        )  # a second side is a second take
        assert other_side.id != first.id
    async with db.session() as s:
        assert (await s.execute(select(text("count(*)")).select_from(Submission))).scalar_one() == 2


async def test_an_edit_during_moderation_wins_over_the_older_run(env, db, clock):
    await make_eligible(db, clock)
    async with db.session() as s:
        first = await env.subs(s, clock).submit("u1", ORG, "pancakes", "for", "idiot " + GOOD)
        sid = first.id
    async with db.session() as s:  # edited before the first run was processed
        await env.subs(s, clock).submit("u1", ORG, "pancakes", "for", GOOD + " Still true.")
    await env.drain()
    async with db.session() as s:
        sub = await s.get(Submission, sid)
    assert sub.status == "accepted" and sub.body.endswith(
        "Still true."
    )  # the stale run neither rejected nor paid it twice
    assert sub.points_awarded == 25


async def test_there_is_no_way_to_reply(env, db, clock):
    cols = {c.name for c in Submission.__table__.columns}
    assert not cols & {"parent_id", "reply_to", "thread_id", "mentions"}


# -- appeals ------------------------------------------------------------------------------------------------------


async def test_a_rejected_take_can_be_appealed_into_the_complaints_desk(env, db, clock):
    await make_eligible(db, clock)
    rej = await send(
        env, clock, body="Only an idiot would bake this, I made it twice and it was awful."
    )
    async with db.session() as s:
        subs = env.subs(s, clock)
        with pytest.raises(LookupError):
            await subs.appeal(
                "someone-else", rej.id, name="X", email="x@example.com", message="unfair"
            )
        c = await subs.appeal(
            "u1",
            rej.id,
            name="Asha",
            email="asha@example.com",
            message="I was quoting my aunt, not insulting anyone.",
        )
        assert (
            (c.category, c.org_id) == (APPEAL_CATEGORY, ORG)
            and str(rej.id) in c.body
            and "keep it civil" in c.body
        )
        ref = c.reference
        with pytest.raises(SubmissionError, match="only a rejected"):
            await subs.appeal("u1", rej.id, name="Asha", email="asha@example.com", message="again")
    async with db.session() as s:
        sub = await s.get(Submission, rej.id)
        assert sub.status == "appealed" and sub.appeal_complaint_id
        with pytest.raises(SubmissionError, match="can no longer be edited"):
            await env.subs(s, clock).submit("u1", ORG, "pancakes", "for", GOOD)
        assert (await s.get(Complaint, sub.appeal_complaint_id)).reference == ref


async def test_upholding_an_appeal_accepts_the_take_and_closes_the_complaint(env, db, clock):
    await make_eligible(db, clock)
    rej = await send(
        env, clock, body="Only an idiot would bake this, I made it twice and it was awful."
    )
    async with db.session() as s:
        await env.subs(s, clock).appeal(
            "u1",
            rej.id,
            name="Asha",
            email="asha@example.com",
            message="I was quoting my aunt, not insulting anyone.",
        )
    async with db.session() as s:
        subs = env.subs(s, clock)
        with pytest.raises(SubmissionError, match="no open appeal"):
            await subs.resolve_appeal("other-org", rej.id, STAFF, upheld=True, resolution="x")
        done = await subs.resolve_appeal(
            ORG, rej.id, STAFF, upheld=True, resolution="Context makes it fine; reinstated."
        )
        assert done.status == "accepted" and done.points_awarded > 0
        c = await s.get(Complaint, done.appeal_complaint_id)
        assert c.status == "resolved" and c.resolution.startswith("Context")
        pub = await subs.public(ORG, "pancakes")
        assert pub["total"] == 1 and pub["items"][0]["author"] == "Asha"


async def test_a_denied_appeal_keeps_the_rejection(env, db, clock):
    await make_eligible(db, clock)
    rej = await send(
        env, clock, body="Only an idiot would bake this, I made it twice and it was awful."
    )
    async with db.session() as s:
        await env.subs(s, clock).appeal(
            "u1", rej.id, name="Asha", email="asha@example.com", message="please reconsider"
        )
    async with db.session() as s:
        done = await env.subs(s, clock).resolve_appeal(
            ORG, rej.id, STAFF, upheld=False, resolution="The insult stands."
        )
        assert (done.status, done.points_awarded) == ("rejected", 0)
        assert (await s.get(Complaint, done.appeal_complaint_id)).status == "resolved"
        with pytest.raises(SubmissionError, match="already appealed"):
            await env.subs(s, clock).appeal(
                "u1", rej.id, name="Asha", email="asha@example.com", message="again"
            )


# -- reading and personal data ----------------------------------------------------------------------------------------


async def test_public_reading_shows_only_accepted_takes_per_side_with_paging(env, db, clock):
    await make_eligible(db, clock)
    for i, user in enumerate(["u1", "u2", "u3"]):
        await make_eligible(db, clock, user)
        await send(env, clock, side="for", body=GOOD + f" Run {i}.", user=user)
    await send(
        env, clock, side="against", body="idiot idiot, and I cooked it twice", user="u1"
    )  # rejected: never public
    async with db.session() as s:
        subs = env.subs(s, clock)
        everything = await subs.public(ORG, "pancakes")
        assert everything["total"] == 3 and {i["side"] for i in everything["items"]} == {"for"}
        assert set(everything["items"][0]) == {
            "id",
            "side",
            "body",
            "author",
            "points",
            "at",
        }  # no user id, no email
        assert (await subs.public(ORG, "pancakes", "against"))["total"] == 0
        page = await subs.public(ORG, "pancakes", "for", limit=2, offset=2)
        assert page["total"] == 3 and len(page["items"]) == 1
        assert (await subs.public("other-org", "pancakes"))["total"] == 0


async def test_submissions_are_exported_and_erased_with_the_account(env, db, clock):
    await make_eligible(db, clock)
    await send(env, clock)

    class Comp:
        def __init__(self):
            self.ex, self.er = {}, {}

        def register_exporter(self, n, f):
            self.ex[n] = f

        def register_eraser(self, n, f):
            self.er[n] = f

    c = Comp()
    register_with_compliance(c)
    async with db.session() as s:
        out = await c.ex["engage"](s, "u1")
        assert out["submissions"][0]["body"] == GOOD
        await c.er["engage"](s, "u1")
    async with db.session() as s:
        assert (await env.subs(s, clock).public(ORG, "pancakes"))["total"] == 0


# -- HTTP ------------------------------------------------------------------------------------------------------------------


async def test_http_flow(env, db, clock):
    who = {"u": SimpleNamespace(sub="u1", name="Asha")}
    staff_p = {"p": Principal(ORG, "bo", frozenset({"reviewer"}))}

    async def account():
        return who["u"]

    async def principal():
        return staff_p["p"]

    app = create_app(Settings())
    app.state.db = db
    app.include_router(
        build_submissions_router(account, db_session, lambda s: env.subs(s, clock), principal),
        prefix="/api",
    )
    await make_eligible(db, clock)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as h:
        elig = (
            await h.get("/api/engage/items/pancakes/eligibility", params={"org_id": ORG})
        ).json()
        assert elig["ok"] is True
        who["u"] = SimpleNamespace(sub="u9", name="New")
        blocked = (
            await h.get("/api/engage/items/pancakes/eligibility", params={"org_id": ORG})
        ).json()
        assert blocked["reasons"] == ["onboarding_incomplete", "not_read"]
        bad = await h.put(
            "/api/engage/items/pancakes/submission",
            json={"org_id": ORG, "side": "for", "body": GOOD},
        )
        assert bad.status_code == 422 and bad.json()["error"]["type"] == "submission_rejected"
        who["u"] = SimpleNamespace(sub="u1", name="Asha")
        ok = await h.put(
            "/api/engage/items/pancakes/submission",
            json={
                "org_id": ORG,
                "side": "for",
                "body": "Only an idiot would bake this, I made it twice and it was awful.",
            },
        )
        assert ok.status_code == 200 and ok.json()["status"] == "pending"
        await env.drain()
        mine = (await h.get("/api/engage/me/submissions")).json()
        assert mine[0]["status"] == "rejected" and mine[0]["reasons"]
        sid = mine[0]["id"]
        ap = await h.post(
            f"/api/engage/submissions/{sid}/appeal",
            json={"message": "I was quoting.", "name": "Asha", "email": "a@example.com"},
        )
        assert ap.status_code == 201 and ap.json()["reference"].startswith("C-")
        denied = await h.post(
            f"/api/engage/submissions/{sid}/appeal",
            json={"message": "again", "name": "Asha", "email": "a@example.com"},
        )
        assert denied.status_code == 422
        staff_p["p"] = Principal(ORG, "ann", frozenset({"editor"}))
        assert (await h.get("/api/engage/staff/review-queue")).status_code == 403
        staff_p["p"] = Principal(ORG, "bo", frozenset({"reviewer"}))
        assert (await h.get("/api/engage/staff/review-queue")).json() == []
        res = await h.post(
            f"/api/engage/staff/submissions/{sid}/appeal",
            json={"upheld": True, "resolution": "Reinstated."},
        )
        assert res.json()["status"] == "accepted"
        pub = (
            await h.get("/api/engage/public/items/pancakes/submissions", params={"org_id": ORG})
        ).json()
        assert pub["total"] == 1 and pub["items"][0]["author"] == "Asha"
        assert (
            await h.post(
                f"/api/engage/submissions/{uuid.uuid4()}/appeal",
                json={"message": "x", "name": "A", "email": "a@example.com"},
            )
        ).status_code == 404


async def test_old_rejected_takes_are_purged_but_appealed_and_accepted_ones_stay(env, db, clock):
    from tsp.engage import purge_rejected

    await make_eligible(db, clock)
    rej = await send(
        env, clock, side="a", body="idiot idiot, but I cooked it twice for the record."
    )
    ok = await send(env, clock, side="b", body=GOOD)
    appealed = await send(
        env, clock, side="c", body="idiot idiot, I cooked it twice and it was fine."
    )
    async with db.session() as s:
        await env.subs(s, clock).appeal(
            "u1", appealed.id, name="A", email="a@example.com", message="please"
        )
    async with db.session() as s:
        await s.execute(text("update en_submissions set decided_at = now() - interval '100 days'"))
        assert await purge_rejected(s) == 1
        left = {r.id for r in (await s.execute(select(Submission))).scalars()}
    assert left == {ok.id, appealed.id} and rej.id not in left
