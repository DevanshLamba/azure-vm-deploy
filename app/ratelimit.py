"""In-memory sliding-window rate limiter for /api routes (one app process, so memory is enough).

Client IP: behind nginx every request arrives from the proxy's address, so the real visitor IP
comes from X-Real-IP / X-Forwarded-For. Those headers are trivially spoofable, so they are only
honoured when the TCP peer is a trusted proxy (TRUSTED_PROXIES, a comma-separated list of
IPs/CIDRs, e.g. the nginx container's fixed address). From anyone else they are ignored.
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
        # nginx overwrites X-Real-IP with $remote_addr, so it can't carry a client-supplied value.
        real = _valid_ip(headers.get("x-real-ip"))
        if real:
            return real
        # Fallback: the right-most X-Forwarded-For entry is the one our proxy added.
        xff = headers.get("x-forwarded-for", "")
        last = _valid_ip(xff.split(",")[-1]) if xff else None
        if last:
            return last
    return peer


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int | None = None, window: float = 60.0, trusted_proxies: str | None = None):
        super().__init__(app)
        self.limit = limit or int(os.environ.get("RATE_LIMIT_PER_MIN", "300"))
        self.window = window
        self.trusted = parse_networks(
            trusted_proxies if trusted_proxies is not None else os.environ.get("TRUSTED_PROXIES", "")
        )
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    async def dispatch(self, request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        ip = client_ip(request.client.host if request.client else None, request.headers, self.trusted)
        now = time.monotonic()
        with self.lock:
            q = self.hits[ip]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                retry = int(self.window - (now - q[0])) + 1
                return JSONResponse(
                    {"detail": "Too many requests, slow down a little."},
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                )
            q.append(now)
            if len(self.hits) > 10_000:  # keep memory bounded
                self.hits = defaultdict(deque, {k: v for k, v in self.hits.items() if v})
        return await call_next(request)
