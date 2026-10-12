from datetime import datetime, timedelta, timezone

import pytest
from tsp.trends import Point, Scored, combine, daily_value, dedupe, series_z, zscore

END = datetime(2026, 3, 1, tzinfo=timezone.utc)


def history(values, end=END):
    """One point per day, the newest ending at ``end`` (so ``values[0]`` is today)."""
    return [Point(end - timedelta(days=i), v) for i, v in enumerate(values)]


def test_recency_decay_halves_every_twelve_hours():
    pts = [
        Point(END, 8.0),
        Point(END - timedelta(hours=12), 8.0),
        Point(END - timedelta(hours=24), 8.0),
    ]
    # the last point is on the boundary and belongs to the previous day
    assert daily_value(pts, END) == pytest.approx(8.0 + 4.0)


def test_points_outside_the_day_are_ignored():
    assert (
        daily_value(
            [Point(END + timedelta(hours=1), 5.0), Point(END - timedelta(hours=30), 5.0)], END
        )
        == 0.0
    )


def test_spike_scores_high_and_flat_scores_near_zero():
    base = [10, 11, 9, 10, 12, 8, 10] * 4  # 28 days, mean 10, sd ~1.2
    spike = series_z(history([60] + base), END)
    flat = series_z(history([10] + base), END)
    assert spike is not None and spike > 5
    assert flat is not None and abs(flat) < 1


def test_too_little_history_gives_no_score():
    assert series_z(history([50, 10, 10, 10]), END) is None
    assert zscore(5.0, [1.0] * 6) is None


def test_flat_history_does_not_explode():
    z = zscore(11.0, [10.0] * 28)
    assert z == pytest.approx(1.0)  # spread is floored at 10% of the mean, at least 1


def test_z_is_capped():
    assert zscore(1e9, [10.0] * 28) == 10.0


def test_negative_signal_does_not_cancel_a_spike():
    assert combine({"a": 6.0, "b": -3.0}, {}) == 6.0
    assert combine({"a": 2.0, "b": 3.0}, {"a": 1.0, "b": 0.5}) == pytest.approx(3.5)


def scored(label, score, **c):
    return Scored(label, label, None, score, c)


def test_dedupe_by_words_without_embeddings():
    out = dedupe(
        [
            scored("Vegan Butter", 3, a=3.0),
            scored("vegan  butter!", 5, b=5.0),
            scored("miso", 4, a=4.0),
        ]
    )
    assert [c.label for c in out] == ["vegan  butter!", "miso"]
    assert out[0].components == {"b": 5.0, "a": 3.0}


def test_dedupe_by_embedding_similarity():
    vecs = [[1.0, 0.0], [0.99, 0.05], [0.0, 1.0]]
    out = dedupe([scored("air fryer", 2), scored("air-fryers", 3), scored("kimchi", 1)], vecs, 0.9)
    assert [c.label for c in out] == ["air-fryers", "kimchi"]
