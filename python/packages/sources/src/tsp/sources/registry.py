from __future__ import annotations

from .connector import STRATEGY_NAMES, Connector

_CONNECTORS: dict[str, type[Connector]] = {}


def register_connector(cls: type[Connector]) -> type[Connector]:
    """Class decorator. Re-registering a key is an error, so two modules cannot silently shadow."""
    if cls.key in _CONNECTORS and _CONNECTORS[cls.key] is not cls:
        raise ValueError(f"connector {cls.key!r} is already registered")
    _CONNECTORS[cls.key] = cls
    return cls


def get_connector(key: str) -> type[Connector]:
    try:
        return _CONNECTORS[key]
    except KeyError:
        raise KeyError(f"no connector registered for source {key!r}") from None


def connectors() -> dict[str, type[Connector]]:
    return dict(_CONNECTORS)


def validate_strategies(names: list[str]) -> list[str]:
    bad = [n for n in names if n not in STRATEGY_NAMES]
    if bad:
        raise ValueError(f"unknown strategies {bad}; use {list(STRATEGY_NAMES)}")
    return names
