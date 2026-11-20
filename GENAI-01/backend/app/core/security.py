"""Small, auditable HS256 JWT implementation and role authorization primitives."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Role(StrEnum):
    ADMIN = "admin"
    FINANCE = "finance"
    ENGINEERING = "engineering"
    GUEST = "guest"


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    role: Role


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_access_token(
    subject: str,
    role: Role,
    secret: str,
    issuer: str,
    expiry_minutes: int,
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": subject,
        "role": role.value,
        "iss": issuer,
        "iat": now,
        "exp": now + expiry_minutes * 60,
        "jti": secrets.token_urlsafe(12),
    }
    encoded_header = _b64encode(json.dumps(header, separators=(",", ":")).encode())
    encoded_payload = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_b64encode(signature)}"


def decode_access_token(token: str, secret: str, issuer: str) -> Principal:
    try:
        header_part, payload_part, signature_part = token.split(".")
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        supplied = _b64decode(signature_part)
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("Invalid token signature")
        header = json.loads(_b64decode(header_part))
        claims: dict[str, Any] = json.loads(_b64decode(payload_part))
        if header.get("alg") != "HS256" or header.get("typ") != "JWT":
            raise ValueError("Unsupported JWT header")
        if claims.get("iss") != issuer:
            raise ValueError("Invalid token issuer")
        if int(claims.get("exp", 0)) <= int(time.time()):
            raise ValueError("Token expired")
        if not claims.get("sub"):
            raise ValueError("Token subject missing")
        return Principal(subject=str(claims["sub"]), role=Role(claims["role"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid or expired access token") from exc
