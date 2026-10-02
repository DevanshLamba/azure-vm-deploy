"""In-memory sliding-window rate limiting (one app process, so memory is enough).

Client IP: behind the reverse proxy every request arrives from the proxy's address, so the real
visitor IP comes from X-Real-IP / X-Forwarded-For. Those headers are trivially spoofable, so they
are only honoured when the TCP peer is a trusted proxy (TRUSTED_PROXIES, a comma-separated list of
IPs/CIDRs, e.g. the proxy container's fixed address). From anyone else they are ignored.
"""
import ipaddress
import os
import threading
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


def parse_networks(value: str) -> list:
    nets = []
    for part in (value or "").split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return nets


def _valid_ip(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def client_ip(peer: str | None, headers, trusted: list) -> str:
    """Return the visitor IP: the proxy-supplied one only if the peer is a trusted proxy."""
    peer = peer or "unknown"
    peer_ip = _valid_ip(peer)
    if peer_ip and any(ipaddress.ip_address(peer_ip) in net for net in trusted):
        # The proxy overwrites X-Real-IP with the remote address, so it can't carry a client value.
        real = _valid_ip(headers.get("x-real-ip"))
        if real:
            return real
        # Fallback: the right-most X-Forwarded-For entry is the one our proxy added.
        xff = headers.get("x-forwarded-for", "")
        last = _valid_ip(xff.split(",")[-1]) if xff else None
        if last:
            return last
    return peer


def request_ip(request) -> str:
    trusted = getattr(request.app.state, "trusted_proxies", [])
    return client_ip(request.client.host if request.client else None, request.headers, trusted)


class SlidingWindow:
    """Counts events per key inside a moving time window. Thread-safe, memory bounded."""

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self.events: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def _trim(self, q: deque, now: float):
        while q and now - q[0] > self.window:
            q.popleft()

    def blocked(self, key: str) -> int:
        """0 if `key` is under the limit, otherwise the seconds until it frees up."""
        now = time.monotonic()
        with self.lock:
            q = self.events.get(key)
            if not q:
                return 0
            self._trim(q, now)
            if len(q) >= self.limit:
                return int(self.window - (now - q[0])) + 1
            return 0

    def add(self, key: str):
        now = time.monotonic()
        with self.lock:
            q = self.events[key]
            self._trim(q, now)
            q.append(now)
            if len(self.events) > 10_000:
                self.events = defaultdict(deque, {k: v for k, v in self.events.items() if v})

    def hit(self, key: str) -> int:
        """Count one event; returns 0 if allowed, else Retry-After seconds (event not counted)."""
        retry = self.blocked(key)
        if not retry:
            self.add(key)
        return retry

    def reset(self, key: str):
        with self.lock:
            self.events.pop(key, None)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int | None = None, window: float = 60.0):
        super().__init__(app)
        self.window = SlidingWindow(limit or int(os.environ.get("RATE_LIMIT_PER_MIN", "300")), window)

    async def dispatch(self, request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        retry = self.window.hit(request_ip(request))
        if retry:
            return JSONResponse(
                {"detail": "Too many requests, slow down a little."},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        return await call_next(request)
