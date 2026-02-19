"""Validation utilities for tenant data — production-grade DDL guard layer.

Every identifier that touches a database DDL statement (CREATE SCHEMA,
CREATE DATABASE, SET search_path …) MUST pass through these validators
before interpolation.  They are the primary defence against SQL injection
via tenant identifiers.
"""
from __future__ import annotations

import json
import re
from typing import Any

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------
_TENANT_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9\-]{1,61}[a-z0-9]$")
_PG_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_URL_RE = re.compile(r"^https?://[a-zA-Z0-9.\-]+(:[0-9]{1,5})?(/.*)?$")


_MAX_IDENTIFIER_INPUT = 512  # hard cap before regex to prevent ReDoS on huge inputs

def validate_tenant_identifier(identifier: str) -> bool:
    """Validate a tenant slug — lowercase, letters/digits/hyphens, 3-63 chars.

    Performs an explicit length check *before* the regex to prevent potential
    ReDoS on pathologically large inputs.  Inputs over 512 bytes are rejected
    immediately without running the pattern.
    """
    if not identifier or not isinstance(identifier, str):
        return False
    # Hard cap before regex — prevents ReDoS on 50KB+ adversarial input
    if len(identifier) > _MAX_IDENTIFIER_INPUT:
        return False
    return bool(_TENANT_IDENTIFIER_RE.match(identifier))


def validate_schema_name(schema_name: str) -> bool:
    """Validate a PostgreSQL schema name — letters/digits/underscores, 1-63 chars.

    Explicit length cap before regex prevents ReDoS on adversarial input.
    """
    if not schema_name or not isinstance(schema_name, str):
        return False
    if len(schema_name) > _MAX_IDENTIFIER_INPUT:
        return False
    return bool(_PG_IDENTIFIER_RE.match(schema_name))


def validate_database_name(database_name: str) -> bool:
    """Validate a PostgreSQL database name (same rules as schema names)."""
    return validate_schema_name(database_name)


def assert_safe_schema_name(schema_name: str, context: str = "") -> None:
    """Raise ValueError immediately if schema_name is not a safe PG identifier.

    Call this BEFORE any DDL that interpolates a schema name.
    """
    if not validate_schema_name(schema_name):
        ctx = f" ({context})" if context else ""
        raise ValueError(
            f"Unsafe or invalid PostgreSQL schema name{ctx}: {schema_name!r}. "
            "Only lowercase letters, digits, and underscores are allowed."
        )


def assert_safe_database_name(database_name: str, context: str = "") -> None:
    """Raise ValueError immediately if database_name is not a safe PG identifier.

    Call this BEFORE any DDL that interpolates a database name.
    """
    if not validate_database_name(database_name):
        ctx = f" ({context})" if context else ""
        raise ValueError(
            f"Unsafe or invalid PostgreSQL database name{ctx}: {database_name!r}. "
            "Only lowercase letters, digits, and underscores are allowed."
        )


def sanitize_identifier(identifier: str) -> str:
    """Sanitize an arbitrary string into a valid PostgreSQL identifier."""
    sanitized = identifier.lower().replace("-", "_")
    sanitized = re.sub(r"[^a-z0-9_]", "_", sanitized)
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    if sanitized and not sanitized[0].isalpha():
        sanitized = f"t_{sanitized}"
    return (sanitized or "tenant")[:63]


def validate_email(email: str) -> bool:
    """Return True if email has a valid format."""
    if not email or not isinstance(email, str):
        return False
    return bool(_EMAIL_RE.match(email))


def validate_url(url: str) -> bool:
    """Return True if url is a valid http/https URL."""
    if not url or not isinstance(url, str):
        return False
    return bool(_URL_RE.match(url))


def validate_json_field(value: Any) -> bool:
    """Return True if value is JSON-serialisable."""
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


__all__ = [
    "assert_safe_database_name",
    "assert_safe_schema_name",
    "sanitize_identifier",
    "validate_database_name",
    "validate_email",
    "validate_json_field",
    "validate_schema_name",
    "validate_tenant_identifier",
    "validate_url",
]
