"""Shared fixtures: a fresh app + database per test, users created directly, logged-in clients.

Clients use https://testserver so the Secure session cookie is sent back like in a browser.
Test passwords are throwaway values generated per run; they are not real credentials.
"""
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Environment for one isolated app. Override values with monkeypatch.setenv before make_app()."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "1000")
    monkeypatch.setenv("LOGIN_FAILURE_DELAY", "0")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    return monkeypatch


@pytest.fixture
def make_app(app_env):
    def _make():
        from app.main import create_app
        return create_app()
    return _make


@pytest.fixture
def app(make_app):
    return make_app()


def new_client(app, ip="198.51.100.10"):
    return TestClient(app, base_url="https://testserver", client=(ip, 50000))


def add_user(username, role="user"):
    from app import auth, db
    password = secrets.token_urlsafe(18)  # throwaway, >= 12 characters
    db.create_user(username, role, auth.hash_password(password))
    return password


def login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r


@pytest.fixture
def user_client(app):
    """Signed-in regular user with the CSRF header set, like the real UI."""
    pw = add_user("alice")
    c = new_client(app)
    login(c, "alice", pw)
    return c


@pytest.fixture
def client(user_client):
    return user_client
