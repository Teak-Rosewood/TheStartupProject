from tsp.cache import NullCache


class MemCache(NullCache):
    def __init__(self):
        self.d = {}

    async def get_json(self, key):
        return self.d.get(key)

    async def set_json(self, key, value, ttl=None):
        self.d[key] = value

    async def incr(self, key, ttl=None):
        self.d[key] = self.d.get(key, 0) + 1
        return self.d[key]

    async def delete(self, *keys):
        for k in keys:
            self.d.pop(k, None)
