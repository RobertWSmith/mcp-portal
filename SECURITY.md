# Security Policy

Report suspected vulnerabilities privately to the repository security contact. Do not
include production credentials, access tokens, personal data, or exploit payloads in a
public issue.

Supported releases receive fixes on the latest minor release line. Deployments should
enable audience-bound JWT authentication, the hardened production controls, namespace
allowlisting, append-only audit export, and an explicit outbound hostname allowlist.

Security-sensitive changes include tool side effects, required scopes, policy rules,
downstream destinations, credential exchange, tenant partitioning, and namespace
admission. These changes require security-owner review in addition to ordinary code
review.
