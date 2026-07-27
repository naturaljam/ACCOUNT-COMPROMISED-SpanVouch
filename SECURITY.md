# Security Policy

## Report a vulnerability

Do not disclose suspected vulnerabilities in a public issue. Use
[GitHub private vulnerability reporting](https://github.com/naturaljam/SpanVouch/security/advisories/new)
and include the affected commit or version, impact, reproduction steps, and any suggested
mitigation.

Do not include real API keys, authorization headers, raw provider responses, hidden model
reasoning, or sensitive agent traces in a report. Replace secrets and private trace values
with minimal synthetic examples.

## Check supported code

Security fixes target the current default branch. This pre-1.0 project does not promise
backports for older commits or unpublished development branches.

## Understand the deployment boundary

The included FastAPI service has no authentication or role-based access control (RBAC). `reviewer_label` is caller
supplied audit text and does not establish identity. The default server binds to localhost;
deployments exposed to a network must add authentication, authorization, TLS, request-size
limits, rate limits, and environment-specific secret management at the gateway or platform
boundary.

SpanVouch minimizes stored provider data, but operators remain responsible for reviewing
trace contents, retention requirements, provider terms, and applicable privacy obligations
before processing real workloads.
