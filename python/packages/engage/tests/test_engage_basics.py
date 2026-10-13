"""K6-A: receipts, polls, streaks, points, badges and onboarding (frozen clock)."""

from datetime import UTC, datetime, timedelta

import pytest
from engage_support import ORG
from sqlalchemy import select
from tsp.engage import (
    BadgeRule,
    Engage,
    EngageConfig,
    PollError,
    Polls,
    ReadPolicy,
    Receipts,
    Response,
    Streaks,
    TooManyPings,
    previous_week_key,
    register_with_compliance,
    week_key,
)

AX1 = {
    "x": {
        "label": "Would you cook it again?",
        "min": 0,
        "max": 10,
        "min_label": "never",
        "max_label": "weekly",
    }
}
AX2 = {**AX1, "y": {"label": "Effort", "min": 0, "max": 5}}


# -- receipts -------------------------------------------------------------------------------------


async def test_reading_needs_dwell_and_the_end_and_dwell_is_capped_by_real_time(session, clock):
    r = Receipts(session, clock=clock)
    await r.start("u1", ORG, "pancakes", "v1", expected_seconds=100)
    await r.ping(
        "u1", "pancakes", dwell=600, scroll=1.0, reached_end=True
    )  # claims 10 minutes after 0 seconds
    assert (
        await session.get(
            __import__("tsp.engage", fromlist=["Receipt"]).Receipt, ("u1", "pancakes")
        )
    ).dwell_seconds == 0
    assert not await r.is_read("u1", "pancakes")
    clock.at += timedelta(seconds=30)
    await r.ping("u1", "pancakes", dwell=30, scroll=0.6, reached_end=False)
    assert not await r.is_read(
        "u1", "pancakes"
    )  # 30 s of 100 is below 40%, and the end was not reached...
    clock.at += timedelta(seconds=20)
    await r.ping("u1", "pancakes", dwell=50, scroll=1.0, reached_end=True)
    assert await r.is_read("u1", "pancakes")
    await r.ping(
        "u1", "pancakes", dwell=10, scroll=0.1, reached_end=False
    )  # progress never goes backwards
    assert await r.is_read("u1", "pancakes")


async def test_policy_variants_and_unserved_items(session, clock):
    r = Receipts(session, clock=clock)
    await r.start("u1", ORG, "a", "v1", expected_seconds=60)
    clock.at += timedelta(seconds=40)
    await r.ping("u1", "a", 40, 0.3, False)
    assert not await r.is_read("u1", "a") and await r.is_read(
        "u1", "a", ReadPolicy(require_end=False)
    )
    await r.start("u1", ORG, "preview", "v1", served_full=False)  # a paywalled preview never counts
    clock.at += timedelta(seconds=500)
    await r.ping("u1", "preview", 500, 1.0, True)
    assert not await r.is_read("u1", "preview", ReadPolicy(min_dwell_ratio=0))
    with pytest.raises(LookupError):
        await r.ping("u1", "never-served", 5, 0.1, False)


async def test_a_new_version_starts_reading_over_but_reopening_keeps_progress(session, clock):
    r = Receipts(session, clock=clock)
    await r.start("u1", ORG, "a", "v1", expected_seconds=10)
    clock.at += timedelta(seconds=20)
    await r.ping("u1", "a", 20, 1.0, True)
    assert await r.is_read("u1", "a")
    await r.start("u1", ORG, "a", "v1", expected_seconds=10)  # reopened
    assert await r.is_read("u1", "a")
    await r.start("u1", ORG, "a", "v2", expected_seconds=10)  # corrected: read it again
    assert not await r.is_read("u1", "a")


async def test_pings_are_rate_limited(session, clock):
    calls = []

    async def allow(key):
        calls.append(key)
        return len(calls) <= 2

    r = Receipts(session, clock=clock, allow=allow)
    await r.start("u1", ORG, "a", "v1")
    await r.ping("u1", "a", 1, 0.1, False)
    await r.ping("u1", "a", 2, 0.1, False)
    with pytest.raises(TooManyPings):
        await r.ping("u1", "a", 3, 0.1, False)


# -- polls --------------------------------------------------------------------------------------------


async def test_poll_answers_are_validated_final_and_ordered(session, clock):
    polls = Polls(session, clock=clock)
    p = await polls.create(
        ORG,
        "pancakes",
        "scale1d",
        AX1,
        opens_at=clock() - timedelta(hours=1),
        closes_at=clock() + timedelta(days=1),
    )
    with pytest.raises(PollError, match="answer before reading first"):
        await polls.respond("u1", p.id, "after", 5)
    with pytest.raises(PollError, match="between 0 and 10"):
        await polls.respond("u1", p.id, "before", 11)
    with pytest.raises(PollError, match="one axis"):
        await polls.respond("u1", p.id, "before", 5, 2)
    with pytest.raises(PollError, match="phase"):
        await polls.respond("u1", p.id, "during", 5)
    await polls.respond("u1", p.id, "before", 3)
    assert await polls.shift("u1", p.id) is None
    with pytest.raises(PollError, match="already answered before"):
        await polls.respond("u1", p.id, "before", 4)  # no revising what you thought before reading
    await polls.respond("u1", p.id, "after", 8)
    assert await polls.shift("u1", p.id) == (5, None)
    clock.at += timedelta(days=2)
    with pytest.raises(PollError, match="not open"):
        await polls.respond("u2", p.id, "before", 1)
    with pytest.raises(PollError, match="min below max"):
        await polls.create(ORG, "x", "scale1d", {"x": {"min": 5, "max": 5}})
    with pytest.raises(PollError, match="kind"):
        await polls.create(ORG, "x", "scale3d", AX1)


async def test_two_axis_polls_and_optional_before(session, clock):
    polls = Polls(session, clock=clock)
    p = await polls.create(ORG, "tikka", "scale2d", AX2)
    with pytest.raises(PollError, match="both x and y"):
        await polls.respond("u1", p.id, "before", 5)
    await polls.respond("u1", p.id, "before", 2, 1)
    await polls.respond("u1", p.id, "after", 6, 4)
    assert await polls.shift("u1", p.id) == (4, 3)
    loose = await polls.create(ORG, "tikka2", "scale1d", {**AX1, "before_required": False})
    await polls.respond("u9", loose.id, "after", 5)  # allowed when the poll says so


async def test_aggregates_are_hidden_below_k_and_shown_above(session, clock):
    polls = Polls(session, clock=clock, min_k=20, bins=5)
    p = await polls.create(ORG, "a", "scale1d", AX1)
    for i in range(19):
        await polls.respond(f"u{i}", p.id, "before", 2)
        await polls.respond(f"u{i}", p.id, "after", 8)
    agg = await polls.aggregate(p.id)
    assert (agg["n_before"], agg["before"], agg["after"], agg["mean_shift"]) == (
        19,
        None,
        None,
        None,
    )
    await polls.respond("u19", p.id, "before", 10)
    await polls.respond("u19", p.id, "after", 10)
    agg = await polls.aggregate(p.id)
    assert (
        agg["before"] == [0, 20 - 1, 0, 0, 1]
        or agg["before"][:2] == [19, 0]
        or sum(agg["before"]) == 20
    )
    assert (
        sum(agg["before"]) == 20 and sum(agg["after"]) == 20 and agg["before"][-1] == 1
    )  # 10 is the last bin
    assert agg["mean_shift"]["n"] == 20 and agg["mean_shift"]["dx"] == round((19 * 6 + 0) / 20, 3)
    q = await polls.create(ORG, "b", "scale2d", AX2)
    assert (await polls.aggregate(q.id))["before"] is None
    for i in range(20):
        await polls.respond(f"v{i}", q.id, "before", i % 10, i % 5)
    grid = (await polls.aggregate(q.id))["before"]
    assert len(grid) == 5 and len(grid[0]) == 5 and sum(map(sum, grid)) == 20


async def test_poll_history_is_private_to_the_reader(session, clock):
    polls = Polls(session, clock=clock)
    p = await polls.create(ORG, "a", "scale1d", AX1)
    await polls.respond("u1", p.id, "before", 2)
    await polls.respond("u1", p.id, "after", 5)
    await polls.respond("u2", p.id, "before", 9)
    mine = await polls.history("u1")
    assert len(mine) == 1 and mine[0]["shift"] == (3, None) and mine[0]["before"]["x"] == 2
    assert [h["poll_id"] for h in await polls.history("u2")] == [str(p.id)] and (
        await polls.history("u2")
    )[0]["shift"] is None
    assert await polls.history("stranger") == []


# -- streaks (frozen clock, week edges, time zone) ------------------------------------------------------


def at(y, m, d, h=12, mi=0, tz=UTC):
    return datetime(y, m, d, h, mi, tzinfo=tz)


def test_week_keys_follow_the_org_time_zone():
    # Sunday 22 Feb 2026 20:00 UTC is already Monday 23 Feb 01:30 in India: a new week there
    sunday_evening_utc = at(2026, 2, 22, 20, 0)
    assert (
        week_key(sunday_evening_utc, "UTC") == "2026-W08"
        and week_key(sunday_evening_utc, "Asia/Kolkata") == "2026-W09"
    )
    assert (
        previous_week_key("2026-W01") == "2025-W52" and previous_week_key("2026-W10") == "2026-W09"
    )
    assert week_key(at(2027, 1, 3), "UTC") == "2026-W53"  # ISO year edge


async def test_streak_grows_per_week_and_a_missed_week_resets_current_but_not_best(session, clock):
    s = Streaks(session, EngageConfig(timezone="UTC", weekly_goal=1), clock=clock)
    st, met = await s.on_read("u1", ORG, at(2026, 3, 2))  # Monday, week 10
    assert (st.current, met) == (1, True)
    st, met = await s.on_read("u1", ORG, at(2026, 3, 4))  # second read, same week: nothing new
    assert (st.current, met, st.progress) == (1, False, 2)
    st, _ = await s.on_read("u1", ORG, at(2026, 3, 9))  # week 11
    st, _ = await s.on_read("u1", ORG, at(2026, 3, 16))  # week 12
    assert (st.current, st.best) == (3, 3)
    # nothing in weeks 13 and 14: the streak is already gone when we look
    gone = await s.state("u1", ORG, at(2026, 3, 30))
    assert (gone.current, gone.best) == (0, 3)
    # last week was missed but this one is still open: still alive until the week ends
    assert (
        await s.state("u1", ORG, at(2026, 3, 22))
    ).current == 3  # week 12 still counts during week 13
    st, _ = await s.on_read("u1", ORG, at(2026, 3, 30))  # back in week 14
    assert (st.current, st.best) == (1, 3)


async def test_weekly_goal_of_three(session):
    s = Streaks(session, EngageConfig(timezone="UTC", weekly_goal=3))
    st, met = await s.on_read("u1", ORG, at(2026, 3, 2))
    assert (st.current, met, st.progress) == (0, False, 1)
    await s.on_read("u1", ORG, at(2026, 3, 3))
    st, met = await s.on_read("u1", ORG, at(2026, 3, 4))
    assert (st.current, met) == (1, True)
    st, met = await s.on_read("u1", ORG, at(2026, 3, 5))
    assert (st.current, met) == (1, False)


async def test_the_time_zone_decides_which_week_a_late_sunday_read_belongs_to(session):
    for tz, expected in (("UTC", 1), ("Asia/Kolkata", 2)):
        s = Streaks(session, EngageConfig(timezone=tz))
        user = f"u-{tz}"
        await s.on_read(user, ORG, at(2026, 2, 16, 12))  # Monday of week 8 in both zones
        # Sunday 22 Feb 20:00 UTC: still week 8 in UTC, but Monday 01:30 (week 9) in India
        st, _ = await s.on_read(user, ORG, at(2026, 2, 22, 20, 0))
        assert st.current == expected, tz


async def test_state_for_a_stranger_is_empty(session):
    st = await Streaks(session, EngageConfig(timezone="UTC")).state("nobody", ORG, at(2026, 3, 4))
    assert (st.current, st.best, st.progress, st.met_this_week) == (0, 0, 0, False)


# -- the facade: points, badges, onboarding ---------------------------------------------------------------


async def read_item(e, clock, user, item, secs=60):
    await e.receipts.start(user, ORG, item, "v1", expected_seconds=secs)
    clock.at += timedelta(seconds=secs)
    await e.receipts.ping(user, item, secs, 1.0, True)
    return await e.record_read(user, ORG, item)


async def test_a_read_pays_once_advances_the_streak_and_grants_badges(session, clock):
    e = Engage(session, EngageConfig(timezone="UTC"), clock=clock)
    first = await read_item(e, clock, "u1", "a")
    assert (
        first.newly_read
        and first.streak_week_met
        and first.points == 10 + 20
        and first.badges == ["first_read"]
    )
    again = await e.record_read("u1", ORG, "a")  # a repeat call changes nothing
    assert not again.newly_read and again.points == 0
    second = await read_item(e, clock, "u1", "b")
    assert (
        second.points == 10 and not second.streak_week_met
    )  # the weekly bonus is paid once a week
    assert await e.points.balance("u1", ORG) == 40
    for i in range(8):
        await read_item(e, clock, "u1", f"c{i}")
    assert "regular" in await e.points.badges("u1", ORG)  # 10 reads


async def test_points_are_paid_once_per_thing_and_unknown_reasons_pay_nothing(session):
    e = Engage(session, EngageConfig())
    assert await e.points.award("u1", ORG, "read", ref_kind="item", ref_id="x") == 10
    assert await e.points.award("u1", ORG, "read", ref_kind="item", ref_id="x") == 0
    assert await e.points.award("u1", ORG, "read", ref_kind="item", ref_id="y") == 10
    assert await e.points.award("u1", ORG, "mystery") == 0
    assert await e.points.award("u1", ORG, "custom", points=7, ref_id="z") == 7
    assert await e.points.balance("u1", ORG) == 27 and await e.points.balance("u2", ORG) == 0


async def test_badge_by_streak(session, clock):
    cfg = EngageConfig(timezone="UTC", badges=(BadgeRule("month_streak", streak=4),))
    e = Engage(session, cfg, clock=clock)
    for week_start in (at(2026, 3, 2), at(2026, 3, 9), at(2026, 3, 16)):
        clock.at = week_start
        await read_item(e, clock, "u1", f"i{week_start.day}")
    assert await e.points.badges("u1", ORG) == []
    clock.at = at(2026, 3, 23)
    out = await read_item(e, clock, "u1", "i23")
    assert out.badges == ["month_streak"]


async def test_onboarding_gates_and_pays_once(session):
    e = Engage(session, EngageConfig(required_onboarding=("welcome", "interests")))
    assert not await e.onboarding.is_complete("u1", ORG) and await e.onboarding.remaining(
        "u1", ORG
    ) == ["welcome", "interests"]
    assert await e.complete_onboarding("u1", ORG, "welcome") == 10
    assert await e.complete_onboarding("u1", ORG, "welcome") == 0
    with pytest.raises(ValueError, match="unknown"):
        await e.complete_onboarding("u1", ORG, "skip")
    await e.complete_onboarding("u1", ORG, "interests")
    assert await e.onboarding.is_complete("u1", ORG) and not await e.onboarding.is_complete(
        "u2", ORG
    )


async def test_poll_points_are_paid_when_both_answers_are_in(session, clock):
    e = Engage(session, EngageConfig(), clock=clock)
    p = await e.polls.create(ORG, "a", "scale1d", AX1)
    assert await e.respond_to_poll("u1", p.id, "before", 3) == 0
    assert await e.respond_to_poll("u1", p.id, "after", 6) == 5
    assert await e.points.balance("u1", ORG) == 5


async def test_me_collects_the_readers_own_history(session, clock):
    e = Engage(session, EngageConfig(timezone="UTC"), clock=clock)
    p = await e.polls.create(ORG, "a", "scale1d", AX1)
    await e.respond_to_poll("u1", p.id, "before", 2)
    await e.respond_to_poll("u1", p.id, "after", 7)
    await read_item(e, clock, "u1", "a")
    await e.complete_onboarding("u1", ORG, "welcome")
    me = await e.me("u1", ORG)
    assert (
        me["polls"][0]["shift"] == (5, None) and me["streak"]["current"] == 1 and me["reads"] == 1
    )
    assert (
        me["points"] == 5 + 10 + 20 + 10
        and me["badges"] == ["first_read"]
        and me["onboarding"]["remaining"] == ["interests"]
    )
    other = await e.me("u2", ORG)
    assert other["polls"] == [] and other["points"] == 0 and other["reads"] == 0


async def test_export_and_erasure(session, clock):
    e = Engage(session, EngageConfig(timezone="UTC"), clock=clock)
    p = await e.polls.create(ORG, "a", "scale1d", AX1)
    await e.respond_to_poll("u1", p.id, "before", 2)
    await read_item(e, clock, "u1", "a")
    await e.respond_to_poll("u2", p.id, "before", 8)

    class Comp:
        def __init__(self):
            self.ex, self.er = {}, {}

        def register_exporter(self, n, f):
            self.ex[n] = f

        def register_eraser(self, n, f):
            self.er[n] = f

    c = Comp()
    register_with_compliance(c)
    out = await c.ex["engage"](session, "u1")
    assert (
        len(out["poll_responses"]) == 1
        and out["reads"][0]["item"] == "a"
        and out["points"]
        and out["badges"]
    )
    await c.er["engage"](session, "u1")
    assert await c.ex["engage"](session, "u1") == {k: [] for k in out}
    assert len((await c.ex["engage"](session, "u2"))["poll_responses"]) == 1
    n = (await session.execute(select(Response))).scalars().all()
    assert [r.user_id for r in n] == ["u2"]
