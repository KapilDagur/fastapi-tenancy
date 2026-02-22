"""Unit tests for fastapi_tenancy.utils.validation"""

from __future__ import annotations

import datetime

import pytest

from fastapi_tenancy.utils.validation import (
    assert_safe_database_name,
    assert_safe_schema_name,
    sanitize_identifier,
    validate_database_name,
    validate_email,
    validate_json_serializable,
    validate_schema_name,
    validate_tenant_identifier,
    validate_url,
)


class TestValidateTenantIdentifier:
    @pytest.mark.parametrize(
        "identifier",
        [
            "acme-corp",
            "my-company",
            "abc",
            "ab1",
            "a" * 63,
            "test-123",
            "abcdef",
        ],
    )
    def test_valid_identifiers(self, identifier):
        assert validate_tenant_identifier(identifier) is True

    @pytest.mark.parametrize(
        "identifier",
        [
            "",  # empty
            "ab",  # too short (2 chars)
            "a",  # too short
            "-bad",  # starts with hyphen
            "bad-",  # ends with hyphen
            "ACME",  # uppercase
            "has space",  # space
            "a_b",  # underscore
            "a" * 64,  # too long (64 chars)
            123,  # not a string
            None,  # None
        ],
    )
    def test_invalid_identifiers(self, identifier):
        assert validate_tenant_identifier(identifier) is False

    def test_rejects_over_max_input_len(self):
        # Over 512 char limit
        assert validate_tenant_identifier("a" * 513) is False


class TestValidateSchemaName:
    @pytest.mark.parametrize(
        "name",
        [
            "tenant_acme_corp",
            "_private",
            "t1",
            "a" * 63,
            "abc",
        ],
    )
    def test_valid(self, name):
        assert validate_schema_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "",  # empty
            "1invalid",  # starts with digit
            "Has-Hyphen",  # has hyphen + uppercase
            "a" * 513,  # too long
            None,  # not a string
        ],
    )
    def test_invalid(self, name):
        assert validate_schema_name(name) is False

    def test_database_name_same_rules(self):
        assert validate_database_name("tenant_acme") is True
        assert validate_database_name("1bad") is False


class TestAssertSafeNames:
    def test_assert_schema_valid(self):
        assert_safe_schema_name("tenant_acme")  # no raise

    def test_assert_schema_invalid(self):
        with pytest.raises(ValueError, match="Unsafe schema name"):
            assert_safe_schema_name("'; DROP TABLE; --")

    def test_assert_schema_with_context(self):
        with pytest.raises(ValueError, match="my context"):
            assert_safe_schema_name("bad!", context="my context")

    def test_assert_database_valid(self):
        assert_safe_database_name("tenant_db")  # no raise

    def test_assert_database_invalid(self):
        with pytest.raises(ValueError, match="Unsafe database name"):
            assert_safe_database_name("bad!name")

    def test_assert_database_with_context(self):
        with pytest.raises(ValueError, match="ctx"):
            assert_safe_database_name("BAD", context="ctx")


class TestSanitizeIdentifier:
    def test_hyphen_to_underscore(self):
        assert sanitize_identifier("acme-corp") == "acme_corp"

    def test_dot_to_underscore(self):
        assert sanitize_identifier("my.company") == "my_company"

    def test_uppercase_lowercased(self):
        assert sanitize_identifier("MyCompany") == "mycompany"

    def test_spaces_to_underscore(self):
        assert sanitize_identifier("A B C") == "a_b_c"

    def test_digit_start_gets_prefix(self):
        assert sanitize_identifier("2fast").startswith("t_")

    def test_consecutive_underscores_collapsed(self):
        result = sanitize_identifier("a--b")
        assert "__" not in result

    def test_empty_string_returns_tenant(self):
        assert sanitize_identifier("") == "tenant"

    def test_only_special_chars_returns_tenant(self):
        assert sanitize_identifier("!!!") == "tenant"

    def test_truncates_to_63(self):
        result = sanitize_identifier("a" * 100)
        assert len(result) <= 63


class TestValidateEmail:
    @pytest.mark.parametrize(
        "email",
        ["user@example.com", "user.name+tag@sub.example.co.uk", "test@test.io"],
    )
    def test_valid(self, email):
        assert validate_email(email) is True

    @pytest.mark.parametrize(
        "email",
        ["", "no-at-sign", "@missing-local", "missing-domain@", None],
    )
    def test_invalid(self, email):
        assert validate_email(email) is False


class TestValidateUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com",
            "https://example.com/path?q=1",
            "https://sub.example.com:8080/path",
        ],
    )
    def test_valid(self, url):
        assert validate_url(url) is True

    @pytest.mark.parametrize(
        "url",
        ["", "ftp://example.com", "not-a-url", None],
    )
    def test_invalid(self, url):
        assert validate_url(url) is False


class TestValidateJsonSerializable:
    def test_dict_is_serializable(self):
        assert validate_json_serializable({"key": "val"}) is True

    def test_list_is_serializable(self):
        assert validate_json_serializable([1, 2, 3]) is True

    def test_string_is_serializable(self):
        assert validate_json_serializable("hello") is True

    def test_none_is_serializable(self):
        assert validate_json_serializable(None) is True

    def test_non_serializable(self):
        assert validate_json_serializable(datetime.datetime.now()) is False

    def test_set_not_serializable(self):
        assert validate_json_serializable({1, 2}) is False
