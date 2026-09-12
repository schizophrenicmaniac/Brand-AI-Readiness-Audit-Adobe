---
name: engagement-audit
description: >
  Evaluates on-site experience for visitors who arrive via AI referrals —
  navigation clarity, orientation cues, load performance, mobile readiness,
  CTAs, and context retention — because a perfectly crawlable page can still
  bounce visitors if it disorients them on landing.
license: MIT
tools:
  - run_command
  - read_url_content
  - view_file
---

# Engagement Audit

> **Diagnostic question:** *Why do visitors who arrive not stay?*

This skill addresses the **on-site experience and retention** layer. A page can
be perfectly crawlable, richly structured, and confidently cited by an AI
assistant — and still lose the visitor within seconds because it fails to orient,
engage, or retain them. This skill surfaces those failures.

It folds in the relevant implication from **Appendix E** (personalization):
assistants personalize using prior context, so a site with zero context
retention of its own gives returning visitors nothing to latch onto.

---

## When to Use

Invoke this skill when:
- Auditing a website's readiness for AI-driven discovery, referral traffic, and visitor conversion.
- Investigating high bounce rates among visitors referred by AI assistants (Perplexity, ChatGPT Search, Google SGE).
- The audit orchestrator invokes it as **step 5** in the pipeline (after `crawl-access-audit`, `render-extraction-audit`, `structured-data-audit`, and `freshness-corroboration-audit`).

This skill receives reachable pages confirmed by `crawl-access-audit`. Pages
flagged as unreachable or blocked are skipped.

---

## Inputs

| Parameter | Required | Description |
|-----------|----------|-------------|
| `url` | Yes | Site root URL (e.g., `https://example.com`). Provided by the audit orchestrator. |
| `pages` | No | Comma-separated list of specific page paths to audit. If omitted, audits the homepage and reachable pages discovered by the orchestrator. |
| `max_pages` | No | Maximum pages to analyze (default: 10). |

---

## Checks

| ID | Check | What to look for |
|----|-------|------------------|
| **EG-01** | **Navigational clarity** | Whether visitors can reach key destinations (about, pricing, contact, products/services) within 1–2 clicks from any landing page. Checks primary navbar consistency and detects critical destinations buried in footers or omitted entirely. |
| **EG-02** | **Orientation cues** | Whether an arriving visitor immediately understands page context and hierarchy. Evaluates presence of single descriptive `<h1>`, above-the-fold value proposition (hero copy), and breadcrumbs on deep catalog/interior pages. |
| **EG-03** | **Load performance red flags** | Structural bottlenecks that inflate First Contentful Paint (FCP) and Cumulative Layout Shift (CLS): render-blocking `<script>` tags in `<head>`, excessive external stylesheets, images missing dimensions or lazy-loading, and third-party domain sprawl (>10 domains). |
| **EG-04** | **Mobile responsiveness** | Presence and configuration of `<meta name="viewport">` tag (`width=device-width, initial-scale=1.0`). Detects accessibility anti-patterns that disable pinch-to-zoom (`user-scalable=no`, `maximum-scale=1.0`). |
| **EG-05** | **Calls-to-action clarity** | Discoverability and actionability of primary conversion paths. Flags absence of actionable CTAs on commercial/pricing pages, overuse of ambiguous generic labels ("Learn More" × 3+), and button overload inducing decision paralysis. |
| **EG-06** | **On-site search** | Discoverability and accessibility of site search inputs on multi-page catalogs, blogs, or documentation suites. Checks for search form controls and accessible labels on icon triggers. |
| **EG-07** | **Context retention / continuity** | Returning-visitor features: browsing history, recently viewed items, saved/wishlist state, account/profile continuity, and client-side retention hooks. |

---

## Procedure

> **Runtime target:** < 1 minute for a typical site (homepage + up to 10 pages).

### Step 1 — Extract engagement and UX metrics

Run the bundled extractor script:

```bash
python scripts/engagement_extractor.py --url <site_root_url> [--pages <paths>] [--max-pages 10] --output /tmp/engagement-raw.json
```

From the JSON output, verify extraction of:
- `navigation`: primary nav link counts and key destination coverage (about, pricing, contact, products).
- `orientation`: `h1` count/text, hero value proposition length, and breadcrumb presence.
- `performance`: render-blocking head scripts, external stylesheets, image dimensions, lazy loading, and third-party domain sprawl.
- `mobile`: viewport meta tag content and zoom restriction flags.
- `ctas`: total buttons, actionable verb count, and generic phrase counts.
- `search`: search input controls, search forms, and icon discoverability.
- `context_retention`: recently viewed, wishlist, saved items, and account anchors.

### Step 2 — Validate engagement signals and heuristics

Run the bundled validator script:

```bash
python scripts/engagement_validator.py --raw-data /tmp/engagement-raw.json --output /tmp/engagement-findings.json
```

The script evaluates checks **EG-01** through **EG-07** and outputs standardized findings.

### Step 3 — Apply false-positive suppression

Before finalizing findings, verify suppression rules in `references/engagement-checks-detail.md`:
- Do not flag missing navigation links or search forms on utility and checkout flows (`/login`, `/signup`, `/cart`, `/checkout`).
- Do not flag CTA or context retention gaps on policy pages (`/privacy-policy`, `/terms`, `/legal`).
- Do not require site search on minimalist microsites with ≤ 3 total pages.
- Do not require breadcrumbs on shallow pages (path depth = 1).
- Accept standard brand navigation aliases (e.g. "Docs" or "Documentation" fulfilling "Help/Support").

### Step 4 — Compile and return findings

Assemble all findings into the standard output schema below. Ensure unique `finding_id`
(`eg-001`, `eg-002`, ...), sort by severity (`critical` → `high` → `medium` → `low` → `info`),
and return the findings list to the audit orchestrator.

---

## Output Schema (per finding)

```json
{
  "finding_id": "eg-001",
  "skill": "engagement-audit",
  "severity": "medium",
  "title": "Deep interior pages lack breadcrumb navigation",
  "detail": "Product detail pages at /products/* have no breadcrumb navigation. Visitors arriving from an AI citation land with no orientation to the site hierarchy.",
  "affected_urls": ["/products/widget-pro", "/products/widget-lite"],
  "recommendation": "Add BreadcrumbList markup and visible breadcrumb UI to all catalog and product pages."
}
```

**Required fields per finding:** `finding_id`, `skill`, `severity`, `title`, `detail`, `affected_urls`, `recommendation`.

---

## Severity Guide

| Severity | Meaning | Examples |
|----------|---------|----------|
| **critical** | Key information unreachable within 2 clicks, or page is functionally broken on mobile. | Missing `<meta name="viewport">` tag rendering microscopic text on phones; core commercial destinations (`/pricing`, `/products`) completely omitted from navigation. |
| **high** | No orientation cues on landing pages, accessibility violations, or missing primary CTA. | Landing page lacks an `<h1>` or visible above-the-fold value proposition; commercial page has zero CTA buttons; viewport restricts zoom (`user-scalable=no`). |
| **medium** | Load performance concerns, missing breadcrumbs, generic CTAs, or missing search. | > 3 render-blocking `<script>` tags in `<head>`; images missing `width`/`height` (CLS risk); deep pages lack breadcrumbs; CTAs dominated by "Learn More" × 3+; no site search on content catalog. |
| **low** | Minor UX issues or hygiene gaps. | Offscreen images lack `loading="lazy"`; search trigger icon lacks accessible `aria-label`; no returning-visitor context retention widgets. |
| **info** | Observation or positive verification signal. | Responsive viewport properly configured; clear single `<h1>` with descriptive hero copy; distinct actionable conversion paths detected. |

See `references/engagement-checks-detail.md` for the full severity decision tree and override rules.
