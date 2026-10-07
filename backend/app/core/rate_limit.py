"""In-process sliding-window limiter for sign-in and registration (V0.13).

Brute-force protection: too many failed sign-ins for one account, or from one client
address, within the window get HTTP 429 until the window has passed. The state lives in
the API process, which is enough for the single-process deployment JobPilot ships
(docker compose runs one uvicorn worker). Several API processes behind a load balancer
would each count separately; a shared store (e.g. Redis) is the documented next step
(docs/security.md).

Memory is bounded: old timestamps are pruned on every use and the number of tracked keys
is capped (the least recently used are dropped first).
"""

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable

_MAX_KEYS = 50_000


class SlidingWindowLimiter:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def _pruned(self, key: str, window: float, now: float) -> deque[float]:
        hits = self._hits.get(key)
        if hits is None:
            hits = deque()
            self._hits[key] = hits
            while len(self._hits) > _MAX_KEYS:
                self._hits.popitem(last=False)
        else:
            self._hits.move_to_end(key)
        while hits and hits[0] <= now - window:
            hits.popleft()
        return hits

    def retry_after(self, key: str, *, limit: int, window_seconds: float) -> float | None:
        """Seconds until `key` may try again, or None when it is under `limit`."""
        with self._lock:
            now = self._clock()
            hits = self._pruned(key, window_seconds, now)
            if len(hits) < limit:
                return None
            return max(0.0, hits[0] + window_seconds - now)

    def hit(self, key: str, *, window_seconds: float) -> None:
        with self._lock:
            now = self._clock()
            self._pruned(key, window_seconds, now).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


auth_limiter = SlidingWindowLimiter()
