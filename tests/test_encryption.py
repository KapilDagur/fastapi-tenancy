"""Tests for :mod:`fastapi_tenancy.utils.encryption`.

Focused on the metadata helpers, which exist to support the
encryption-at-rest invariant: every ``_enc_*`` value in a tenant's metadata is
ciphertext.  ``update_metadata`` only encrypts the keys present in a patch, so
values written before encryption was switched on stay plaintext until
something rewrites them — these helpers are how an operator finds and fixes
that.
"""

from __future__ import annotations

import pytest

from fastapi_tenancy.utils.encryption import TenancyEncryption, _derive_fernet_key

#: Any high-entropy passphrase; HKDF turns it into a real Fernet key. The
#: constructor takes the *derived* key, not the passphrase.
_PASSPHRASE = "a-passphrase-with-at-least-32-characters"


@pytest.fixture
def enc() -> TenancyEncryption:
    """Return an encryption helper with a deterministic key."""
    return TenancyEncryption(_derive_fernet_key(_PASSPHRASE))


@pytest.mark.unit
class TestFindPlaintextEncKeys:
    def test_reports_plaintext_enc_keys(self, enc: TenancyEncryption) -> None:
        bad = enc.find_plaintext_enc_keys(
            {"_enc_api_key": "still-plaintext", "_enc_webhook": "also-plaintext"}
        )
        assert bad == ["_enc_api_key", "_enc_webhook"]

    def test_ignores_already_encrypted_values(self, enc: TenancyEncryption) -> None:
        metadata = {"_enc_api_key": enc.encrypt("secret")}
        assert enc.find_plaintext_enc_keys(metadata) == []

    def test_ignores_keys_without_the_prefix(self, enc: TenancyEncryption) -> None:
        """Only ``_enc_*`` keys are covered by the invariant."""
        assert enc.find_plaintext_enc_keys({"api_key": "plaintext-but-not-claimed"}) == []

    def test_ignores_non_string_values(self, enc: TenancyEncryption) -> None:
        """A nested dict or number cannot be a Fernet token, so it is not a finding."""
        metadata = {"_enc_count": 42, "_enc_nested": {"a": 1}, "_enc_none": None}
        assert enc.find_plaintext_enc_keys(metadata) == []

    def test_empty_metadata_is_clean(self, enc: TenancyEncryption) -> None:
        assert enc.find_plaintext_enc_keys({}) == []

    def test_result_is_sorted_for_stable_reporting(self, enc: TenancyEncryption) -> None:
        """Admin tooling diffs these lists, so ordering must not wobble."""
        metadata = {"_enc_z": "p", "_enc_a": "p", "_enc_m": "p"}
        assert enc.find_plaintext_enc_keys(metadata) == ["_enc_a", "_enc_m", "_enc_z"]


@pytest.mark.unit
class TestEncryptMetadata:
    def test_encrypts_only_prefixed_string_values(self, enc: TenancyEncryption) -> None:
        out = enc.encrypt_metadata(
            {"_enc_api_key": "secret", "plain": "left-alone", "_enc_count": 7}
        )
        assert enc.is_encrypted(out["_enc_api_key"])
        assert enc.decrypt(out["_enc_api_key"]) == "secret"
        assert out["plain"] == "left-alone"
        assert out["_enc_count"] == 7

    def test_is_idempotent(self, enc: TenancyEncryption) -> None:
        """Re-encrypting must not double-wrap — the operation is safe to retry."""
        once = enc.encrypt_metadata({"_enc_api_key": "secret"})
        twice = enc.encrypt_metadata(once)
        assert twice["_enc_api_key"] == once["_enc_api_key"]
        assert enc.decrypt(twice["_enc_api_key"]) == "secret"

    def test_does_not_mutate_the_input(self, enc: TenancyEncryption) -> None:
        original = {"_enc_api_key": "secret"}
        enc.encrypt_metadata(original)
        assert original == {"_enc_api_key": "secret"}

    def test_closes_the_gap_it_exists_for(self, enc: TenancyEncryption) -> None:
        """The audit-then-repair round trip an operator would run."""
        row = {"_enc_api_key": "written-before-encryption-was-enabled"}
        assert enc.find_plaintext_enc_keys(row) == ["_enc_api_key"]

        repaired = enc.encrypt_metadata(row)
        assert enc.find_plaintext_enc_keys(repaired) == []

    def test_empty_metadata_round_trips(self, enc: TenancyEncryption) -> None:
        assert enc.encrypt_metadata({}) == {}
