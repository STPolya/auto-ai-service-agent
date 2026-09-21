"""Shared constant-time credential comparison; never logs credential values."""

import secrets


def keys_match(candidate: str, expected: str) -> bool:
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
