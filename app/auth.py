"""Invite-only authentication: argon2id password hashes, server-side sessions, CSRF, throttling.

- Passwords: argon2id (argon2-cffi) with the OWASP-recommended minimum (19 MiB, t=2, p=1), which
  keeps memory use safe inside the 256 MiB container. Never stored or logged in plain text.
- Sessions: a 256-bit random token in an HttpOnly, Secure, SameSite=Lax cookie. The database only
  stores HMAC-SHA256(secret, token), so a copy of the database alone can't be used to log in.
  Logout or a password reset deletes the session rows (real revocation).
- Session secret: SESSION_SECRET from the environment, or a random file created once next to the
  database (mode 0600). Never in git, never printed.
- CSRF: every state-changing /api request needs the per-session token in X-CSRF-Token (compared in
  constant time) and, when the browser sends an Origin header, it must match our own host.
- Login throttling (in memory): failed attempts per IP and per username, plus a fixed delay on
  every failure. Messages are generic so they never reveal whether a username exists.
"""
import asyncio
import hashlib
import hmac
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import db
from .ratelimit import SlidingWindow, request_ip

hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)  # argon2id by default

PASSWORD_MIN, PASSWORD_MAX = 12, 128
USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
ROLES = ("admin", "user", "demo")
SESSION_TTL = int(os.environ.get("SESSION_TTL_SECONDS", str(7 * 24 * 3600)))
GENERIC_LOGIN_ERROR = "Wrong username or password."
TOO_MANY_LOGINS = "Too many sign-in attempts. Please wait a few minutes and try again."
DEMO_READ_ONLY = "Demo account is read-only."
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# A real hash to verify against when the username doesn't exist, so the response takes the same
# time whether or not the account exists (no user enumeration through timing).
_DUMMY_HASH = hasher.hash(secrets.token_urlsafe(16))


# ---------------------------------------------------------------- settings / secret
def cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "1") != "0"


def cookie_name() -> str:
    # The __Host- prefix makes browsers refuse the cookie unless it is Secure, host-only and Path=/.
    return "__Host-ct_session" if cookie_secure() else "ct_session"


def _load_secret() -> bytes:
    env = os.environ.get("SESSION_SECRET", "")
    if env:
        if len(env) < 32:
            raise RuntimeError("SESSION_SECRET must be at least 32 characters")
        return env.encode()
    path = Path(db.db_path()).parent / ".session_secret"
    try:  # create once, atomically, readable only by the app user
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    return path.read_text().strip().encode()


_secret: bytes | None = None


def secret() -> bytes:
    global _secret
    if _secret is None:
        _secret = _load_secret()
    return _secret


def reset_secret_cache():  # used by tests
    global _secret
    _secret = None


def token_hash(token: str) -> str:
    return hmac.new(secret(), token.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- passwords
def validate_username(username: str) -> str:
    username = (username or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise ValueError("Username: 3-32 characters, lowercase letters, digits, '.', '_' or '-'.")
    return username


def validate_password(password: str, username: str = "") -> None:
    if len(password) < PASSWORD_MIN:
        raise ValueError(f"Password must be at least {PASSWORD_MIN} characters.")
    if len(password) > PASSWORD_MAX:
        raise ValueError(f"Password must be at most {PASSWORD_MAX} characters.")
    if username and password.strip().lower() == username.lower():
        raise ValueError("Password must not be the same as the username.")


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ---------------------------------------------------------------- throttling
class Throttle:
    def __init__(self):
        window = float(os.environ.get("LOGIN_WINDOW_SECONDS", "900"))
        self.per_ip = SlidingWindow(int(os.environ.get("LOGIN_MAX_FAILURES_PER_IP", "10")), window)
        self.per_user = SlidingWindow(int(os.environ.get("LOGIN_MAX_FAILURES_PER_USER", "5")), window)
        self.failure_delay = float(os.environ.get("LOGIN_FAILURE_DELAY", "1.0"))
        self.demo_api = SlidingWindow(int(os.environ.get("DEMO_RATE_LIMIT_PER_MIN", "60")), 60.0)


# ---------------------------------------------------------------- sessions
def new_session(user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)  # 256 bits
    csrf = secrets.token_urlsafe(32)
    now = int(time.time())
    db.create_session(token_hash(token), user_id, csrf, now, now + SESSION_TTL)
    return token, csrf


def set_session_cookie(response, token: str):
    response.set_cookie(
        cookie_name(), token, max_age=SESSION_TTL, path="/",
        httponly=True, secure=cookie_secure(), samesite="lax",
    )


def clear_session_cookie(response):
    response.delete_cookie(cookie_name(), path="/", httponly=True, secure=cookie_secure(), samesite="lax")


def session_from_request(request: Request) -> dict | None:
    token = request.cookies.get(cookie_name())
    if not token or len(token) > 100:
        return None
    s = db.get_session(token_hash(token))
    if not s:
        return None
    if s["expires_at"] <= time.time():
        db.delete_session(token_hash(token))
        return None
    s["token"] = token
    return s


async def login(request: Request, username: str, password: str) -> dict:
    """Check credentials with throttling. Returns the user row or raises 401/429 (generic)."""
    throttle: Throttle = request.app.state.throttle
    ip = request_ip(request)
    name = (username or "").strip().lower()[:64]
    user_key = f"user:{name}"

    if throttle.per_ip.blocked(ip):
        raise HTTPException(429, TOO_MANY_LOGINS)
    user = db.get_user_by_name(name) if USERNAME_RE.match(name) else None
    is_demo = bool(user and user["role"] == "demo")
    # The demo password is public, so locking its username would only let strangers lock everyone
    # out of the demo. Its per-IP limit still applies.
    if not is_demo and throttle.per_user.blocked(user_key):
        raise HTTPException(429, TOO_MANY_LOGINS)

    stored = user["password_hash"] if user else _DUMMY_HASH
    ok = await run_in_threadpool(verify_password, stored, (password or "")[:PASSWORD_MAX * 4])
    if user and ok:
        throttle.per_user.reset(user_key)
        return user

    throttle.per_ip.add(ip)
    if not is_demo:
        throttle.per_user.add(user_key)
    await asyncio.sleep(throttle.failure_delay)
    raise HTTPException(401, GENERIC_LOGIN_ERROR)


# ---------------------------------------------------------------- request checks (FastAPI deps)
def check_origin(request: Request):
    """Reject cross-site state-changing requests: browsers always send Origin on POST/PATCH/DELETE."""
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc.lower() != (request.headers.get("host") or "").lower():
        raise HTTPException(403, "Cross-site request blocked.")


def current_user(request: Request) -> dict:
    """Dependency: a valid session is required (401 otherwise). Applies CSRF + demo checks."""
    s = session_from_request(request)
    if not s:
        raise HTTPException(401, "Please sign in.")
    if request.method in UNSAFE_METHODS:
        check_origin(request)
        sent = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(sent.encode(), s["csrf_token"].encode()):
            raise HTTPException(403, "Missing or invalid CSRF token. Reload the page and try again.")
    if s["role"] == "demo":
        retry = request.app.state.throttle.demo_api.hit(request_ip(request))
        if retry:
            raise HTTPException(429, "The demo account is busy. Please slow down a little.",
                                headers={"Retry-After": str(retry)})
    return s


def writer(request: Request) -> dict:
    """Dependency for writes: logged in, valid CSRF, and not the read-only demo account."""
    s = current_user(request)
    if s["role"] == "demo":
        raise HTTPException(403, DEMO_READ_ONLY)
    return s
