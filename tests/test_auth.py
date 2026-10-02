"""Authentication, sessions, CSRF, authorisation, throttling and the read-only demo account."""
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from conftest import add_user, login, new_client

WRITES = [
    ("post", "/api/tasks", {"title": "x"}),
    ("patch", "/api/tasks/{id}", {"title": "y"}),
    ("post", "/api/tasks/{id}/toggle", None),
    ("delete", "/api/tasks/{id}", None),
    ("post", "/api/tasks/sample", None),
]


def call(client, method, path, body=None, task_id=1):
    kw = {"json": body} if body is not None else {}
    return getattr(client, method)(path.format(id=task_id), **kw)


# ------------------------------------------------------------------ login / logout
def test_login_success_sets_hardened_cookie(app):
    pw = add_user("alice")
    c = new_client(app)
    r = login(c, "alice", pw)
    assert r.json()["username"] == "alice" and r.json()["role"] == "user" and r.json()["csrf_token"]
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-ct_session=")
    for attr in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=604800"):
        assert attr.lower() in cookie.lower(), attr
    me = c.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["username"] == "alice"
    assert c.get("/").status_code == 200  # the board page itself


def test_login_failure_is_generic(app):
    pw = add_user("alice")
    c = new_client(app)
    wrong_pw = c.post("/api/auth/login", json={"username": "alice", "password": pw + "x"})
    no_user = c.post("/api/auth/login", json={"username": "nobody", "password": pw})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json() == {"detail": "Wrong username or password."}
    assert "set-cookie" not in wrong_pw.headers


def test_failed_login_is_delayed(app_env, make_app):
    app_env.setenv("LOGIN_FAILURE_DELAY", "0.3")
    c = new_client(make_app())
    t0 = time.monotonic()
    assert c.post("/api/auth/login", json={"username": "nobody", "password": "whatever-123456"}).status_code == 401
    assert time.monotonic() - t0 >= 0.3


def test_logout_revokes_the_session(app):
    pw = add_user("alice")
    c = new_client(app)
    login(c, "alice", pw)
    old_cookie = c.cookies.get("__Host-ct_session")
    r = c.post("/api/auth/logout")
    assert r.status_code == 204
    assert "max-age=0" in r.headers["set-cookie"].lower()
    # Replaying the old cookie no longer works: the session was deleted on the server.
    replay = new_client(app)
    replay.cookies.set("__Host-ct_session", old_cookie)
    assert replay.get("/api/tasks").status_code == 401


def test_expired_session_is_rejected(app):
    from app import db
    pw = add_user("alice")
    c = new_client(app)
    login(c, "alice", pw)
    with db.connect() as conn:
        conn.execute("UPDATE sessions SET expires_at = ?", (int(time.time()) - 1,))
    assert c.get("/api/tasks").status_code == 401


# ------------------------------------------------------------------ access control
def test_no_access_without_a_session(app):
    c = new_client(app)
    for path in ("/api/tasks", "/api/metrics", "/api/info", "/api/auth/me"):
        assert c.get(path).status_code == 401, path
    assert c.post("/api/tasks", json={"title": "x"}).status_code == 401
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/login").status_code == 200
    assert c.get("/health").json() == {"status": "ok"}


def test_user_isolation(app):
    pw_a, pw_b = add_user("alice"), add_user("bob")
    a, b = new_client(app), new_client(app, ip="198.51.100.20")
    login(a, "alice", pw_a)
    login(b, "bob", pw_b)
    tid = a.post("/api/tasks", json={"title": "Alice's secret"}).json()["id"]

    assert b.get("/api/tasks").json() == []
    assert b.patch(f"/api/tasks/{tid}", json={"title": "pwned"}).status_code == 404
    assert b.post(f"/api/tasks/{tid}/toggle").status_code == 404
    assert b.delete(f"/api/tasks/{tid}").status_code == 404
    task = a.get("/api/tasks").json()[0]
    assert task["title"] == "Alice's secret" and task["done"] is False


def test_demo_account_is_read_only(app):
    from app import db
    from app.manage_users import seed_demo
    pw = add_user("demo", role="demo")
    seed_demo(db.get_user_by_name("demo")["id"])
    c = new_client(app)
    login(c, "demo", pw)
    tasks = c.get("/api/tasks").json()
    assert len(tasks) == 10 and sum(t["done"] for t in tasks) == 3
    assert c.get("/api/metrics").status_code == 200
    for method, path, body in WRITES:
        r = call(c, method, path, body, task_id=tasks[0]["id"])
        assert r.status_code == 403, (method, path)
        assert r.json()["detail"] == "Demo account is read-only."
    assert len(c.get("/api/tasks").json()) == 10  # nothing changed


def test_demo_cannot_see_other_users_data(app):
    pw_a, pw_demo = add_user("alice"), add_user("demo", role="demo")
    a, d = new_client(app), new_client(app, ip="198.51.100.30")
    login(a, "alice", pw_a)
    login(d, "demo", pw_demo)
    tid = a.post("/api/tasks", json={"title": "private"}).json()["id"]
    assert d.get("/api/tasks").json() == []
    assert d.patch(f"/api/tasks/{tid}", json={"title": "x"}).status_code == 403


def test_demo_has_a_stricter_rate_limit(app_env, make_app):
    app_env.setenv("DEMO_RATE_LIMIT_PER_MIN", "3")
    app = make_app()
    pw = add_user("demo", role="demo")
    c = new_client(app)
    login(c, "demo", pw)
    codes = [c.get("/api/tasks").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


# ------------------------------------------------------------------ CSRF
def test_csrf_is_required_for_writes(user_client):
    good = user_client.headers.pop("X-CSRF-Token")
    assert user_client.post("/api/tasks", json={"title": "x"}).status_code == 403  # missing
    user_client.headers["X-CSRF-Token"] = "not-the-token"
    assert user_client.post("/api/tasks", json={"title": "x"}).status_code == 403  # wrong
    assert user_client.post("/api/auth/logout").status_code == 403
    user_client.headers["X-CSRF-Token"] = good
    evil = user_client.post("/api/tasks", json={"title": "x"}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403  # right token, foreign origin
    ok = user_client.post("/api/tasks", json={"title": "x"}, headers={"Origin": "https://testserver"})
    assert ok.status_code == 201
    assert user_client.get("/api/tasks").status_code == 200  # reads don't need the token


def test_login_from_a_foreign_origin_is_blocked(app):
    pw = add_user("alice")
    c = new_client(app)
    r = c.post("/api/auth/login", json={"username": "alice", "password": pw}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


# ------------------------------------------------------------------ login throttling
def test_login_limit_per_ip(app_env, make_app):
    app_env.setenv("LOGIN_MAX_FAILURES_PER_IP", "3")
    app = make_app()
    pw = add_user("alice")
    c = new_client(app, ip="203.0.113.50")
    codes = [c.post("/api/auth/login", json={"username": f"guess{i}", "password": "wrong-password-1"}).status_code
             for i in range(3)]
    assert codes == [401, 401, 401]
    # Blocked now, even with the right password, from this IP...
    assert c.post("/api/auth/login", json={"username": "alice", "password": pw}).status_code == 429
    # ...but not from another IP.
    login(new_client(app, ip="203.0.113.51"), "alice", pw)


def test_login_limit_per_username_but_not_for_demo(app_env, make_app):
    app_env.setenv("LOGIN_MAX_FAILURES_PER_USER", "3")
    app = make_app()
    pw_alice, pw_demo = add_user("alice"), add_user("demo", role="demo")
    for i in range(3):  # spread over IPs so only the per-username limit can trigger
        assert new_client(app, ip=f"203.0.113.{60 + i}").post(
            "/api/auth/login", json={"username": "alice", "password": "wrong-password-1"}).status_code == 401
    blocked = new_client(app, ip="203.0.113.70").post("/api/auth/login", json={"username": "alice", "password": pw_alice})
    assert blocked.status_code == 429
    # Same thing for the demo account: no per-username lockout, it still signs in.
    for i in range(4):
        new_client(app, ip=f"203.0.113.{80 + i}").post(
            "/api/auth/login", json={"username": "demo", "password": "wrong-password-1"})
    login(new_client(app, ip="203.0.113.90"), "demo", pw_demo)


# ------------------------------------------------------------------ secrets never leak
def test_password_hash_is_never_returned(app, capsys):
    from app import manage_users
    pw = add_user("alice")
    c = new_client(app)
    bodies = [login(c, "alice", pw).text, c.get("/api/auth/me").text, c.get("/api/tasks").text,
              c.get("/api/info").text]
    assert not any("argon2" in b or "password" in b for b in bodies)
    manage_users.main(["list"])
    out = capsys.readouterr().out
    assert "alice" in out and "argon2" not in out


def test_session_token_is_only_stored_hashed(app):
    from app import db
    pw = add_user("alice")
    c = new_client(app)
    login(c, "alice", pw)
    token = c.cookies.get("__Host-ct_session")
    with db.connect() as conn:
        stored = [r[0] for r in conn.execute("SELECT token_hash FROM sessions")]
    assert token not in stored and len(stored[0]) == 64


def test_passwords_are_argon2id_and_policy_is_enforced(app):
    from app import auth, db
    add_user("alice")
    assert db.get_user_by_name("alice")["password_hash"].startswith("$argon2id$")
    with pytest.raises(ValueError):
        auth.validate_password("short-pw")  # < 12
    with pytest.raises(ValueError):
        auth.validate_password("alice-alice-1", username="alice-alice-1")
    with pytest.raises(ValueError):
        auth.validate_username("Not Valid!")


# ------------------------------------------------------------------ migration
def test_migration_from_single_board_schema_deletes_ownerless_tasks(app_env, tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(path)  # a database as created by the previous version
    conn.executescript("""
        CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, note TEXT,
            priority TEXT NOT NULL, due_date TEXT, color TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO tasks (title, priority, color, created_at, updated_at) VALUES ('old', 'low', 'mint', 'x', 'x');
    """)
    conn.commit()
    conn.close()
    from app import db
    from app.main import create_app
    create_app()
    assert db.schema_version() == 2
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        cols = [r[1] for r in c.execute("PRAGMA table_info(tasks)")]
    assert "user_id" in cols
    create_app()  # running the migration twice is a no-op
    assert db.schema_version() == 2


def test_deleting_a_user_removes_their_tasks_and_sessions(app):
    from app import db
    pw = add_user("alice")
    c = new_client(app)
    login(c, "alice", pw)
    c.post("/api/tasks", json={"title": "x"})
    db.delete_user(db.get_user_by_name("alice")["id"])
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert c.get("/api/tasks").status_code == 401


def test_head_requests_work_for_monitors(app):
    c = new_client(app)
    assert c.head("/health").status_code == 200
    assert c.head("/login").status_code == 200
    r = c.head("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
