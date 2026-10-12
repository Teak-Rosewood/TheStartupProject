import os
from types import SimpleNamespace

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
TABLES = "pw_policies, pw_reads, pw_gifts, pw_credits, pw_unlocks, alembic_version"
SECRET = "test-secret-with-enough-length-0123456789"


class MemCache:
    """The slice of tsp.cache.Cache the paywall uses."""

    def __init__(self):
        self.d = {}

    async def get_json(self, key):
        return self.d.get(key)

    async def set_json(self, key, value, ttl=None):
        self.d[key] = value


class NoDns:
    async def reverse(self, ip):
        return None

    async def forward(self, host):
        return []


class FakeDns:
    def __init__(self, table):
        self.t = table  # ip -> host; host -> [ips]

    async def reverse(self, ip):
        return self.t.get(ip)

    async def forward(self, host):
        return self.t.get(host, [])


def browser(
    ip="203.0.113.7",
    ua="Mozilla/5.0 Firefox/130",
    lang="en-IN,en;q=0.9",
    cookie=None,
    user=None,
    gift=None,
):
    from tsp.paywall import Visitor

    return Visitor(
        ip=ip, user_agent=ua, accept_language=lang, meter_cookie=cookie, user_id=user, gift=gift
    )


def cookie_of(decision):
    return decision.set_cookie


Namespace = SimpleNamespace
