# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | ✅ Current |
| 0.1.x   | ⚠ Security fixes only |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Email **security@example.com** with:

1. A description of the vulnerability and its impact
2. Steps to reproduce
3. Affected versions
4. Any proposed mitigations

We will acknowledge your report within **48 hours** and aim to release a patch within **7 days** for critical issues.

## Security model

### SQL injection

All schema names, database names, and table prefixes are validated by `assert_safe_schema_name()` / `assert_safe_database_name()` before use in any DDL statement. Identifiers are also double-quoted. Session variables are always set via bind parameters.

### Context isolation

`TenantContext` uses `contextvars.ContextVar`. Each async task has its own isolated copy. Setting the context in one coroutine does not affect any other concurrently running coroutine.

### Timing attacks

`constant_time_compare()` uses `hmac.compare_digest` for any secret comparison.

### Sensitive data

Passwords and API keys in configuration objects are masked by `mask_sensitive_data()` before logging.
