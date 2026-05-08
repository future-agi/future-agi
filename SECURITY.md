# Security Policy

## Reporting a Vulnerability

The Future AGI team takes security seriously. If you discover a security vulnerability in Future AGI, please report it privately — **do not open a public GitHub issue.**

**Email:** **security@futureagi.com**

Please include as much of the following as you can:

- Type of issue (e.g. authentication bypass, SQL injection, SSRF, RCE)
- Full paths of source file(s) related to the manifestation of the issue
- Location of the affected source code (tag / branch / commit / direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue — how an attacker might exploit it

## Response timeline

- **Acknowledgement:** within 24 hours of report (Mon–Fri, Pacific & IST)
- **Initial assessment:** within 3 business days
- **Fix target:** depends on severity (see below)
- **Public disclosure:** coordinated with the reporter, typically 7–90 days after a patch is available

### Severity and fix targets

| Severity | Examples | Target |
|---|---|---|
| 🔴 Critical | RCE, auth bypass, mass data exposure | Patch within 72 hours |
| 🟠 High | Privilege escalation, tenant isolation breach, credential leak | Patch within 7 days |
| 🟡 Medium | XSS, CSRF, information disclosure (scoped) | Patch within 30 days |
| 🟢 Low | Rate-limit bypass, minor info leak, hardening gaps | Next scheduled release |

## Scope

**In scope:**

- The main repo: `future-agi/future-agi`
- Published SDKs: `ai-evaluation` (PyPI), `@traceai/*` (npm), and their Go/Java counterparts
- Managed Cloud: `app.futureagi.com`, `api.futureagi.com`, `cloud.futureagi.com`
- The agentcc gateway: `agentcc-gateway/`

**Out of scope:**

- Third-party integrations (report to the upstream vendor)
- Denial-of-service via traffic volume
- Social-engineering attacks on Future AGI employees
- Physical attacks on Future AGI infrastructure
- Spam / brute-force on public marketing pages

## Safe harbor

We will not pursue legal action against security researchers who:

- Make a good-faith effort to avoid privacy violations, destruction of data, and interruption of service
- Only interact with accounts they own or with explicit permission of the account holder
- Do not exploit a vulnerability beyond what is necessary to confirm its existence
- Report the vulnerability promptly
- Do not publicly disclose the vulnerability before a patch is released

## Acknowledgement

We maintain a [Security Researcher Hall of Fame](https://futureagi.com/security/hall-of-fame) and are happy to credit reporters who wish to be named. For qualifying reports, we run a bug bounty via HackerOne — contact security@futureagi.com for details.

## PGP

If you prefer encrypted communication, our PGP key is available at:
<https://futureagi.com/.well-known/pgp-key.txt>

---

Thanks for helping keep Future AGI and our users safe. ❤️
