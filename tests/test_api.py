"""API tests: every test gets a fresh app with its own temporary SQLite database."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def _make(rate_limit: int = 1000):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
        monkeypatch.setenv("RATE_LIMIT_PER_MIN", str(rate_limit))
        from app.main import create_app
        return TestClient(create_app())
    return _make


@pytest.fixture
def client(make_client):
    return make_client()


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["database"] == "ok"


def test_create_and_list_task(client):
    r = client.post("/api/tasks", json={"title": "  Deploy to Azure  ", "priority": "high", "due_date": "2026-10-05"})
    assert r.status_code == 201
    task = r.json()
    assert task["title"] == "Deploy to Azure"  # trimmed
    assert task["priority"] == "high"
    assert task["due_date"] == "2026-10-05"
    assert task["done"] is False
    assert task["color"] in {"peach", "mint", "lavender", "butter", "sky", "blush"}

    listed = client.get("/api/tasks").json()
    assert [t["id"] for t in listed] == [task["id"]]


@pytest.mark.parametrize("payload", [
    {"title": ""},
    {"title": "   "},
    {"title": "x" * 121},
    {"title": "ok", "priority": "urgent"},
    {"title": "ok", "due_date": "not-a-date"},
    {"title": "ok", "note": "n" * 501},
    {"title": "ok", "is_admin": True},  # unknown fields are rejected
])
def test_create_validation(client, payload):
    assert client.post("/api/tasks", json=payload).status_code == 422
    assert client.get("/api/tasks").json() == []


def test_toggle_done(client):
    tid = client.post("/api/tasks", json={"title": "Toggle me"}).json()["id"]
    assert client.post(f"/api/tasks/{tid}/toggle").json()["done"] is True
    assert client.post(f"/api/tasks/{tid}/toggle").json()["done"] is False


def test_edit_task(client):
    tid = client.post("/api/tasks", json={"title": "Old", "due_date": "2026-10-05"}).json()["id"]
    r = client.patch(f"/api/tasks/{tid}", json={"title": "New", "priority": "low", "note": "hi", "due_date": None})
    assert r.status_code == 200
    body = r.json()
    assert (body["title"], body["priority"], body["note"], body["due_date"]) == ("New", "low", "hi", None)
    # title can't be nulled or blanked
    assert client.patch(f"/api/tasks/{tid}", json={"title": None}).status_code == 422
    assert client.patch(f"/api/tasks/{tid}", json={"title": " "}).status_code == 422


def test_delete_and_not_found(client):
    tid = client.post("/api/tasks", json={"title": "Bye"}).json()["id"]
    assert client.delete(f"/api/tasks/{tid}").status_code == 204
    assert client.delete(f"/api/tasks/{tid}").status_code == 404
    assert client.patch(f"/api/tasks/{tid}", json={"title": "x"}).status_code == 404
    assert client.post(f"/api/tasks/{tid}/toggle").status_code == 404


def test_html_is_stored_as_plain_text_and_security_headers(client):
    evil = '<img src=x onerror="alert(1)">'
    r = client.post("/api/tasks", json={"title": evil})
    assert r.json()["title"] == evil  # stored verbatim; the UI renders it with textContent
    assert r.headers["content-type"].startswith("application/json")
    page = client.get("/")
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["x-frame-options"] == "DENY"


def test_sample_tasks(client):
    r = client.post("/api/tasks/sample")
    assert r.status_code == 201
    assert len(r.json()) == 10
    assert len(client.get("/api/tasks").json()) == 10
    assert {t["priority"] for t in r.json()} == {"low", "medium", "high"}


def test_metrics_and_info(client):
    m = client.get("/api/metrics").json()
    assert 0 <= m["cpu_percent"] <= 100
    assert 0 < m["memory"]["percent"] <= 100
    assert m["disk"]["total"] > 0
    assert m["uptime_seconds"] > 0
    i = client.get("/api/info").json()
    for key in ("os", "hostname", "version", "region", "public_ip", "deployed_at", "container"):
        assert key in i
    assert "subscriptionId" not in str(i)


def test_info_reports_where_azure_metadata_came_from(client, monkeypatch):
    from app import system
    monkeypatch.setattr(system, "_imds", lambda: None)  # app has no egress: IMDS unreachable

    local = client.get("/api/info").json()
    assert local["metadata_source"] is None and local["on_azure"] is False

    for key, value in {"REGION": "eastasia", "VM_SIZE": "Standard_B2pts_v2",
                       "VM_NAME": "vm-cloudtasks", "PUBLIC_IP": "203.0.113.10"}.items():
        monkeypatch.setenv(f"AZURE_{key}", value)
    deployed = client.get("/api/info").json()
    assert deployed["metadata_source"] == "deploy"
    assert (deployed["region"], deployed["public_ip"]) == ("eastasia", "203.0.113.10")

    monkeypatch.setattr(system, "_imds", lambda: {"region": "eastasia", "public_ip": None})
    assert client.get("/api/info").json()["metadata_source"] == "imds"


def test_rate_limit_trusts_proxy_headers_only_from_trusted_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "proxy.db"))
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "3")
    monkeypatch.setenv("TRUSTED_PROXIES", "172.28.0.10/32")
    from app.main import create_app

    # Untrusted peer (e.g. someone hitting the app directly) rotates spoofed headers: ignored,
    # all requests count against the peer's own IP, so the limit still applies.
    attacker = TestClient(create_app(), client=("203.0.113.7", 40000))
    codes = [
        attacker.get("/api/tasks", headers={"X-Real-IP": f"10.9.9.{i}", "X-Forwarded-For": f"10.8.8.{i}"}).status_code
        for i in range(5)
    ]
    assert codes == [200, 200, 200, 429, 429]

    # Trusted proxy (nginx's fixed address): the forwarded visitor IP is honoured, so two
    # visitors behind the same proxy get separate budgets...
    nginx = TestClient(create_app(), client=("172.28.0.10", 40000))
    alice = [nginx.get("/api/tasks", headers={"X-Real-IP": "198.51.100.1"}).status_code for _ in range(4)]
    bob = nginx.get("/api/tasks", headers={"X-Real-IP": "198.51.100.2"}).status_code
    assert alice == [200, 200, 200, 429]
    assert bob == 200
    # ...and without X-Real-IP the right-most X-Forwarded-For entry (the one nginx added) is used.
    carol = [nginx.get("/api/tasks", headers={"X-Forwarded-For": "6.6.6.6, 198.51.100.3"}).status_code for _ in range(4)]
    assert carol == [200, 200, 200, 429]


def test_rate_limit(make_client):
    client = make_client(rate_limit=5)
    codes = [client.get("/api/tasks").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5
    assert codes[5:] == [429, 429]
    assert "retry-after" in client.get("/api/tasks").headers
    assert client.get("/health").status_code == 200  # /health is not rate limited
