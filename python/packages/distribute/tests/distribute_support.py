import os
from datetime import UTC, datetime, timedelta

from tsp.distribute import Item, Variant

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
NOW = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)

PANCAKES = Item(
    id="r1",
    slug="pancakes",
    published_at=NOW - timedelta(hours=5),
    updated_at=NOW - timedelta(hours=1),
    authors=("Asha Rao",),
    image_url="https://cdn.example.com/pancakes.jpg",
    keywords=("breakfast", "easy"),
    variants={
        "en": Variant(
            "Fluffy pancakes in ten minutes", "Whisk, rest, flip. That is the whole secret."
        ),
        "hi": Variant("दस मिनट में फूले हुए पैनकेक", "फेंटें, रखें, पलटें। बस यही राज़ है।"),
    },
)
PANEER = Item(
    id="r2",
    slug="paneer-tikka",
    published_at=NOW - timedelta(days=5),
    free=False,
    variants={"en": Variant("Paneer tikka at home & fast", "Smoky <b>grilled</b> cubes.")},
)


class MemStore:
    def __init__(self):
        self.objs: dict[str, tuple[bytes, str]] = {}
        self.writes = 0

    async def head(self, key):
        return (len(self.objs[key][0]), self.objs[key][1]) if key in self.objs else None

    async def write(self, key, data, content_type="application/octet-stream"):
        self.writes += 1
        self.objs[key] = (data, content_type)
