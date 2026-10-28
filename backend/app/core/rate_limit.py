import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

SWEEP_EVERY_SECONDS = 60


class RateLimiter:
    """Sliding-window limiter in process memory: per instance, so N replicas allow N x limit.

    Idle keys are dropped once their own window has passed, so memory is bounded by recent callers.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._windows: dict[str, float] = {}
        self._lock = threading.Lock()
        self._clock = clock
        self._next_sweep = clock() + SWEEP_EVERY_SECONDS

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        """Record a hit unless the key is already at its limit."""
        with self._lock:
            hits = self._recent(key, window_seconds)
            if len(hits) >= limit:
                return False
            hits.append(self._clock())
            return True

    def forgive(self, key: str) -> None:
        """Give back the most recent hit, e.g. a slot reserved for an attempt that turned out fine."""
        with self._lock:
            if hits := self._hits.get(key):
                hits.pop()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._windows.clear()

    def __len__(self) -> int:
        return len(self._hits)

    def _recent(self, key: str, window_seconds: float) -> deque[float]:
        now = self._clock()
        self._windows[key] = max(self._windows.get(key, 0.0), window_seconds)
        if now >= self._next_sweep:
            # Each key by its own window: a 24 h key elsewhere must not keep 15-minute keys alive.
            for stale in [k for k, h in self._hits.items() if not h or h[-1] <= now - self._windows.get(k, 0.0)]:
                del self._hits[stale]
                self._windows.pop(stale, None)
            self._next_sweep = now + SWEEP_EVERY_SECONDS
        hits = self._hits[key]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        return hits


auth_limiter = RateLimiter()
upload_limiter = RateLimiter()
