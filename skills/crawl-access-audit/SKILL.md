---
name: crawl-access-audit
description: >
  Determines whether AI crawlers and search-engine bots can reach, fetch, and
  index the site's pages. Checks robots.txt (with AI-bot-specific classification
  of search vs. training bots), meta/X-Robots-Tag directives (noindex, nosnippet,
  noarchive), sitemap presence and validity, HTTP status codes and soft-404s,
  redirect chains and loops, canonical tag conflicts, response latency, bot-
  challenge interception (Cloudflare/Akamai/Imperva/CAPTCHA), TLS certificate
  health, crawl depth and orphan pages, and crawl-delay directives. This is the
  "step 1 gate" from Appendix A — if the crawler cannot
  get in, nothing downstream matters for that page.
license: MIT
tools:
  - run_command
  - read_url_content
  - view_file
---

# Crawl-Access Audit

> **Diagnostic question:** *Can a machine even get in?*

This skill maps to **Appendix A** of the Brand AI Readiness framework. It
evaluates whether the site's technical configuration permits crawlers —
especially AI search/retrieval crawlers — to discover, fetch, and index its
content. A failure at this layer renders every subsequent audit moot for the
affected pages.

---

## When to Use

Invoke this skill when:
- Auditing a website's readiness for AI-driven discovery and citation.
- Diagnosing why a brand is missing from AI assistant answers.
- The audit orchestrator invokes it as **step 1** in the pipeline.

This skill is always run first. If it finds a page completely blocked
(severity = critical), downstream skills should skip that page.

---

## Inputs

| Parameter | Required | Description |
|-----------|----------|-------------|
| `url` | Yes | Site root URL (e.g., `https://example.com`). Provided by the audit orchestrator. |
| `pages` | No | Comma-separated list of specific page paths to audit. If omitted, the skill will discover pages via sitemap + BFS crawl. |
| `max_pages` | No | Maximum pages to crawl in BFS depth analysis (default: 50). |

---

## Checks

### Group A — Permission Layer (Is the crawler allowed in?)

| ID | Check | What to look for |
|----|-------|------------------|
| **CA-01** | **robots.txt — AI crawler blocks** | Overly broad `Disallow` directives; bot-specific blocks distinguishing AI *search/retrieval* bots (OAI-SearchBot, ChatGPT-User, PerplexityBot, Claude-SearchBot — blocking = discoverability loss) from *training* bots (GPTBot, ClaudeBot, CCBot — blocking = policy choice, not defect). See `references/ai-crawler-registry.md` for the full classification. |
| **CA-02** | **robots.txt — critical path blocks** | `Disallow` rules that block high-value paths (`/products/`, `/pricing/`, `/blog/`, `/about/`) even when the bot is generally allowed. Suppresses false positives on CMS admin paths (`/wp-admin/`, `/admin/`). |
| **CA-03** | **Meta-robots / X-Robots-Tag directives** | `noindex` (page excluded from indexes), `nofollow` (links not followed), `nosnippet` (page cannot be quoted in AI answers), `noarchive` (cached version unavailable). Both `<meta name="robots">` tags and HTTP `X-Robots-Tag` headers are checked. |
| **CA-04** | **Crawl-delay directives** | Excessively high `Crawl-delay` values that throttle AI crawlers. >10s = medium, >30s = high. Training-bot crawl-delays are suppressed. |

### Group B — Discovery Layer (Can the crawler find the pages?)

| ID | Check | What to look for |
|----|-------|------------------|
| **CA-05** | **Sitemap presence & validity** | Missing sitemap, malformed XML, sitemap not declared in robots.txt, oversized sitemaps (>50k URLs), stale `<lastmod>` dates (>1 year), URLs that 404, duplicate URLs, and URL parameter proliferation (crawl trap risk). |
| **CA-06** | **Crawl depth & orphan pages** | High-value pages buried >3 clicks from the homepage. Orphan pages (in sitemap but unreachable via internal links). Broken internal links that fragment the crawl graph. |

### Group C — Fetch Layer (Does the server cooperate?)

| ID | Check | What to look for |
|----|-------|------------------|
| **CA-08** | **HTTP status codes & soft-404s** | 4xx/5xx errors on key pages. Soft-404s: pages returning 200 but with < 200 chars body text, "page not found" patterns, or 404-indicating `<title>` tags. |
| **CA-09** | **Redirect chains & loops** | Chains > 2 hops (medium), > 5 hops (high). Redirect loops (critical). Mixed 301/302 chains. HTTP→HTTPS→www normalization chains. |
| **CA-10** | **Canonical tag conflicts** | Missing self-canonical, canonical pointing to different domain (critical), canonical target returning 404 (high), canonical disagreeing with redirect destination (high). See `references/crawl-checks-detail.md` for full conflict matrix. |
| **CA-11** | **Response latency & timeouts** | TTFB > 3s (medium), > 5s (high), > 10s or timeout (critical). Rate-limit responses (429/503). |
| **CA-12** | **Bot-protection / challenge pages** | Cloudflare challenge ("Just a moment..."), Akamai bot manager, Imperva/Incapsula markers, CAPTCHAs (reCAPTCHA, hCaptcha, Turnstile), or JS-redirect-only pages. See `references/crawl-checks-detail.md` for full signature catalog. |
| **CA-13** | **TLS / HTTPS issues** | Expired or invalid SSL certificates. Missing HTTP→HTTPS redirect. Missing `Strict-Transport-Security` (HSTS) header. |

---

## Procedure

> **Runtime target:** < 2 minutes for a typical site (< 200 pages in sitemap).

### Step 1 — Analyze robots.txt

Run the bundled script:

```bash
python3 scripts/robots_analyzer.py --url <site_root_url>
```

From the JSON output, generate findings for:
- **CA-01**: Each AI search bot blocked with `Disallow: /` → finding (severity from registry).
- **CA-02**: Each critical path blocked for search bots or wildcard `*` → finding.
- **CA-04**: Each excessive `Crawl-delay` → finding.
- Note the `sitemap_urls` for Step 2.
- Note `wildcard_disallow_all` — if true, flag as critical.

### Step 2 — Validate sitemaps

Run the bundled script, passing sitemap URLs from Step 1:

```bash
python3 scripts/sitemap_validator.py --url <site_root_url> [--sitemap-urls <urls_from_step1>] > /tmp/sitemap-audit.json
```

From the JSON output, generate findings for:
- **CA-05**: Missing sitemap → finding (medium). Malformed XML → finding (medium). Stale lastmod → finding (low). URLs returning 404 → finding (medium). Oversized sitemap → finding (low). Parameter proliferation → finding (medium). Not declared in robots.txt → finding (low). Duplicate URLs → finding (low).

### Step 3 — Fetch and analyze pages

Run the bundled script:

```bash
python3 scripts/page_fetcher.py --url <site_root_url> [--pages <paths>] [--max-pages 50] --sitemap-page-urls-file /tmp/sitemap-audit.json
```

From the JSON output, generate findings for:

**TLS (CA-13):**
- Invalid certificate → finding (critical).
- Certificate expiring within 30 days → finding (high).
- Missing HTTP→HTTPS redirect → finding (medium).
- Missing HSTS header → finding (low).

**Per-page (CA-03, CA-08, CA-09, CA-10, CA-11, CA-12):**
For each page in the `pages` array:
- **CA-08**: Non-200 status → finding. Soft-404 detected → finding (medium).
- **CA-09**: Redirect chain > 2 → finding. Loop → finding (critical).
- **CA-03**: `noindex`, `nosnippet`, `noarchive` in meta_robots or x_robots_tag → finding (severity per `references/crawl-checks-detail.md`).
- **CA-10**: Canonical mismatch → finding (severity per `references/crawl-checks-detail.md`).
- **CA-11**: TTFB > 3s → finding. Timeout → finding (critical).
- **CA-12**: Challenge page detected → finding (severity depends on page importance).

**Crawl graph (CA-06):**
- Pages at depth > 3 → finding (medium).
- Broken internal links → finding (medium).
- Sitemap URLs absent from the normalized link frontier → potential-orphan finding. Include `confidence`; a page/depth-bounded crawl cannot prove a page is orphaned.
- Pages skipped under the audit client's robots policy → access-restriction evidence, not a network error.

### Step 4 — Apply false-positive suppression

Before finalizing findings, suppress false positives using the rules in
`references/crawl-checks-detail.md`:
- Don't flag `noindex` on admin/staging/internal paths.
- Don't flag standard CMS hygiene blocks in robots.txt.
- Don't flag training-bot blocks as defects.
- Don't flag trailing-slash-only canonical differences.
- State that a challenge was observed for `BrandAIReadinessAudit`; do not claim a named AI bot is blocked without bot-specific evidence.

### Step 5 — Compile and return findings

Assemble all findings into the output schema below. Assign a unique `finding_id`
(`ca-001`, `ca-002`, ...) to each. Sort by severity (critical → info). Return
the findings list to the audit orchestrator.

---

## Output Schema (per finding)

```json
{
  "finding_id": "ca-001",
  "skill": "crawl-access-audit",
  "severity": "critical",
  "title": "robots.txt blocks OAI-SearchBot on all paths",
  "detail": "User-agent: OAI-SearchBot with Disallow: / prevents OpenAI's search crawler from indexing any page. This removes the brand from ChatGPT Search citations.",
  "affected_urls": ["/robots.txt"],
  "recommendation": "Remove the blanket Disallow for OAI-SearchBot, or add 'Allow: /' to permit search indexing while keeping GPTBot (training) blocked if desired."
}
```

**Required fields per finding:** `finding_id`, `skill`, `severity`, `title`, `detail`, `affected_urls`, `recommendation`.

---

## Severity Guide

| Severity | Meaning | Examples |
|----------|---------|----------|
| **critical** | Page is completely unreachable by crawlers or removed from all AI citations. Immediate fix required. | Blanket robots.txt block on all bots, redirect loop, TLS failure, 5xx on homepage, AI search bot blocked. |
| **high** | Page is reachable but explicitly excluded from indexing or citation. Should be fixed soon. | `noindex` on product page, `nosnippet` on landing page, canonical points to 404, TTFB > 5s. |
| **medium** | Page is indexable but with degraded crawl efficiency. Plan to fix. | Redirect chain > 2 hops, soft-404, stale sitemap, challenge page on interior page, crawl depth > 3, missing HTTP→HTTPS redirect. |
| **low** | Minor hygiene issue that marginally affects quality. Fix opportunistically. | Missing self-canonical, sitemap not in robots.txt, stale lastmod, missing HSTS, trailing-slash canonical mismatch. |
| **info** | Observation with no immediate action needed, or a positive signal. | Training bot blocked (policy choice), self-canonical present, TLS valid. |

See `references/crawl-checks-detail.md` for the full severity decision tree and override rules.
