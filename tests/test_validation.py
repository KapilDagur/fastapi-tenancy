"""Unit tests for utils/validation.py — the DDL injection guard.

These are the most critical tests in the suite: they verify the primary
defence against SQL injection via tenant identifiers.
"""
from __future__ import annotations

import pytest

from fastapi_tenancy.utils.validation import (
    assert_safe_database_name,
    assert_safe_schema_name,
    sanitize_identifier,
    validate_email,
    validate_json_field,
    validate_schema_name,
    validate_tenant_identifier,
    validate_url,
)

# ---------------------------------------------------------------------------
# validate_tenant_identifier
# ---------------------------------------------------------------------------

class TestValidateTenantIdentifier:

    @pytest.mark.parametrize("identifier", [
        "acme-corp",
        "my-company",
        "tenant-123",
        "ab",           # 2 chars — but wait, min is 3 → expect False
        "abc",
        "a1b2c3",
        "company-name-here",
    ])
    def test_valid_identifiers(self, identifier: str) -> None:
        # "ab" is 2 chars — should fail (min 3); fix expectation
        if len(identifier) < 3:
            assert validate_tenant_identifier(identifier) is False
        else:
            assert validate_tenant_identifier(identifier) is True

    @pytest.mark.parametrize("identifier", [
        "",
        "A-Corp",            # uppercase
        "123-corp",          # starts with digit
        "corp-",             # trailing hyphen
        "-corp",             # leading hyphen
        "a",                 # too short
        "ab",                # too short
        "'; DROP TABLE --",  # SQL injection
        "schema'; DROP",
        "a" * 64,            # too long (max 63)
        "corp name",         # space
        "corp/name",         # slash
        "corp.name",         # dot
    ])
    def test_invalid_identifiers(self, identifier: str) -> None:
        assert validate_tenant_identifier(identifier) is False

    def test_none_is_invalid(self) -> None:
        assert validate_tenant_identifier(None) is False  # type: ignore[arg-type]

    def test_max_length_valid(self) -> None:
        # Exactly 63 chars: letter + 61 valid chars + letter
        ident = "a" + "b" * 61 + "c"
        assert len(ident) == 63
        assert validate_tenant_identifier(ident) is True

    def test_over_max_length_invalid(self) -> None:
        ident = "a" + "b" * 62 + "c"  # 64 chars
        assert validate_tenant_identifier(ident) is False


# ---------------------------------------------------------------------------
# validate_schema_name
# ---------------------------------------------------------------------------

class TestValidateSchemaName:

    @pytest.mark.parametrize("name", [
        "tenant_acme",
        "_private",
        "my_schema_123",
        "t",
        "a" * 63,
    ])
    def test_valid_schema_names(self, name: str) -> None:
        assert validate_schema_name(name) is True

    @pytest.mark.parametrize("name", [
        "",
        "Uppercase",
        "has-hyphen",
        "has space",
        "123starts",
        "a" * 64,       # too long
        "'; DROP TABLE",
        None,           # type: ignore[arg-type]
    ])
    def test_invalid_schema_names(self, name: str) -> None:
        assert validate_schema_name(name) is False


# ---------------------------------------------------------------------------
# assert_safe_schema_name / assert_safe_database_name
# ---------------------------------------------------------------------------

class TestAssertSafeIdentifiers:

    def test_safe_schema_passes(self) -> None:
        # Should not raise
        assert_safe_schema_name("tenant_acme")

    def test_unsafe_schema_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsafe or invalid"):
            assert_safe_schema_name("'; DROP TABLE --")

    def test_safe_database_passes(self) -> None:
        assert_safe_database_name("tenant_acme_db")

    def test_unsafe_database_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsafe or invalid"):
            assert_safe_database_name("db'; DROP DATABASE")

    def test_context_included_in_message(self) -> None:
        with pytest.raises(ValueError, match="my_context"):
            assert_safe_schema_name("Bad-Name", context="my_context")


# ---------------------------------------------------------------------------
# sanitize_identifier
# ---------------------------------------------------------------------------

class TestSanitizeIdentifier:

    @pytest.mark.parametrize("raw, expected", [
        ("Acme-Corp!", "acme_corp"),
        ("123-company", "t_123_company"),
        ("my..company", "my_company"),
        ("__leading", "leading"),
        ("UPPER", "upper"),
        ("", "tenant"),
        ("a" * 100, "a" * 63),    # truncated to 63
        ("my company name", "my_company_name"),
    ])
    def test_sanitize(self, raw: str, expected: str) -> None:
        result = sanitize_identifier(raw)
        assert result == expected
        # Result must always be a valid schema name
        assert validate_schema_name(result), f"sanitized {raw!r} → {result!r} is not a valid schema name"


# ---------------------------------------------------------------------------
# Email / URL / JSON validators
# ---------------------------------------------------------------------------

class TestEmailValidator:

    @pytest.mark.parametrize("email", ["user@example.com", "a+b@foo.org", "x@y.co.uk"])
    def test_valid(self, email: str) -> None:
        assert validate_email(email) is True

    @pytest.mark.parametrize("email", ["", "notanemail", "@missing.com", "a@", None])
    def test_invalid(self, email: str) -> None:
        assert validate_email(email) is False  # type: ignore[arg-type]


class TestUrlValidator:

    @pytest.mark.parametrize("url", [
        "https://example.com",
        "http://localhost:8080/path",
        "https://api.example.com/v1",
    ])
    def test_valid(self, url: str) -> None:
        assert validate_url(url) is True

    @pytest.mark.parametrize("url", ["", "not-a-url", "ftp://bad.com", None])
    def test_invalid(self, url: str) -> None:
        assert validate_url(url) is False  # type: ignore[arg-type]


class TestJsonFieldValidator:

    def test_valid_dict(self) -> None:
        assert validate_json_field({"key": "value", "count": 42}) is True

    def test_valid_list(self) -> None:
        assert validate_json_field([1, 2, 3]) is True

    def test_valid_primitives(self) -> None:
        for v in [None, True, 42, "hello", 3.14]:
            assert validate_json_field(v) is True

    def test_invalid_lambda(self) -> None:
        assert validate_json_field(lambda x: x) is False

    def test_invalid_datetime(self) -> None:
        from datetime import datetime
        # Raw datetime is not JSON serialisable by default
        assert validate_json_field(datetime.now()) is False
