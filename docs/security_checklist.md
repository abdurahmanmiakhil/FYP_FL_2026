# Security checklist (OWASP ASVS 4.0 level 2 / OWASP Top 10)

✅ done and tested · ⚠️ done with a documented limitation · 🔧 operator task at deployment

## V1 Architecture
- ✅ Threat model for upload, tile server, auth, review, reports (`docs/threat_model.md`).
- ✅ Only the reverse proxy is exposed; database, Redis and object storage are on the internal network.
- ✅ Slides never leave the server; the browser receives only viewer tiles and results.

## V2 Authentication
- ✅ Passwords hashed with argon2id (m = 19 MiB, t = 2, p = 1), rehashed on login when parameters change.
- ✅ Password policy: ≥ 12 characters, 3 of 4 character classes, not common, not containing the email name (API and UI).
- ✅ Brute force: 5 logins/min per IP (429), lockout 15 min after 5 failures (423) - `tests/test_auth.py`.
- ✅ Generic error message and dummy hash verification for unknown accounts.
- ✅ Optional TOTP two-factor authentication; secret encrypted with AES-256-GCM.
- ⚠️ 2FA is optional - 🔧 make it mandatory for administrators by policy.
- ✅ Admin-created and reset passwords are flagged "must change password".

## V3 Session management
- ✅ httpOnly, Secure, SameSite=strict cookies; refresh cookie scoped to `/api/v1/auth`.
- ✅ 15-minute access tokens; 7-day refresh tokens rotated on every use; reuse detection revokes the session.
- ✅ Server-side sessions: logout, "sign out on all devices", password change, role/hospital change and deactivation revoke sessions immediately (`test_logout_revokes_session_server_side`).
- ✅ 15-minute idle timeout enforced by the server and the client.

## V4 Access control
- ✅ Role checks on every endpoint (admin / pathologist / urologist) - permission matrix test.
- ✅ Row-level access by hospital; other hospitals' cases answer 404 (no ID disclosure).
- ✅ Admins cannot review; admins cannot demote or deactivate themselves.

## V5 Validation, sanitisation, encoding
- ✅ Pydantic v2 validation of every request body and query; unknown fields ignored.
- ✅ Pseudonym codes: strict format, national-ID patterns rejected.
- ✅ Uploads: extension allow-list, TIFF magic bytes, OpenSlide pyramid check, size limit, optional ClamAV.
- ✅ Output encoding by React; CSV cells neutralised against formula injection; PDF text escaped.

## V6 Cryptography
- ✅ TLS by Caddy (automatic certificates); HSTS 2 years.
- ✅ JWT HS256 with a random ≥ 64-char secret; production refuses the development default.
- ✅ Object storage encrypted at rest (SeaweedFS `encryptVolumeData`; verified: plaintext marker absent from volume files).
- ✅ Backups encrypted with AES-256-CBC + PBKDF2 (200,000 iterations).
- 🔧 PostgreSQL volume: enable full-disk encryption on the host (LUKS / BitLocker / FileVault); see runbook.

## V7 Errors and logging
- ✅ Structured JSON logs with request ids; passwords, tokens, cookies and patient codes redacted.
- ✅ Unhandled errors return a generic message with a request id (no stack traces).
- ✅ Append-only audit trail: login, failed login, logout, upload, view, predict, review, export, delete, user admin; hash-chained and protected by a PostgreSQL trigger; integrity check in the admin UI.
- ✅ Optional Sentry with request bodies, cookies, headers and user data stripped.

## V8 Data protection
- ✅ Pseudonym codes only - no names or national IDs.
- ✅ `Cache-Control: no-store` on API responses; results `private`.
- ✅ Retention job (default 10 years, configurable) and 30-day purge of soft-deleted cases.
- ✅ Data-subject export and erasure endpoints (admin), audited.

## V9 Communications
- ✅ HTTP redirected to HTTPS (308); internal endpoints (`/metrics`) blocked at the proxy.

## V10 Malicious code / supply chain
- ✅ Model files verified by sha256 manifest before use; revisions pinned.
- ✅ Dependencies pinned; `pip-audit` and `npm audit`: 0 known vulnerabilities; Trivy: 0 fixable HIGH/CRITICAL in all images; gitleaks: no secrets (`scripts/security-scan.sh`, CI).
- ✅ Next.js upgraded 14 → 15.5.26 to fix CVE-2026-75604 (critical unauthenticated RCE).

## V12 Files
- ✅ Random UUID storage keys, key pattern check, no user-controlled paths; files outside the web root.

## V13 API
- ✅ CSRF double-submit token on all state-changing requests; CORS limited to the dashboard origin.
- ✅ OpenAPI docs disabled in production.

## V14 Configuration
- ✅ Secrets only in `.env` (generated, mode 600) or Docker secrets; never in images (`.dockerignore`).
- ✅ Containers run as non-root with `no-new-privileges`; frontend root filesystem read-only.
- ✅ Security headers: CSP (`default-src 'self'`, `frame-ancestors 'none'`), X-Frame-Options DENY, nosniff, Referrer-Policy no-referrer, Permissions-Policy, COOP.
- ⚠️ CSP allows `'unsafe-inline'` scripts (needed by Next.js bootstrap without nonces). Mitigated by React escaping and no user-supplied HTML.

## Open high-risk items
None. Remaining items are operator tasks (🔧) listed above.
