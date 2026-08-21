"""Auth, health and CORS-origin parsing for the harness's own HTTP surface.

The harness has no notion of platform users; the bearer secret is the only thing standing
between the network and every session on disk. These tests exercise that gate directly,
without the backend proxy in front of it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from harness.ui.app import _assert_auth_configured, _cors_origins, app

from harness.ui import app as app_module


def test_rejects_missing_bearer(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "the-secret")
    client = TestClient(app)
    response = client.get("/api/status")
    assert response.status_code == 401


def test_rejects_wrong_bearer(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "the-secret")
    client = TestClient(app)
    response = client.get("/api/status", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_accepts_correct_bearer(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "the-secret")
    client = TestClient(app)
    response = client.get("/api/status", headers={"Authorization": "Bearer the-secret"})
    assert response.status_code not in (401, 403, 503)


def test_healthz_is_open(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_unconfigured_secret_is_503(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERNAL_API_SECRET", "the-secret")
    # Entering the TestClient as a context manager runs the real startup hook, which
    # would otherwise read the repo's real artifacts dir and adopt whatever session
    # is on disk there, leaking it into module globals for every test that runs after.
    monkeypatch.setattr(app_module, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setattr(app_module, "OPEN", tmp_path / ".open-session")
    with TestClient(app) as client:
        # The middleware reads the env var per request, so this is visible without a restart.
        monkeypatch.delenv("INTERNAL_API_SECRET")
        response = client.get("/api/status")
    assert response.status_code == 503


def test_auth_disabled_escape_hatch(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    monkeypatch.setenv("HARNESS_AUTH_DISABLED", "1")
    client = TestClient(app)
    response = client.get("/api/status")
    assert response.status_code not in (401, 503)


def test_startup_guard_raises_without_secret(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    monkeypatch.delenv("HARNESS_AUTH_DISABLED", raising=False)
    with pytest.raises(RuntimeError):
        _assert_auth_configured()

    monkeypatch.setenv("INTERNAL_API_SECRET", "the-secret")
    _assert_auth_configured()

    monkeypatch.delenv("INTERNAL_API_SECRET")
    monkeypatch.setenv("HARNESS_AUTH_DISABLED", "1")
    _assert_auth_configured()


def test_cors_origins_parsing(monkeypatch):
    monkeypatch.delenv("HARNESS_CORS_ORIGINS", raising=False)
    assert _cors_origins() == []

    monkeypatch.setenv("HARNESS_CORS_ORIGINS", "http://a,http://b")
    assert _cors_origins() == ["http://a", "http://b"]

    monkeypatch.setenv("HARNESS_CORS_ORIGINS", " http://a , http://b ")
    assert _cors_origins() == ["http://a", "http://b"]
