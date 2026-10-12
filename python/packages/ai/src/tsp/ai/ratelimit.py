import threading
import time
import uuid
from dataclasses import dataclass


@dataclass
class CooldownEntry:
    platform: str
    model_id: str
    key_id: str
    expires_at: float  # timestamp
    source: str = "heuristic"  # heuristic, authoritative, credit, tier


class RateLimitTracker:
    """Sliding-window rate limit tracker with in-memory cooldown management."""

    MINUTE = 60_000
    DAY = 24 * 60 * MINUTE

    # Cooldown escalation ladder
    ESCALATION_LADDER = [2 * 60 * 1000, 10 * 60 * 1000, 60 * 60 * 1000, 24 * 60 * 60 * 1000]
    MAX_COOLDOWN_MS = 24 * 60 * 60 * 1000

    def __init__(self):
        self._lock = threading.Lock()
        self._windows: dict[str, list[float]] = {}  # key -> [timestamps]
        self._token_windows: dict[str, list[tuple[float, int]]] = {}  # key -> [(ts, tokens)]
        self._cooldowns: dict[str, CooldownEntry] = {}  # composite_key -> CooldownEntry
        self._escalation_count: dict[str, int] = {}  # key -> hit count
        self._leases: dict[str, dict] = {}  # lease_id -> lease info

    def _window_key(self, platform: str, model_id: str, key_id: str, limit_type: str) -> str:
        return f"{platform}:{model_id}:{key_id}:{limit_type}"

    def can_make_request(
        self, platform: str, model_id: str, key_id: str, rpm_limit: int | None = None
    ) -> bool:
        """Check if a request is allowed under RPM limit."""
        if rpm_limit is None:
            return True
        key = self._window_key(platform, model_id, key_id, "rpm")
        now = time.time() * 1000
        with self._lock:
            return self._check_window(key, now, rpm_limit, self.MINUTE)

    def can_use_tokens(
        self,
        platform: str,
        model_id: str,
        key_id: str,
        tpm_limit: int | None = None,
        token_estimate: int = 0,
    ) -> bool:
        """Check if token usage is within TPM limit."""
        if tpm_limit is None:
            return True
        key = self._window_key(platform, model_id, key_id, "tpm")
        now = time.time() * 1000
        with self._lock:
            used = self._window_token_usage(key, now, self.MINUTE)
            return used + token_estimate <= tpm_limit

    def record_request(self, platform: str, model_id: str, key_id: str):
        """Record a successful request."""
        key = self._window_key(platform, model_id, key_id, "rpm")
        now = time.time() * 1000
        with self._lock:
            self._add_to_window(key, now)
            self._escalation_count.pop(key, None)

    def record_tokens(self, platform: str, model_id: str, key_id: str, tokens: int):
        """Record token usage."""
        for window_name in ["tpm", "tpd"]:
            key = self._window_key(platform, model_id, key_id, window_name)
            now = time.time() * 1000
            with self._lock:
                self._add_token_to_window(key, now, tokens)

    def _check_window(self, key: str, now: float, limit: int, window_ms: float) -> bool:
        timestamps = self._windows.get(key, [])
        cutoff = now - window_ms
        valid = [ts for ts in timestamps if ts > cutoff]
        return len(valid) < limit

    def _add_to_window(self, key: str, timestamp: float):
        if key not in self._windows:
            self._windows[key] = []
        self._windows[key].append(timestamp)
        # Prune old entries
        cutoff = timestamp - self.DAY
        self._windows[key] = [ts for ts in self._windows[key] if ts > cutoff]

    def _window_token_usage(self, key: str, now: float, window_ms: float) -> int:
        entries = self._token_windows.get(key, [])
        cutoff = now - window_ms
        return sum(t for ts, t in entries if ts > cutoff)

    def _add_token_to_window(self, key: str, timestamp: float, tokens: int):
        if key not in self._token_windows:
            self._token_windows[key] = []
        self._token_windows[key].append((timestamp, tokens))
        cutoff = timestamp - self.DAY
        self._token_windows[key] = [(ts, t) for ts, t in self._token_windows[key] if ts > cutoff]

    def set_cooldown(
        self,
        platform: str,
        model_id: str,
        key_id: str,
        expires_at: float,
        source: str = "heuristic",
    ):
        """Set a cooldown for a key."""
        composite = f"{platform}:{model_id}:{key_id}"
        cooldown = CooldownEntry(platform, model_id, key_id, expires_at, source)
        with self._lock:
            self._cooldowns[composite] = cooldown

    def is_cooled_down(self, platform: str, model_id: str, key_id: str) -> bool:
        """Return True if the key is not currently on cooldown."""
        cooldown = self.get_cooldown(platform, model_id, key_id)
        return cooldown is None

    def get_cooldown(self, platform: str, model_id: str, key_id: str) -> CooldownEntry | None:
        """Get current cooldown if any."""
        composite = f"{platform}:{model_id}:{key_id}"
        with self._lock:
            cooldown = self._cooldowns.get(composite)
            if cooldown and cooldown.expires_at > time.time() * 1000:
                return cooldown
            if cooldown:
                del self._cooldowns[composite]
            return None

    def acquire_lease(
        self, platform: str, model_id: str, key_id: str, max_concurrency: int = 1
    ) -> int | None:
        """Acquire a lease for concurrent request tracking."""
        key = f"{platform}:{model_id}:{key_id}"
        with self._lock:
            existing = [lease for lease in self._leases.values() if lease["key"] == key]
            if len(existing) >= max_concurrency:
                return None
            lease_id = str(uuid.uuid4())
            self._leases[lease_id] = {"key": key, "created_at": time.time() * 1000}
            return hash(lease_id)  # keep returning int for backward compatibility

    def release_lease(self, lease_id: int):
        """Release a lease."""
        with self._lock:
            to_remove = []
            for lid, _info in self._leases.items():
                if hash(lid) == lease_id:
                    to_remove.append(lid)
            for lid in to_remove:
                del self._leases[lid]

    def prune_expired(self):
        """Remove expired entries."""
        now = time.time() * 1000
        with self._lock:
            self._cooldowns = {k: v for k, v in self._cooldowns.items() if v.expires_at > now}

    def format_reset_eta(self, expires_at: float) -> str:
        """Format cooldown expiry as human-readable string."""
        delta_ms = expires_at - time.time() * 1000
        if delta_ms <= 0:
            return "now"
        secs = int(delta_ms / 1000)
        if secs < 90:
            return f"~{secs}s"
        mins = secs // 60
        if mins < 90:
            return f"~{mins}m"
        hours = mins // 60
        return f"~{hours}h"
