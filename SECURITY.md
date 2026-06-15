# Security

## Reporting a vulnerability

Please report security issues privately to the maintainer (open a private security advisory
on GitHub, or email the address in the repository profile) rather than a public issue.
We'll acknowledge within a few days.

## Posture (what the service does to protect users & the owner)

- **Auth**: Google OAuth + signed session cookies (`SameSite=Lax`, `Secure` in prod). The app
  refuses to boot with the default session secret when auth is enabled.
- **Access control**: per-request endpoints require a session; jobs are owner-scoped; shareable
  analyses use unguessable random tokens (no enumerable ids).
- **Owner-paid LLM safeguards**: per-user daily quota (enforced under a row lock), an atomic
  global daily spend cap, a bounded review sample per analysis, and answer caching.
- **Untrusted content**: review text and the user's question are treated as data, never
  instructions; the model gets no tools and no secrets; returned quotes are verified against
  the source reviews; all model/review text is rendered escaped, behind a strict CSP.
- **Transport/headers**: HTTPS via Caddy + HSTS; CSP, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy` on every response.
- **Secrets**: API keys are server-side only, never logged, never returned in errors, never in
  the client bundle. `.env` is git- and docker-ignored; deps are pinned in `requirements.txt`.

## Responsible use

This tool scrapes publicly visible Google Play data via an unofficial library. Operators are
responsible for compliance with Google Play's Terms of Service and applicable law. Scraped
reviews contain user-generated content; reviewer names/images are dropped at ingest, and
redistributing datasets may carry data-protection obligations. Provided "as is", no warranty.
