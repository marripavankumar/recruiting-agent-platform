# Security Review (Phase 8, before the penetration test)

**Date:** 2026-11-25
**Scope:** backend API and agents (`backend/`), web app (`web/`), container images and the Compose stack (`infra/`). Code review plus dependency audit. This review does not replace the independent penetration test in Phase 8; it removes what a code review can find first, so the testers can spend their time on what it cannot.

## 1. Summary

No critical issue was found. Sign-in, role checks, client data isolation, the approval gate, SQL handling, the inbound webhooks and the candidate upload links held up. Five issues were fixed in this review; three lower-risk items stay open (section 4) and are listed for the penetration testers.

| # | Finding | Severity | Status |
| --- | --- | --- | --- |
| F1 | Career-page fetcher could be pointed at the platform's own network (SSRF) | High | Fixed |
| F2 | Compose stack published the database, Qdrant and n8n on every network interface | Medium | Fixed |
| F3 | Sign-in accepted an email address the provider marks as unverified | Medium | Fixed |
| F4 | Web app sent no security headers (could be framed: clickjacking on approve buttons) | Medium | Fixed |
| F5 | API docs and schema published in production | Low | Fixed |
| O1 | Approval covers the message content but not the recipient | Low | Open |
| O2 | No request rate limits on the public endpoints | Low | Open |
| O3 | Default settings are the local ones (`RAP_ENVIRONMENT=local`, demo sign-in) | Low (by design) | Open: deployment checklist |

## 2. Fixed

**F1. Server-side request forgery in Agent 2's career-page fetcher.** Career-page URLs come from CSV imports, data providers and the pages' own redirects. The fetcher followed any URL and any redirect, with no size limit, so a crafted company record, or a company site redirecting, could make the platform call Qdrant (no authentication), n8n, the database host or a cloud metadata endpoint. Now every URL, every redirect hop and robots.txt must resolve only to public internet addresses, redirects are followed by hand (at most 5), and pages are capped at 5 MB (`agents/hiring_signals.py`, tests in `test_search_and_discovery.py`). Remaining gap: a host that changes its DNS answer between the check and the connection (DNS rebinding). Production should also deny the platform's outbound traffic to private address ranges at the network level.

**F2. Ports open to the network.** `infra/docker-compose.yml` published PostgreSQL (user and password `rap`), Qdrant and n8n on all interfaces on a Linux host. They now listen on 127.0.0.1 only. The web app and API also default to 127.0.0.1; `RAP_BIND_ADDRESS=0.0.0.0` opens them, which is safe only with company sign-in. The API logs a warning at start whenever demo sign-in is on.

**F3. Unverified email at sign-in.** Roles are found by email address, so an identity provider account carrying a staff member's address without having verified it could sign in as that person. The API now refuses a token whose `email_verified` claim is false (`identity/auth.py`). With Microsoft Entra ID, prefer `RAP_OIDC_EMAIL_CLAIM=preferred_username` (the sign-in name, which the tenant controls) over `email`, which users can sometimes set themselves.

**F4. Missing security headers.** The web app now sends `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'; base-uri 'self'; object-src 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` and a `Permissions-Policy` (`web/next.config.ts`). A full script CSP is left for later (Next.js needs nonces for it). HTTPS and HSTS belong on the reverse proxy in front of the app.

**F5. API docs in production.** `/docs`, `/redoc` and `/openapi.json` are now off when `RAP_ENVIRONMENT=prod`.

## 3. Checked and found sound

| Area | What was checked |
| --- | --- |
| Sign-in (API) | OIDC tokens verified for signature, issuer, audience and expiry on every request; algorithms pinned (RS256, ES256). Demo header sign-in refuses to start outside `RAP_ENVIRONMENT=local`. Inactive users are refused. |
| Sign-in (web) | Authorization code flow with PKCE, state and nonce; ID token kept in an httpOnly, SameSite=Lax cookie (Secure over HTTPS); the browser never holds an API credential. |
| Role checks | Every API route requires a signed-in user; each one checks the role (route-by-route listing). Agent runs are limited to each agent's roles. |
| Client isolation | Client users see only their own company's jobs and submissions; internal job fields are removed from what they see; feedback is accepted only on their own submissions. |
| Approval gate | Every send goes through `assert_approved`, which checks approval status and that the content is exactly what was approved; opt-out, environment switch, allow-list and daily cap are checked before any channel runs. Agents cannot send directly. |
| SQL | All queries take parameters; the f-strings in SQL only insert fixed column lists. |
| Webhooks | `/inbound/email` and `/inbound/bounce` require the shared secret (constant-time compare) and refuse when none is configured. |
| Public links | Unsubscribe tokens are HMAC-signed (the default secret is refused outside local); candidate upload links are 256-bit random, stored only as a hash, and expire. |
| File uploads | 10 MB per file, 20 files per upload, 50 PDF pages, 50 MB uncompressed per Word file (zip bomb), type decided from content; Word XML is parsed without entity resolution. |
| Dependencies | `pip-audit` (Python, locked versions) and `npm audit` (web, production): no known vulnerabilities on 2026-11-25. |
| Containers | Images run as non-root users and hold no secrets; settings come from the environment at runtime. |

## 4. Open items for the penetration test

- **O1. Recipient not covered by the approval.** The approval hash covers subject and body; the recipient comes from the workflow that drafted the message and no user can change it, so this is defence in depth only. Binding the recipient into the approval would change how existing approvals verify, so it is left for a planned change.
- **O2. Rate limits.** The public endpoints (`/unsubscribe`, `/collect/{token}`, the webhooks) and sign-in have no request limits. Their tokens cannot be guessed, so the risk is load, not access. Add limits at the reverse proxy or API gateway.
- **O3. Local defaults.** Without settings the API runs as `local` with demo sign-in, so that a laptop works out of the box. Every shared environment must set `RAP_ENVIRONMENT` (`dev`, `test` or `prod`), `RAP_AUTH_PROVIDER=oidc`, a real `RAP_UNSUBSCRIBE_SECRET` and `RAP_INBOUND_WEBHOOK_SECRET`; the go-live runbook (doc 06) should check them.
- **Prompt injection.** Inbound emails, JDs and resumes reach the LLM. An injected instruction can at most change a draft or a classification: nothing is sent without a person approving it. Testers should still try it against the deep agents (`RAP_AGENTS_ENABLED`).
