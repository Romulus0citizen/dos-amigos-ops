from collections.abc import Mapping, Sequence
from typing import Any

SENSITIVE_FRAGMENTS = (
    "api_login",
    "apilogin",
    "authorization",
    "cookie",
    "credential",
    "license_key",
    "licensekey",
    "password",
    "secret",
    "token",
)


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(fragment in normalized for fragment in SENSITIVE_FRAGMENTS)


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "***REDACTED***" if is_sensitive_key(str(key)) else redact(item)
            for key, item in value.items()
        }

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(item) for item in value]

    return value
