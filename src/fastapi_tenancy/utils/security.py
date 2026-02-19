"""Security utilities for tenant operations - Production-grade.

This module provides security-related utilities:
- Secure ID generation
- API key generation
- Data masking
- Password hashing helpers
- Token generation
"""

import hashlib
import secrets
import string
from typing import Any


def generate_tenant_id(prefix: str = "tenant") -> str:
    """Generate a secure random tenant ID.

    Uses cryptographically secure random generation.

    Args:
        prefix: Prefix for the ID (default: 'tenant')

    Returns:
        Generated tenant ID

    Example:
        ```python
        tenant_id = generate_tenant_id()
        # Returns: "tenant-A1b2C3d4E5f6"

        custom_id = generate_tenant_id("org")
        # Returns: "org-X9y8Z7w6V5u4"
        ```
    """
    # Generate 12 characters of URL-safe randomness
    random_part = secrets.token_urlsafe(12)[:12]
    return f"{prefix}-{random_part}"


def generate_api_key(length: int = 32) -> str:
    """Generate a secure API key.

    Args:
        length: Length of the key (default: 32)

    Returns:
        Generated API key

    Example:
        ```python
        api_key = generate_api_key()
        # Returns: "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"

        long_key = generate_api_key(64)
        # Returns 64-character key
        ```
    """
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_secret_key(length: int = 64) -> str:
    """Generate a secure secret key for JWT or encryption.

    Args:
        length: Length of the key in bytes (default: 64)

    Returns:
        Hex-encoded secret key

    Example:
        ```python
        secret = generate_secret_key()
        # Returns: Long hex string suitable for JWT_SECRET
        ```
    """
    return secrets.token_hex(length)


def mask_sensitive_data(
    data: dict[str, Any],
    sensitive_keys: list[str] | None = None,
    mask_char: str = "*",
) -> dict[str, Any]:
    """Mask sensitive data in dictionary.

    Args:
        data: Dictionary to mask
        sensitive_keys: List of keys to mask (default: common sensitive fields)
        mask_char: Character to use for masking (default: '*')

    Returns:
        Dictionary with masked values

    Example:
        ```python
        data = {
            "username": "john",
            "password": "secret123",
            "api_key": "abc123xyz",
            "email": "john@example.com"
        }

        masked = mask_sensitive_data(data)
        # Returns: {
        #     "username": "john",
        #     "password": "***MASKED***",
        #     "api_key": "***MASKED***",
        #     "email": "john@example.com"
        # }
        ```
    """
    if sensitive_keys is None:
        sensitive_keys = [
            "password",
            "secret",
            "token",
            "api_key",
            "apikey",
            "database_url",
            "connection_string",
            "private_key",
            "access_token",
            "refresh_token",
        ]

    masked = data.copy()

    for key in masked:
        # Check if key contains any sensitive keyword
        key_lower = key.lower()
        if any(sensitive in key_lower for sensitive in sensitive_keys):
            if isinstance(masked[key], str):
                # Mask the value
                masked[key] = f"{mask_char * 3}MASKED{mask_char * 3}"
            else:
                masked[key] = "***MASKED***"

    return masked


def hash_value(value: str, salt: str | None = None) -> str:
    """Hash a value using SHA-256.

    Args:
        value: Value to hash
        salt: Optional salt to add

    Returns:
        Hex-encoded hash

    Example:
        ```python
        hashed = hash_value("my-secret-value")
        # Returns: SHA-256 hash

        salted = hash_value("value", salt="random-salt")
        # Returns: SHA-256 hash of value + salt
        ```
    """
    if salt:
        value = f"{value}{salt}"

    return hashlib.sha256(value.encode()).hexdigest()


def generate_verification_token(length: int = 32) -> str:
    """Generate a verification token for email/phone verification.

    Args:
        length: Length of the token (default: 32)

    Returns:
        URL-safe verification token

    Example:
        ```python
        token = generate_verification_token()
        # Use in email: https://app.com/verify?token={token}
        ```
    """
    return secrets.token_urlsafe(length)


def constant_time_compare(val1: str, val2: str) -> bool:
    """Compare two strings in constant time.

    Prevents timing attacks when comparing secrets.

    Args:
        val1: First value
        val2: Second value

    Returns:
        True if values are equal

    Example:
        ```python
        # Safe comparison of API keys
        if constant_time_compare(provided_key, stored_key):
            # API key is valid
            ...
        ```
    """
    return secrets.compare_digest(val1, val2)


__all__ = [
    "constant_time_compare",
    "generate_api_key",
    "generate_secret_key",
    "generate_tenant_id",
    "generate_verification_token",
    "hash_value",
    "mask_sensitive_data",
]
