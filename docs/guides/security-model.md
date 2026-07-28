# Security model

This page describes what `fastapi-tenancy` protects against, what it does
**not** protect against, and the responsibilities the library leaves to the
application. Read this before deploying — many of the strongest guarantees
the library provides only hold when paired with correct application
configuration.

---

## What this library protects against

### Cross-tenant data access at the database boundary

Each isolation strategy enforces a hard boundary that the application cannot
accidentally cross while using the standard session:

| Strategy | Mechanism | Guarantee |
| --- | --- | --- |
| **Schema** (Postgres / MSSQL) | `SET LOCAL search_path` per transaction via `Session.after_begin` listener | Unqualified table references resolve only to the current tenant's schema |
| **Database** | One physical database per tenant; per-tenant engine cache | Cross-tenant queries are physically impossible — they target a different host/database |
| **RLS** (Postgres) | `SET LOCAL` of the policy GUC per transaction | Postgres enforces `USING (tenant_id = current_setting(...))` at row evaluation |
| **Hybrid** | Premium tenants on schema, standard tenants on RLS — both share one engine | Combination of the two above |

These guarantees apply to ORM and Core SQL that goes through the session the
library hands you. They do **not** apply to raw connections you obtain
yourself, to manual `set_session`/`set` calls, or to SQL that the application
constructs and ships through a different engine.

### Tenant identifier injection

All identifiers that flow into DDL (schema/database creation, `search_path`,
table prefixes) pass through `assert_safe_schema_name` /
`assert_safe_database_name` before interpolation. The regex grammar
(`[a-z_][a-z0-9_]{0,62}`) is enforced; anything outside it raises, never
silently truncates.

### Tenant enumeration via the resolution layer

Every resolver (header, subdomain, path, JWT) returns the same generic
`"Tenant not found"` payload for every failure mode (missing identifier,
malformed identifier, unknown tenant, expired JWT, audience mismatch).
Unknown-tenant errors do **not** produce a distinguishable HTTP 404; a
caller cannot probe for valid tenant slugs by status-code comparison.

### Sensitive field leakage in the persistence layer

When `enable_encryption=True`, the manager encrypts `database_url` and
metadata keys with the `_enc_` prefix using Fernet (AES-128-CBC + HMAC-SHA256)
before writing to the store. The proxy decrypts on every read path
(`get_by_identifier` / `get_by_id` / `list` / `get_by_ids` / `search` /
`create` / `update` / `set_status` / `update_metadata`) so route handlers
and the L1 cache see plaintext. Backup files, query logs, and dumps see
only ciphertext.

**Limitation — `update_metadata` does not retroactively encrypt legacy
plaintext.** The store merges the patch server-side (`jsonb ||` on
PostgreSQL); `update_metadata` encrypts only the keys present in the
patch. A pre-existing `_enc_*` key that is **already plaintext in the
row** and is not mentioned in the patch stays plaintext. This only
applies to data written *before* encryption was enabled. Before flipping
the flag in production, run
`TenancyEncryption.find_plaintext_enc_keys(tenant.metadata)` over every
row and one-shot-rewrite the offenders via
`update_metadata(tenant.id, plaintext_dict)` — the proxy encrypts each
key on that pass.

**Reserved prefix — `enc::`** Encrypted strings are stored with the
literal prefix `enc::`. Do not write user-supplied free-text values
that begin with this string into `database_url` or `_enc_*` metadata
keys — the reader treats anything starting with `enc::` as ciphertext
and Fernet decryption will raise `InvalidToken` on the next read. The
prefix is a structural marker, not a security boundary, and is
deliberately short for log readability.

### Sliding-window rate-limit bypass

The Redis Lua script reads time from `redis.call('TIME')`, not from the API
host's wall clock. NTP skew, daylight-saving transitions, and clock-drifted
VM instances cannot corrupt the window. Each request supplies a unique
member identifier so concurrent requests at the same Redis tick don't
overwrite each other. The script is atomic at the Redis level.

### Forging tenant identity via spoofed `X-Forwarded-Host`

!!! danger "This is insecure by default — set `trust_x_forwarded=False`"

    `SubdomainTenantResolver` defaults to **`trust_x_forwarded=True`**, so it
    reads `X-Forwarded-Host` in preference to `Host`. With no proxy in front —
    or a proxy that forwards the client's header unchanged — an attacker sends
    `X-Forwarded-Host: victim-tenant.example.com` and every request resolves as
    that tenant. This is full tenant impersonation from a single header.

    The default is retained for backward compatibility: flipping it would
    silently break every deployment behind a `Host`-rewriting proxy, which
    would start resolving the wrong tenant with no error. It is scheduled to
    flip in the next major release.

    Until then the exposure is made visible rather than silent — a startup
    `WARNING` is logged whenever the header is trusted. **Pass
    `trust_x_forwarded=False` explicitly** unless the table below says
    otherwise.

#### Deployment checklist — `trust_x_forwarded`

The safe setting has a deployment trade-off worth being explicit about:
behind a reverse proxy that **rewrites the `Host` header** to the proxy's
internal backend hostname (a common Kubernetes Ingress pattern, AWS ALB
host-based routing without `preserve_host`, etc.), `trust_x_forwarded=False`
will silently route to the wrong tenant — or fail to match any — because the
public hostname only survives in `X-Forwarded-Host`. That is why the flag
exists, and why the default cannot simply be flipped without a major version.

| Deployment shape | `trust_x_forwarded` |
|---|---|
| Local dev (`uvicorn` directly on the public port) | `False` — set explicitly |
| Single trusted proxy that **preserves `Host`** (nginx `proxy_set_header Host $host`) | `False` — set explicitly |
| Reverse proxy that **rewrites `Host`** (K8s Ingress default, AWS ALB host-routing without preserve, Cloudflare with origin rules) | `True` (current default) |
| Direct exposure to the internet with no proxy | `False` — **the default is unsafe here** |
| Behind a CDN or WAF that you control and that sanitises XFW | `True` (current default) |

When opting in, the proxy chain must **strip any inbound
`X-Forwarded-Host` header** from the *client*-facing edge before
forwarding it to FastAPI. Otherwise a client can supply
`X-Forwarded-Host: victim-tenant.example` and reach any tenant on the
platform.

Worked example (nginx in front of FastAPI, public hostname preserved):

```nginx
location / {
    # Edge: drop inbound XFW so an attacker's header doesn't leak through.
    proxy_set_header X-Forwarded-Host "";
    # Then re-set XFW from the trusted incoming Host.
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header Host $host;  # also preserves Host — either approach works
    proxy_pass http://fastapi_upstream;
}
```

With this configuration `trust_x_forwarded=False` works correctly because
`Host` is preserved — prefer it whenever you can guarantee that, since it
removes the spoofing vector entirely rather than relying on the edge to strip
the header.

### Algorithm-confusion attacks on JWT tenant tokens

`JWTTenantResolver` pins `algorithms=[self._algorithm]` at decode time
**and** rejects `algorithm="none"` (and case-variants, whitespace, empty
string) at construction. Unsigned tokens cannot be configured into the
resolver.

---

## What this library does NOT protect against

### Authentication of the caller

The tenancy middleware trusts whatever the resolver returns. If the
resolver pulls a tenant ID from an HTTP header, anyone who can send that
header can claim that tenant. Pair tenancy with an authentication layer
that runs **before** the tenancy middleware: validate a session cookie,
verify a Bearer access token (JWT or opaque), or use mTLS — then assert
that the authenticated principal is actually allowed to act for the
resolved tenant.

The `JWTTenantResolver` is a tenant-identification mechanism, not a user-
identification mechanism. A signed JWT proves the *token issuer* believed
the tenant claim; it does not by itself prove the *caller* is authorised
for that tenant. Use the `sub` / `azp` / custom claims in addition.

### Authorisation within a tenant

Resolving a tenant gives every authenticated user equal access to that
tenant's data. Per-user roles, per-resource permissions, and field-level
access control are application concerns.

### SQL injection in application queries

The library validates identifiers used in **DDL** and middleware-issued
DML (e.g. `SET LOCAL search_path`). It does not inspect or rewrite the
SQL your application sends. Use parameterised queries (`text(":id")` with
binds, ORM models, or `bindparams`) in every route handler. The isolation
strategies provide a backstop, not a substitute, for parameterisation.

### Data exfiltration via legitimate access

If an authenticated user has read access to a tenant's data, they can
read it. The library does not enforce data classification, redaction,
masking, or query auditing. RLS policies authored at the database level
are an option but are out of scope for the library itself.

### In-flight transport security (TLS) or transport-layer attacks

The library assumes HTTPS. Tenant identifiers in headers, JWTs in `Authorization`,
and cookies are sent in clear text without TLS. Terminate HTTPS at your
load balancer or run uvicorn with `--ssl-certfile`/`--ssl-keyfile`. Use
HSTS. Don't accept HTTP for any path that flows through tenancy
middleware.

### CSRF on cookie-authenticated requests

If you authenticate users with session cookies (rather than `Authorization`
headers), enable a CSRF protection layer **before** the tenancy
middleware. Without it, an attacker can trick a user's browser into
sending a state-changing request that the tenancy middleware will happily
route to the user's tenant.

### Side-channel attacks

The library uses `constant_time_compare` for token comparisons in
`utils/security`, but does not defend against side-channel attacks
(timing, cache, power) on the database or on the encryption key in
memory. Run on infrastructure that addresses these at the platform level
(constant-time crypto in the runtime, memory-protected enclaves where
appropriate).

### Compromised application secrets

The `encryption_key`, JWT `secret`, Redis credentials, and database
credentials are all loaded from environment variables or the
`TenancyConfig`. If your secrets management (Vault, AWS Secrets Manager,
sealed-secrets) is compromised, every layer the library protects with
those secrets is compromised. Rotate regularly; see the [Key Rotation
section](#key-rotation) below.

### Denial of service

The rate limiter throttles requests per tenant but does not protect
against:

- **Slowloris** and other connection-exhaustion attacks (handle at the load balancer / reverse proxy).
- **Compute amplification** in route handlers (use timeouts and the dependency budget).
- **Cache stampede** when a hot tenant is evicted from the L1 cache and many concurrent requests refetch from the store (mitigate with a longer TTL or `singleflight`-style coalescing in the store).

### Tenant data destruction by privileged operators

`destroy_tenant()` permanently drops the schema/database. There is no
undo. Build approval workflows and confirm-by-typing prompts in the
admin layer; the library will not slow you down.

---

## Required application responsibilities

A short checklist for a production deployment:

- [ ] Terminate HTTPS at the load balancer; redirect HTTP → HTTPS.
- [ ] Add an authentication middleware *before* `TenancyMiddleware`.
- [ ] Add an authorization layer *after* authentication that asserts the authenticated principal is allowed to act for the resolved tenant.
- [ ] If using cookie auth, add CSRF protection.
- [ ] Configure CORS explicitly (`fastapi.middleware.cors`) — don't allow `*` for credentialed requests.
- [ ] Set `enable_encryption=True` and supply a 32+ character `encryption_key` from a secret manager.
- [ ] Set `enable_rate_limiting=True` with a Redis URL.
- [ ] Set `JWTTenantResolver(... audience="your-service-name")` — without `audience` the resolver warns at startup.
- [ ] Run only behind a trusted reverse proxy when enabling `trust_x_forwarded=True` on the subdomain resolver or `trust_x_forwarded_for=True` on the audit dependency.
- [ ] Enable database SSL (`?sslmode=require` or `ssl=True` in the URL).
- [ ] Pin tenant-resolver order: header → fallback (don't trust multiple resolvers in parallel).

---

## Key rotation

The current encryption setup uses a single Fernet key derived via HKDF
from the configured `encryption_key`. Fields encrypted with the old key
become unreadable the moment you change the key — the `enc::` prefix
distinguishes ciphertext from plaintext, but does not distinguish *which
key* the ciphertext was produced by.

If you suspect key compromise, the safest procedure is:

1. **Stop** writes to any tenant whose record will be re-encrypted (most apps will accept a short maintenance window for this).
2. **Read** every tenant via the old key (`enable_encryption=True`, current `encryption_key`).
3. **Switch** the application to the new `encryption_key` *and disable encryption temporarily* in a single deploy, so reads return plaintext (Fernet's `decrypt` is idempotent on plaintext, but a new key would refuse old ciphertext).
4. **Re-write** every tenant via `manager.store.update(tenant)` — the proxy encrypts on write with the new key.
5. **Re-enable** encryption in a follow-up deploy.

This is operational toil. A future release will likely add a
`encryption_key_secondary` configuration field that allows reading with
either of two keys while writing with the primary, eliminating the
maintenance window.

If you need the dual-key capability today, you can implement it as a thin
wrapper around `TenancyEncryption` that tries the primary key first and
falls back to the secondary on `InvalidToken`. The library does not yet
provide this out of the box.

---

## Threat actors considered

This document was written with three threat models in mind:

| Actor | Capabilities | What this library mitigates |
| --- | --- | --- |
| **External web attacker** | Can send arbitrary HTTP requests to the public endpoint | Anti-enumeration, identifier injection, JWT alg-confusion, XFH spoofing, rate limiting |
| **Compromised tenant** | Has valid credentials for tenant A; tries to read tenant B's data | Isolation strategies (schema/database/RLS), session-scoped GUC/search_path, defence-in-depth tenant_id filters |
| **Backup leak** | Has read access to a database dump / log archive | Field-level encryption of `database_url` and `_enc_*` metadata |

Actors **not** considered: a compromised database administrator, a
compromised application host, a compromised secret-management system,
nation-state side-channel attackers. Those threat models are outside the
library's design scope.

---

## Reporting a security issue

If you find an issue this document does not cover, **please do not file a
public GitHub issue**. Email the maintainer or use GitHub's private
security advisory feature for the repository. Coordinated disclosure with
a 90-day default embargo is preferred.
