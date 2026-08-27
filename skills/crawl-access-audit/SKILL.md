---
name: crawl-access-audit
description: >
  Determines whether AI crawlers and search-engine bots can reach, fetch, and
  index the site's pages — the "step 1 gate" from Appendix A. If this fails,
  nothing downstream matters for that page.
license: MIT
---

# Crawl-Access Audit

> **Diagnostic question:** *Can a machine even get in?*

This skill maps to **Appendix A** of the Brand AI Readiness framework. It
evaluates whether the site's technical configuration permits crawlers to
discover, fetch, and index its content. A failure at this layer renders every
subsequent audit moot for the affected pages.

---

## Checks

| # | Check | What to look for |
|---|-------|------------------|
| 1 | **robots.txt rules** | Overly broad `Disallow` directives, bot-specific blocks (e.g., `GPTBot`, `Google-Extended`), missing `Allow` overrides for key paths. |
| 2 | **Meta-robots tags** | `noindex`, `nofollow`, `none`, or `noarchive` on pages that *should* be indexed. |
| 3 | **Sitemap.xml presence & validity** | Missing sitemap, malformed XML, stale `<lastmod>` dates, URLs that 404, sitemap not referenced in robots.txt. |
| 4 | **HTTP status codes on key pages** | 4xx/5xx errors, soft-404s (200 status on empty/error pages). |
| 5 | **Redirect chains & loops** | Chains > 2 hops, redirect loops, HTTP→HTTPS→www chains that waste crawl budget. |
| 6 | **Canonical tag conflicts** | `rel=canonical` pointing to a different URL than the one served, conflicting canonical + redirect signals. |
| 7 | **Response latency / timeouts** | TTFB > 3 s on key pages, intermittent timeouts that cause crawlers to abandon the fetch. |

---

## Procedure

1. **Fetch `robots.txt`** at the site root. Parse every `User-agent` / `Disallow` / `Allow` block. Flag directives that block known AI-crawler user-agents or that blanket-block important path prefixes.
2. **Fetch the sitemap** (from robots.txt `Sitemap:` directive, or try `/sitemap.xml` and `/sitemap_index.xml`). Validate XML well-formedness. Sample-check listed URLs for HTTP status and `<lastmod>` freshness.
3. **Crawl key pages** (homepage + up to 10 high-value pages). For each page:
   - Record HTTP status code.
   - Follow redirects and record the chain length / final destination.
   - Extract `<meta name="robots">` and `X-Robots-Tag` headers.
   - Extract `<link rel="canonical">` and compare against the request URL.
   - Record TTFB and total response time.
4. **Compile findings.** Each finding must include:
   - `finding_id` (unique)
   - `skill`: `"crawl-access-audit"`
   - `severity`: `critical | high | medium | low | info`
   - `title`, `detail`, `affected_urls[]`, `recommendation`

---

## Output Schema (per finding)

```json
{
  "finding_id": "ca-001",
  "skill": "crawl-access-audit",
  "severity": "critical",
  "title": "robots.txt blocks GPTBot on all paths",
  "detail": "User-agent: GPTBot with Disallow: / prevents OpenAI's crawler from indexing any page.",
  "affected_urls": ["/robots.txt"],
  "recommendation": "Add 'Allow: /' under GPTBot or remove the blanket Disallow."
}
```

---

## Severity Guide

| Severity | Meaning |
|----------|---------|
| **critical** | Page is completely unreachable by crawlers (blocked, 5xx, redirect loop). |
| **high** | Page is reachable but explicitly excluded from indexing (noindex, canonical conflict). |
| **medium** | Page is indexable but with degraded crawl efficiency (long redirect chains, slow TTFB). |
| **low** | Minor hygiene issue (stale sitemap lastmod, missing sitemap reference in robots.txt). |
| **info** | Observation with no immediate action needed. |
