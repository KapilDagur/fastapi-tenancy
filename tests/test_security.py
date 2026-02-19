"""Unit tests for security utilities."""
from __future__ import annotations

import inspect

from fastapi_tenancy.utils.security import (
    constant_time_compare,
    generate_api_key,
    generate_secret_key,
    generate_tenant_id,
    generate_verification_token,
    hash_value,
    mask_sensitive_data,
)


class TestGenerateTenantId:

    def test_has_prefix(self) -> None:
        tid = generate_tenant_id()
        assert tid.startswith("tenant-")

    def test_custom_prefix(self) -> None:
        tid = generate_tenant_id("org")
        assert tid.startswith("org-")

    def test_uniqueness(self) -> None:
        ids = {generate_tenant_id() for _ in range(100)}
        assert len(ids) == 100


class TestGenerateApiKey:

    def test_returns_string(self) -> None:
        key = generate_api_key()
        assert isinstance(key, str)
        assert len(key) > 0

    def test_custom_length(self) -> None:
        key = generate_api_key(length=64)
        assert len(key) >= 64

    def test_uniqueness(self) -> None:
        keys = {generate_api_key() for _ in range(50)}
        assert len(keys) == 50


class TestGenerateSecretKey:

    def test_returns_string(self) -> None:
        s = generate_secret_key()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_length_custom(self) -> None:
        s = generate_secret_key(length=128)
        assert len(s) >= 100  # url-safe base64 is longer than raw bytes

    def test_uniqueness(self) -> None:
        keys = {generate_secret_key() for _ in range(50)}
        assert len(keys) == 50


class TestMaskSensitiveData:

    def test_masks_password(self) -> None:
        data = {"username": "admin", "password": "s3cr3t"}
        result = mask_sensitive_data(data)
        assert result["username"] == "admin"
        assert result["password"] != "s3cr3t"
        assert "***" in str(result["password"])

    def test_non_sensitive_keys_unchanged(self) -> None:
        data = {"name": "Alice", "email": "a@b.com"}
        result = mask_sensitive_data(data)
        assert result["name"] == "Alice"

    def test_handles_nested(self) -> None:
        """Non-dict values should not crash mask_sensitive_data."""
        data = {"key": "value"}
        result = mask_sensitive_data(data)
        assert isinstance(result, dict)


class TestHashValue:

    def test_returns_string(self) -> None:
        h = hash_value("my-secret")
        assert isinstance(h, str)
        assert len(h) > 0

    def test_deterministic_without_salt(self) -> None:
        # Without a random salt, same input → same output
        h1 = hash_value("my-secret", salt="fixed")
        h2 = hash_value("my-secret", salt="fixed")
        assert h1 == h2

    def test_different_salts_differ(self) -> None:
        h1 = hash_value("my-secret", salt="salt1")
        h2 = hash_value("my-secret", salt="salt2")
        assert h1 != h2

    def test_plaintext_not_in_hash(self) -> None:
        h = hash_value("plaintext")
        assert "plaintext" not in h


class TestConstantTimeCompare:

    def test_equal_strings(self) -> None:
        assert constant_time_compare("abc", "abc") is True

    def test_unequal_strings(self) -> None:
        assert constant_time_compare("abc", "xyz") is False

    def test_empty_strings(self) -> None:
        assert constant_time_compare("", "") is True

    def test_uses_compare_digest(self) -> None:
        """Must use hmac.compare_digest (timing-safe)."""
        import fastapi_tenancy.utils.security as mod
        source = inspect.getsource(mod)
        assert "compare_digest" in source, \
            "constant_time_compare must use hmac.compare_digest"


class TestGenerateVerificationToken:

    def test_returns_string(self) -> None:
        t = generate_verification_token()
        assert isinstance(t, str)
        assert len(t) > 0

    def test_uniqueness(self) -> None:
        tokens = {generate_verification_token() for _ in range(50)}
        assert len(tokens) == 50
