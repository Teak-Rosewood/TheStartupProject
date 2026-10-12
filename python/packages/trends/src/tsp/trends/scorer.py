"""Scoring maths, kept free of I/O so it can be tested on plain numbers."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

BASELINE_DAYS = 28
HALF_LIFE_HOURS = 12.0
MIN_BASELINE_DAYS = 7  # fewer prior days than this and a z-score would be noise
Z_CAP = 10.0


@dataclass(frozen=True)
class Point:
    at: datetime
    value: float


@dataclass
class Scored:
    key: str  # topic id, or the term for an emerging candidate
    label: str
    topic_id: str | None
    score: float
    components: dict[str, float] = field(default_factory=dict)


def daily_value(
    points: Sequence[Point], day_end: datetime, half_life_h: float = HALF_LIFE_HOURS
) -> float:
    """Sum of the values in the 24 hours before ``day_end``, each halved every ``half_life_h`` hours
    of age, so a burst this morning outweighs the same burst yesterday evening."""
    start = day_end - timedelta(hours=24)
    total = 0.0
    for p in points:
        if start < p.at <= day_end:
            age_h = (day_end - p.at).total_seconds() / 3600
            total += p.value * 0.5 ** (age_h / half_life_h)
    return total


def zscore(today: float, history: Sequence[float]) -> float | None:
    """``today`` against the prior days. ``None`` while there is too little history. The spread has a
    floor (10% of the mean, at least 1) so a flat history does not turn tiny changes into huge scores."""
    if len(history) < MIN_BASELINE_DAYS:
        return None
    mean = sum(history) / len(history)
    var = sum((h - mean) ** 2 for h in history) / len(history)
    sd = max(math.sqrt(var), 0.1 * mean, 1.0)
    return max(-Z_CAP, min(Z_CAP, (today - mean) / sd))


def series_z(
    points: Sequence[Point], day_end: datetime, half_life_h: float = HALF_LIFE_HOURS
) -> float | None:
    history = [
        daily_value(points, day_end - timedelta(days=d), half_life_h)
        for d in range(1, BASELINE_DAYS + 1)
    ]
    # Days before the first observation are not zeros, they are unknown.
    first = min((p.at for p in points), default=None)
    if first is None:
        return None
    history = [h for d, h in enumerate(history, 1) if day_end - timedelta(days=d) >= first]
    return zscore(daily_value(points, day_end, half_life_h), history)


def combine(z_by_signal: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Weighted sum of positive evidence. A signal that is below baseline does not cancel another
    that spikes; being quiet on one channel is not evidence against a story."""
    return sum(weights.get(s, 1.0) * z for s, z in z_by_signal.items() if z > 0)


_WORDS = re.compile(r"[^\W_]+(?:'[^\W_]+)?", re.UNICODE)


def normalise_label(label: str) -> str:
    return " ".join(w.lower() for w in _WORDS.findall(label))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


def dedupe(
    scored: Sequence[Scored],
    vectors: Sequence[Sequence[float]] | None = None,
    threshold: float = 0.9,
) -> list[Scored]:
    """Merge near-identical candidates, keeping the highest-scoring label. With ``vectors`` (one per
    candidate, from an embedder) similarity is cosine; without, labels match when their normalised
    words are equal. A merged candidate keeps the larger value for each signal."""
    order = sorted(range(len(scored)), key=lambda i: -scored[i].score)
    kept: list[int] = []
    out: dict[int, Scored] = {}
    for i in order:
        match = next(
            (
                k
                for k in kept
                if (
                    cosine(vectors[i], vectors[k]) >= threshold
                    if vectors is not None
                    else normalise_label(scored[i].label) == normalise_label(scored[k].label)
                )
            ),
            None,
        )
        if match is None:
            kept.append(i)
            c = scored[i]
            out[i] = Scored(c.key, c.label, c.topic_id, c.score, dict(c.components))
        else:
            for s, z in scored[i].components.items():
                out[match].components[s] = max(out[match].components.get(s, z), z)
    return [out[k] for k in kept]
