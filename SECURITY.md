# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest (main) | ✅ |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email: **eliranbarhum@gmail.com**

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fix (optional)

You will receive an acknowledgement within 48 hours and a resolution timeline within 7 days.

## Security Architecture Notes

- **All traffic** to the portal goes through `oauth2-proxy` (TLS + OIDC session). Direct access to the `api-gateway` or `ui` services requires forging `X-Forwarded-User` headers, which is only possible from inside the cluster.
- **api-gateway** and **ui** are `ClusterIP` only — never expose them via LoadBalancer.
- **Dex** uses a self-signed internal TLS cert by default. Replace `mco-tls` with a real certificate from your CA or cert-manager.
- **Credentials** (VMware API passwords, LLM keys, AD bind password) are stored in a Kubernetes `Secret` and never logged or returned in API responses.
- **PowerCLI scripts** are wrapped in a vCenter connect/disconnect session. Scripts containing destructive verbs (Remove, Delete, etc.) require an explicit `allow_writes: true` flag from the caller.
- **Audit log**: every authenticated API call is logged to PostgreSQL with user identity, timestamp, path, method, and status code.
