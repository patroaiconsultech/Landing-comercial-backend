"""Negative, offline administrative-gate regression tests; no DB/secrets required."""
from __future__ import annotations
import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app
from dashboard.config import config


def test_disabled_admin_fails_closed_without_breaking_public_chat_status():
    with patch.dict(os.environ, {"PATROAI_DASHBOARD_ENABLED": "false"}):
        with TestClient(app) as client:
            response = client.get("/v1/admin/auth/session")
            assert response.status_code == 503
            assert response.json() == {"detail": "ADMIN_UNAVAILABLE"}
            response = client.post("/v1/admin/auth/password", json={
                "email": "nobody@example.invalid", "password": "not-a-real-password"
            }, headers={"Origin": "https://patroai.com.br"})
            assert response.status_code == 503
            assert response.json() == {"detail": "ADMIN_UNAVAILABLE"}
            assert client.get("/v1/public/chat/status").status_code == 200


def test_disabled_flag_requires_literal_true():
    with patch.dict(os.environ, {"PATROAI_DASHBOARD_ENABLED": "True"}):
        try:
            config()
        except RuntimeError as exc:
            assert str(exc) == "ADMIN_DISABLED"
        else:
            raise AssertionError("Admin unexpectedly enabled")


def test_keys_and_origin_invalid_fail_closed_without_network_access():
    with patch.dict(os.environ, {
        "PATROAI_DASHBOARD_ENABLED": "true",
        "PATROAI_ADMIN_ORIGIN": "https://patroai.com.br/",
        "PATROAI_ADMIN_SESSION_KEY": "00" * 32,
        "PATROAI_ADMIN_TOTP_KEY": "11" * 32,
        "PATROAI_HANDOFF_ENCRYPTION_KEY": "22" * 32,
        "DATABASE_URL": "postgresql://never-connect.invalid/test",
    }):
        try:
            config()
        except RuntimeError as exc:
            assert str(exc) == "ADMIN_ORIGIN_INVALID"
        else:
            raise AssertionError("Trailing slash unexpectedly accepted")
