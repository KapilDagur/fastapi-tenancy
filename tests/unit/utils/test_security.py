"""Unit tests for fastapi_tenancy.utils.security"""

from __future__ import annotations

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
    def test_default_prefix(self):
        tid = generate_tenant_id()
        assert tid.startswith("tenant-")

    def test_custom_prefix(self):
        tid = generate_tenant_id("org")
        assert tid.startswith("org-")

    def test_unique(self):
        ids = {generate_tenant_id() for _ in range(100)}
        assert len(ids) == 100


class TestGenerateApiKey:
    def test_default_length(self):
        key = generate_api_key()
        assert len(key) == 32

    def test_custom_length(self):
        key = generate_api_key(64)
        assert len(key) == 64

    def test_alphanumeric_only(self):
        key = generate_api_key(200)
        assert key.isalnum()

    def test_unique(self):
        keys = {generate_api_key() for _ in range(50)}
        assert len(keys) == 50


class TestGenerateSecretKey:
    def test_default(self):
        key = generate_secret_key()
        assert len(key) == 128

    def test_custom_length(self):
        key = generate_secret_key(32)
        assert len(key) == 64

    def test_is_hex(self):
        key = generate_secret_key()
        int(key, 16)  # should not raise


class TestGenerateVerificationToken:
    def test_returns_string(self):
        token = generate_verification_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_url_safe(self):
        for _ in range(20):
            token = generate_verification_token()
            assert "+" not in token
            assert "/" not in token

    def test_unique(self):
        tokens = {generate_verification_token() for _ in range(50)}
        assert len(tokens) == 50


class TestConstantTimeCompare:
    def test_equal_strings(self):
        assert constant_time_compare("abc", "abc") is True

    def test_different_strings(self):
        assert constant_time_compare("abc", "def") is False

    def test_empty_strings(self):
        assert constant_time_compare("", "") is True

    def test_different_lengths(self):
        assert constant_time_compare("ab", "abc") is False


class TestHashValue:
    def test_returns_hex_string(self):
        result = hash_value("my-key")
        assert len(result) == 64
        int(result, 16)

    def test_with_salt(self):
        r1 = hash_value("my-key", salt="salt1")
        r2 = hash_value("my-key", salt="salt2")
        r3 = hash_value("my-key")
        assert r1 != r2
        assert r1 != r3

    def test_deterministic(self):
        assert hash_value("abc") == hash_value("abc")

    def test_different_inputs(self):
        assert hash_value("abc") != hash_value("def")


class TestMaskSensitiveData:
    def test_masks_password(self):
        result = mask_sensitive_data({"username": "alice", "password": "s3cr3t"})
        assert result["password"] == "***MASKED***"
        assert result["username"] == "alice"

    def test_masks_secret(self):
        result = mask_sensitive_data({"api_key": "mykey123"})
        assert result["api_key"] == "***MASKED***"

    def test_masks_database_url(self):
        result = mask_sensitive_data({"database_url": "postgresql://user:pass@host/db"})
        assert result["database_url"] == "***MASKED***"

    def test_case_insensitive(self):
        result = mask_sensitive_data({"PASSWORD": "secret"})
        assert result["PASSWORD"] == "***MASKED***"

    def test_custom_sensitive_keys(self):
        result = mask_sensitive_data(
            {"my_token": "value", "name": "alice"},
            sensitive_keys=["token"],
        )
        assert result["my_token"] == "***MASKED***"
        assert result["name"] == "alice"

    def test_custom_mask(self):
        result = mask_sensitive_data({"password": "secret"}, mask="[REDACTED]")
        assert result["password"] == "[REDACTED]"

    def test_does_not_mutate_original(self):
        original = {"password": "secret", "name": "alice"}
        mask_sensitive_data(original)
        assert original["password"] == "secret"

    def test_non_string_value_also_masked(self):
        result = mask_sensitive_data({"password": 12345})
        assert result["password"] == "***MASKED***"

    def test_all_default_sensitive_keys(self):
        data = {
            "password": "x",
            "secret": "x",
            "token": "x",
            "api_key": "x",
            "apikey": "x",
            "database_url": "x",
            "connection_string": "x",
            "private_key": "x",
            "access_token": "x",
            "refresh_token": "x",
            "encryption_key": "x",
            "jwt_secret": "x",
        }
        result = mask_sensitive_data(data)
        for k in data:
            assert result[k] == "***MASKED***", f"{k} not masked"
