# Upstream SSO session logout (chentu patch)

Based on upstream v1.9.3. Enable `sso_require_session_binding = true` only with an
OIDC provider that issues signed RS256 ID tokens carrying `sub`, `sid`, `iat`,
`exp` and `auth_time`. Discovery and JWKS must remain available at the configured
issuer, with the same origin and trusted TLS certificates.

Configure Keycloak's confidential Matrix client with frontchannel logout disabled,
backchannel logout session required, and the exact URL
`https://matrix.example.internal/_tuwunel/oidc/backchannel/matrix-app`.
Keycloak must trust the homeserver ingress CA. The endpoint verifies the signature,
issuer, audience, event, session/subject and timestamps before persisting revocation.
Do not enable unsigned-token acceptance or disable certificate verification.

Each access and refresh token inherits the original SSO session binding through
both native OIDC and legacy Matrix SSO. Durable session revocations fence later
exchanges and refreshes, including after restart. A revoked session receives HTTP
401 with `M_UNKNOWN_TOKEN` and explicit `soft_logout: false`. Another session for
the same account remains usable. Appservice tokens retain their existing behavior.

Enabling strict binding invalidates previously issued unbound user tokens. Users
must sign in once again; server name, provider ID, issuer, client ID, subject,
users, rooms and messages must be preserved. Device authorization without an
upstream session binding is not supported in strict mode. Do not downgrade to an
unpatched server while relying on the persisted revocation records.

This patch does not reconcile missed Keycloak notifications. If delivery fails,
restore connectivity, explicitly revoke affected Matrix devices, and validate with
a fresh session. An already deleted SSO session might not generate another event.
Token binding and revocation records currently have no garbage collection.

Validation includes signed-claim rejection tests, RocksDB restart/session-scope
coverage in `src/main/tests/sso_revocation.rs`, and chentu's C-IDS-65/92–95 tests.
The browser test exercises Chat-only logout, IAM's actual logout button, then a
new platform account. Build production artifacts outside the platform repository
and pin their digest in the environment's offline bundle.
