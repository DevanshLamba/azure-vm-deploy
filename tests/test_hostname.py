"""Host card: the hostname is the VM name (IMDS -> HOST_NAME -> socket), container ID shown separately.
The Azure metadata call is mocked at the HTTP layer, so the real request code is exercised."""
import io
import json
import socket
import urllib.request

import pytest

from app import system

IMDS_BODY = {"compute": {"name": "vm-cloudtasks", "location": "eastasia", "vmSize": "Standard_B2pts_v2"},
             "network": {"interface": [{"ipv4": {"ipAddress": [{"publicIpAddress": ""}]}}]}}


class FakeOpener:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def open(self, req, timeout=None):
        self.calls.append((req.full_url, req.get_header("Metadata"), timeout))
        if self.fail:
            raise OSError("Network is unreachable")
        return io.BytesIO(json.dumps(IMDS_BODY).encode())


@pytest.fixture
def opener(monkeypatch):
    def make(fail=False):
        fake = FakeOpener(fail)
        monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: fake)
        # Fresh cache for every test.
        monkeypatch.setattr(system, "_imds_cache", None)
        monkeypatch.setattr(system, "_imds_checked_at", 0.0)
        for var in ("HOST_NAME", "CONTAINER_NAME", "AZURE_VM_NAME"):
            monkeypatch.delenv(var, raising=False)
        return fake
    return make


def test_hostname_from_imds_with_header_timeout_and_cache(opener):
    fake = opener()
    assert system.info()["hostname"] == "vm-cloudtasks"
    url, header, timeout = fake.calls[0]
    assert url.startswith("http://169.254.169.254/metadata/instance")
    assert header == "true" and timeout == 1.0
    system.info()
    assert len(fake.calls) == 1  # cached: IMDS is asked once


def test_hostname_falls_back_to_host_name_env(opener, monkeypatch):
    fake = opener(fail=True)  # no route to IMDS (the app container has no internet access)
    monkeypatch.setenv("HOST_NAME", "vm-cloudtasks")
    assert system.info()["hostname"] == "vm-cloudtasks"
    assert len(fake.calls) == 1


def test_hostname_last_resort_and_container_id(opener, monkeypatch):
    opener(fail=True)
    info = system.info()
    assert info["hostname"] == socket.gethostname()
    assert info["container_id"] is None  # not in a container
    monkeypatch.setenv("CONTAINER_NAME", "cloudtasks-app")
    info = system.info()
    assert info["container"] == "cloudtasks-app"
    assert info["container_id"] == socket.gethostname()  # Docker sets hostname = short container ID
