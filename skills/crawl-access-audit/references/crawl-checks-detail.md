# Crawl-Access Checks — Detailed Reference

> **Purpose:** Deep-reference document for the agent executing `crawl-access-audit`.
> Contains severity decision trees, challenge-page signatures, soft-404 heuristics,
> canonical conflict logic, and false-positive suppression rules.
> 
> **Machine-Readable SSOT Datasets:**
> - Bot Registry: [`references/ai-crawler-registry.json`](file:///Users/divyansh/Projects/Brand%20AI%20Readiness%20Audit%20%7C%20Adobe/skills/crawl-access-audit/references/ai-crawler-registry.json)
> - Challenge Signatures & Soft-404s: [`references/challenge-signatures.json`](file:///Users/divyansh/Projects/Brand%20AI%20Readiness%20Audit%20%7C%20Adobe/skills/crawl-access-audit/references/challenge-signatures.json)
> - Crawl Configuration & Thresholds: [`references/crawl-config.json`](file:///Users/divyansh/Projects/Brand%20AI%20Readiness%20Audit%20%7C%20Adobe/skills/crawl-access-audit/references/crawl-config.json)

---

## 1. Severity Decision Tree

Use this flowchart to assign severity deterministically for each finding.

```
START: Is the page completely unreachable?
│
├─ YES (robots.txt blocks all crawlers, 5xx error, redirect loop, TLS failure)
│  └─► severity = CRITICAL
│
├─ NO → Is the page reachable but explicitly excluded from indexing or citation?
│  │
│  ├─ YES (noindex, nosnippet, AI search bot specifically blocked)
│  │  └─► severity = HIGH
│  │
│  ├─ NO → Is the page indexable but with degraded crawl efficiency?
│  │  │
│  │  ├─ YES (redirect chain > 2, TTFB > 3s, canonical conflict, soft-404,
│  │  │       challenge page, deep crawl depth, stale sitemap)
│  │  │  └─► severity = MEDIUM
│  │  │
│  │  ├─ NO → Is this a minor hygiene issue?
│  │  │  │
│  │  │  ├─ YES (missing self-canonical, no sitemap in robots.txt,
│  │  │  │       stale lastmod, missing HSTS)
│  │  │  │  └─► severity = LOW
│  │  │  │
│  │  │  └─ NO → Neutral observation or positive signal
│  │  │     └─► severity = INFO
```

### Severity Override Rules

| Condition | Override |
|-----------|----------|
| AI *search* bot blocked (`search_bot` category) | Always at least **high**, promote to **critical** if `Disallow: /` |
| AI *training* bot blocked (`training_bot` category) | Cap at **info** (policy choice, not defect) |
| `nosnippet` on product/landing pages | **high** (prevents citation in AI answers) |
| `nosnippet` on legal/privacy pages | **low** (expected) |
| Challenge page on homepage | **critical** |
| Challenge page on deep interior page | **medium** |
| TTFB > 5s | Promote from medium to **high** |
| TTFB > 10s or timeout | Promote to **critical** |

---

## 2. Challenge-Page Signature Catalog

When a bot-protection system intercepts a request, it returns a page that *looks* like
a normal response (usually HTTP 200 or 403) but contains a JavaScript challenge or
CAPTCHA instead of actual content. The agent should check for these signatures.

### Cloudflare

| Signal | Pattern |
|--------|---------|
| Title tag | `<title>Just a moment...</title>` |
| Title tag (alt) | `<title>Attention Required! \| Cloudflare</title>` |
| Meta tag | `<meta name="captcha-bypass"` |
| Body class | `class="cf-browser-verification"` |
| Cookie | `__cf_bm`, `cf_clearance` |
| Script src | `challenges.cloudflare.com` |
| Ray ID | `data-ray=` or `Cloudflare Ray ID:` in body |
| HTTP header | `cf-ray`, `cf-cache-status`, `server: cloudflare` |

### Akamai Bot Manager

| Signal | Pattern |
|--------|---------|
| Cookie | `_abck`, `ak_bmsc`, `bm_sz` |
| Script src | Contains `akamaized.net` or `akam` |
| Response header | `x-akamai-transformed`, `akamai-grn` |
| Body content | `AkamaiGHost` or `_AkamaiClientData` |

### Imperva / Incapsula

| Signal | Pattern |
|--------|---------|
| Cookie | `incap_ses_`, `visid_incap_`, `nlbi_` |
| Script src | `_Incapsula_Resource` |
| Meta tag | `<meta name="ROBOTS" content="NOINDEX, NOFOLLOW">` (on challenge page) |
| Body content | `Incapsula incident ID` |

### Generic CAPTCHA Detection

| Signal | Pattern |
|--------|---------|
| Form element | `<form` containing `captcha` (case-insensitive) |
| Google reCAPTCHA | `class="g-recaptcha"` or `src="google.com/recaptcha"` |
| hCaptcha | `class="h-captcha"` or `src="hcaptcha.com"` |
| Turnstile | `class="cf-turnstile"` or `challenges.cloudflare.com/turnstile` |

### JavaScript-Redirect-Only Pages

A page that returns HTTP 200 but has:
- Body content < 1 KB
- Contains `<script>` with `window.location`, `document.location`, or `location.href`
- No meaningful text content outside `<script>` tags

This is a soft block — the bot gets a redirect page that only works with JS execution.

---

## 3. Soft-404 Detection Heuristics

A "soft 404" is a page that returns HTTP 200 but is functionally a missing page.
AI crawlers will waste time indexing these, and they pollute the crawl graph.

### Detection Rules (apply in order; first match wins)

| # | Rule | Confidence |
|---|------|------------|
| 1 | HTTP 200 but **body text** (stripped of HTML tags) has fewer than **200 characters** | High |
| 2 | HTTP 200 and body text contains any of these patterns (case-insensitive): `"page not found"`, `"404"`, `"not found"`, `"does not exist"`, `"no longer available"`, `"we couldn't find"`, `"this page has been removed"`, `"page has moved"` | High |
| 3 | HTTP 200 and `<title>` contains `"404"`, `"not found"`, `"error"`, `"page missing"` | High |
| 4 | HTTP 200 and page has a `<meta name="robots" content="noindex">` tag AND body is generic/templated (< 1000 chars unique content) | Medium |
| 5 | HTTP 200 but response body is **identical** (by hash) to 2+ other unrelated URLs on the same domain | High (indicates a catch-all error template) |

### What to Record

For each detected soft-404:
- The URL
- The HTTP status code (200)
- The detection rule that triggered
- A snippet of the body (first 200 chars) as evidence
- The `<title>` tag content

---

## 4. Canonical Tag Conflict Resolution Logic

Canonical tags tell crawlers which URL is the "official" version of a page.
Conflicts confuse crawlers and can cause the wrong URL to be indexed (or none at all).

### Conflict Matrix

| Scenario | What Happens | Severity | Recommendation |
|----------|-------------|----------|----------------|
| **No canonical tag** on any page | Crawler must guess the canonical URL; duplicates may be indexed | `low` | Add self-referencing `<link rel="canonical">` to every page |
| **Self-canonical present** | ✅ Correct — page declares itself as canonical | `info` | No action |
| **Canonical points to different path on same domain** | Crawler treats current page as a duplicate of the canonical target | `medium` | Verify this is intentional (e.g., pagination → main page) |
| **Canonical points to different domain** | Likely misconfiguration (e.g., staging domain in canonical) | `critical` | Fix canonical to point to production URL |
| **Canonical target returns 404** | Crawler has no valid canonical; page may be de-indexed | `high` | Fix the canonical URL or remove the tag |
| **Canonical target returns 301/302** | Indirect canonical — crawler must follow the redirect to resolve | `medium` | Update canonical to point directly to final URL |
| **Canonical + redirect disagree** | Page redirects to URL-A but canonical says URL-B — conflicting signals | `high` | Align redirect destination and canonical target |
| **Canonical differs by protocol only** (http vs https) | Minor but causes duplicate signals | `medium` | Normalize to HTTPS in canonical |
| **Canonical differs by trailing slash only** | Common misconfiguration; minor crawl waste | `low` | Standardize trailing-slash policy |
| **Multiple canonical tags on same page** | Crawler ignores all of them (undefined behavior per spec) | `high` | Remove duplicates; keep exactly one |

### How to Check

1. Fetch the page and extract all `<link rel="canonical" href="...">` tags
2. If more than one → finding (multiple canonicals)
3. Normalize both the request URL and canonical URL (lowercase, strip fragment, resolve relative)
4. Compare:
   - Same → `info` (self-canonical, correct)
   - Different domain → `critical`
   - Same domain, different path → `medium` (verify intent)
   - Differs only by protocol/trailing slash → `low`
5. If canonical URL differs, HEAD-request the canonical target:
   - 200 → OK (canonical is valid)
   - 404 → `high` (broken canonical)
   - 301/302 → `medium` (indirect canonical)
6. If the page itself is a redirect, compare redirect target with canonical target:
   - Same → OK
   - Different → `high` (conflicting signals)

---

## 5. Redirect Chain Analysis Rules

| Condition | Severity | Finding |
|-----------|----------|---------|
| **No redirects** (direct 200) | `info` | Clean — no redirect overhead |
| **1 redirect** (e.g., http→https or www→non-www) | `info` | Normal normalization |
| **2 redirects** | `low` | Acceptable but consider flattening |
| **3+ redirects** | `medium` | Chain too long; crawlers may abandon |
| **5+ redirects** | `high` | Excessive chain; high risk of crawler abandonment |
| **Redirect loop** (URL seen twice in chain) | `critical` | Fatal — page is unreachable |
| **Mixed redirect types** (301 + 302 in same chain) | `medium` | Inconsistent signals; 302 prevents link equity transfer |
| **HTTP → HTTPS → www → non-www** (4-hop normalization) | `medium` | Common but wasteful; configure server to go directly to final URL |

---

## 6. False-Positive Suppression Rules

To avoid noisy, unhelpful findings, suppress these patterns:

| Pattern | Why Suppress |
|---------|-------------|
| `noindex` on paths matching `/admin/`, `/wp-admin/`, `/staging/`, `/internal/`, `/api/`, `/cgi-bin/` | CMS admin / internal pages should not be indexed |
| `Disallow: /wp-admin/`, `/admin/`, `/cgi-bin/`, `/tmp/`, `/?s=` | Standard CMS hygiene blocks |
| `Disallow` for `training_bot` category bots | Policy choice, not a defect |
| `Crawl-delay` for `training_bot` category bots | Throttling training bots is normal |
| `noindex` on `/search`, `/tag/`, `/author/` pages | Thin/duplicate content suppression (best practice) |
| `noarchive` on pages with sensitive content (login, account, checkout) | Expected — these pages shouldn't be cached |
| Redirect from `/page` to `/page/` (trailing slash only) | Normal URL normalization, not a real redirect chain |
| Canonical differing by trailing slash only | Minor normalization, not a real conflict |
| HTTP 403 on `/robots.txt` itself | Some servers return 403 instead of 404 for missing robots.txt — treat as "no robots.txt" (allow all) |

---

## 7. URL Parameter Proliferation Detection

When analyzing sitemap URLs or crawled links, detect parameter proliferation:

### Detection Logic

1. Parse all discovered URLs and extract query parameters
2. Group URLs by path (ignoring query string)
3. Flag if any single path has **10+ URL variants** differing only by query parameters
4. Common culprits: `sort`, `order`, `filter`, `page`, `sessionid`, `sid`, `ref`, `utm_*`, `fbclid`, `gclid`
5. Severity: `medium` (wastes crawl budget but doesn't block access)

### Tracking Parameter Exclusion

When counting "real" parameter variants, ignore known tracking parameters that don't change content:
`utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, `fbclid`, `gclid`, `msclkid`, `ref`, `affiliate`

These create URL duplication but are a *canonical/dedup* issue, not a content-variation issue.
