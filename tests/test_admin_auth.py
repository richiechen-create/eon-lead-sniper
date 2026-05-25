"""Acceptance criteria #14, #15 (partial), #17, #18.

Covers:
  - /admin/* is gated by auth (401 / 303 redirect)
  - login with correct creds sets a session cookie
  - logout clears the session
  - /run/* requires X-Internal-Key
  - cron-style direct invocation keeps working regardless of UI auth state (AC #18)
"""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Rep, TargetingProfile


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "admin_test.sqlite"
    url = f"sqlite:///{db_file}"
    eng = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(eng)
    SessionLocal = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)

    # Patch the global engine + session factory BEFORE app import side-effects.
    import app.db as _db
    monkeypatch.setattr(_db, "engine", eng)
    monkeypatch.setattr(_db, "SessionLocal", SessionLocal)

    # Ensure auth env vars are deterministic for the test.
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("INTERNAL_API_KEY", "cron-token")
    # Force settings cache invalidation.
    from app.config import get_settings
    get_settings.cache_clear()

    from app.api.main import app
    return TestClient(app, follow_redirects=False)


def test_admin_root_redirects_to_login_when_unauth(client):
    resp = client.get("/admin/", headers={"Accept": "text/html"})
    assert resp.status_code == 303
    assert "/admin/login" in resp.headers["location"]


def test_admin_no_slash_redirects_to_slash(client):
    # FastAPI auto-redirects /admin -> /admin/ before reaching the auth check.
    resp = client.get("/admin", headers={"Accept": "text/html"})
    assert resp.status_code == 307
    assert resp.headers["location"].endswith("/admin/")


def test_admin_leads_returns_401_for_json_clients(client):
    resp = client.get("/admin/leads", headers={"Accept": "application/json"})
    assert resp.status_code == 401


def test_login_with_correct_creds_sets_cookie(client):
    resp = client.post(
        "/admin/login",
        data={"username": "admin", "password": "secret", "next": "/admin"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin"
    assert "lead_engine_session" in resp.cookies


def test_login_with_wrong_password_returns_form_with_error(client):
    resp = client.post(
        "/admin/login",
        data={"username": "admin", "password": "WRONG", "next": "/admin"},
    )
    assert resp.status_code == 200
    assert "Invalid username or password" in resp.text


def test_logout_clears_session(client):
    client.post("/admin/login", data={"username": "admin", "password": "secret"})
    assert client.get("/admin/", headers={"Accept": "text/html"}).status_code == 200
    client.post("/admin/logout")
    resp = client.get("/admin/", headers={"Accept": "text/html"})
    assert resp.status_code == 303
    assert "/admin/login" in resp.headers["location"]


def test_dashboard_accessible_after_login(client):
    client.post("/admin/login", data={"username": "admin", "password": "secret"})
    resp = client.get("/admin/", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Dashboard" in resp.text


def test_login_does_not_redirect_to_offsite_next(client):
    resp = client.post(
        "/admin/login",
        data={"username": "admin", "password": "secret", "next": "https://evil.example.com"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin"


def test_run_enrichment_endpoint_requires_internal_key(client):
    resp = client.post("/run/enrichment")
    assert resp.status_code == 401
    # With the correct header it tries to run (will fail without Apollo key, but auth passed).
    resp = client.post(
        "/run/enrichment",
        headers={"X-Internal-Key": "cron-token"},
    )
    # 200 (ran the no-op since no companies seeded) — the point is auth passed.
    assert resp.status_code in (200, 500)
    assert resp.status_code != 401


def test_run_digest_endpoint_requires_internal_key(client):
    resp = client.post("/run/digest")
    assert resp.status_code == 401


def test_cron_works_regardless_of_ui_auth_state(client):
    """AC #18: cron pings keep working whether or not the operator is logged into the UI."""
    resp1 = client.post("/run/digest", headers={"X-Internal-Key": "cron-token"})
    assert resp1.status_code != 401

    client.post("/admin/login", data={"username": "admin", "password": "secret"})
    resp2 = client.post("/run/digest", headers={"X-Internal-Key": "cron-token"})
    assert resp2.status_code != 401
