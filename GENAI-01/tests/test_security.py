import time

import pytest

from app.core.security import Role, create_access_token, decode_access_token


def test_jwt_round_trip_and_role_claim():
    token = create_access_token("reviewer", Role.FINANCE, "a-secret-key-with-at-least-32-characters", "issuer", 10)
    principal = decode_access_token(token, "a-secret-key-with-at-least-32-characters", "issuer")
    assert principal.subject == "reviewer"
    assert principal.role is Role.FINANCE


def test_tampered_jwt_is_rejected():
    token = create_access_token("reviewer", Role.ADMIN, "a-secret-key-with-at-least-32-characters", "issuer", 10)
    header, payload, signature = token.split(".")
    altered_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    altered = f"{header}.{altered_payload}.{signature}"
    with pytest.raises(ValueError, match="Invalid or expired"):
        decode_access_token(altered, "a-secret-key-with-at-least-32-characters", "issuer")


def test_expired_jwt_is_rejected(monkeypatch):
    import app.core.security as security_module

    token = create_access_token("reviewer", Role.ADMIN, "a-secret-key-with-at-least-32-characters", "issuer", 5)
    real_time = time.time
    monkeypatch.setattr(security_module.time, "time", lambda: real_time() + 1000)
    with pytest.raises(ValueError):
        decode_access_token(token, "a-secret-key-with-at-least-32-characters", "issuer")
