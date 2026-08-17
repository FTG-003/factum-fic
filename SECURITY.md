# SECURITY.md

## Reporting a Vulnerability

If you discover a security vulnerability in **factum-fic**, please **do not open a public issue**.

Instead, send a private report to **info@pyragogy.org**.

We will:
1. Acknowledge receipt within **48 hours**.
2. Investigate and fix the issue within **14 days** (or provide a timeline).
3. Publish a security advisory on GitHub once the fix is released.

## Scope

The following are **in scope**:
- Remote code execution via crafted XML/PDF files
- Credential leakage through logging or error messages
- SQL injection in the queue database
- Unauthorized access to Fatture in Cloud API via token mishandling

The following are **out of scope**:
- Social engineering attacks on the maintainer
- Physical access to the machine running factum-fic
- Denial of service via large files (file size limits are not enforced)

## Safe Harbor

We will not pursue legal action against researchers who:
- Follow this disclosure policy
- Make a good-faith effort to avoid privacy violations and data destruction
- Report the issue privately before public disclosure

Thank you for helping keep factum-fic and its users safe.