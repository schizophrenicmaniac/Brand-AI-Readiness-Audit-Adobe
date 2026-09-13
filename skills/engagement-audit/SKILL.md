---
name: engagement-audit
description: >
  Evaluates on-site experience for visitors who arrive via AI referrals —
  navigation clarity, orientation cues, load performance, mobile readiness,
  CTAs, and context retention — because a perfectly crawlable page can still
  bounce visitors if it disorients them on landing.
license: MIT
allowed-tools: "run_command read_url_content view_file"
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
| **EG-01** | **Navigational clarity** | Presence and semantic labeling of primary navigation, consistency of navigation labels across sampled templates, and context-appropriate offering/support paths on pages with commercial evidence. It does not require pricing or commercial menus on news, documentation, or general sites. |
| **EG-02** | **Orientation cues** | Non-empty `<h1>` cues, introductory copy near the start of landing pages, and breadcrumbs on deep hierarchical documentation/commercial pages. Editorial permalinks are excluded from breadcrumb expectations. |
| **EG-03** | **Structural performance risks** | Measurable HTML architecture signals: synchronous external head scripts, stylesheet count, image dimension coverage, later-document image loading, and third-party host count. These are risks, not measured Core Web Vitals. |
| **EG-04** | **Viewport and zoom** | Presence of viewport metadata, exact `width=device-width`, and zoom restrictions (`user-scalable=no/0` or `maximum-scale<2`). |
| **EG-05** | **Calls-to-action clarity** | Missing controls only on pages classified as commercial, repeated generic labels only when they dominate non-editorial controls, and actionable CTA density. |
| **EG-06** | **On-site search** | Search inputs, forms, landmarks, triggers, and icon-trigger accessible names. Absence is reported only with a ≥5-page sample or ≥3 documentation/editorial pages. |
| **EG-07** | **Context retention / continuity** | Evidence of saved/history/account features and client storage hooks. Absence is reported only for a sampled stateful commercial journey, not news/docs/general sites. |

---

## Procedure

> **Runtime target:** < 1 minute for a typical site (homepage + up to 10 pages).

### Step 1 — Extract engagement and UX metrics

Run the bundled extractor script:

```bash
python scripts/engagement_extractor.py --url <site_root_url> [--pages <paths>] [--max-pages 10] --output /tmp/engagement-raw.json
```

The public extractor integration is:

```python
raw = engagement_extractor.build_raw(base_url, paths, session, max_pages)
```

`session` is requests-compatible and may be the orchestrator's shared cached safe session. The raw result records `successful_html_pages`, `skipped_pages`, and a per-page `html_success`/`fetch_status`. Only successful 2xx HTML responses that are non-empty and not probable block/interstitial pages contain extracted sections:
- `navigation`: semantic/inferred primary navigation, labels, internal links, and destination evidence.
- `orientation`: `h1` count/text, introductory text length, path depth, and visible/schema breadcrumbs.
- `performance`: structural counts, ratios, and resource samples; no synthetic timing claim is made.
- `mobile`: viewport content, device-width parsing, and zoom restrictions.
- `ctas`: per-control classifications, distinct-action counts, generic-label ratios, and text evidence.
- `search`: inputs, forms, landmarks, triggers, and accessible-name evidence.
- `context_retention`: saved/history/account markers and client-storage hooks.

### Step 2 — Validate engagement signals and heuristics

Run the bundled validator script:

```bash
python scripts/engagement_validator.py --raw-data /tmp/engagement-raw.json --output /tmp/engagement-findings.json
```

The script evaluates checks **EG-01** through **EG-07**. Programmatically, `EngagementValidator(raw).run_all()` returns a `list` of standardized shared-contract findings. The CLI wraps that list with site and count metadata.

### Step 3 — Apply false-positive suppression

Before finalizing findings, verify suppression rules in `references/engagement-checks-detail.md`:
- Skip every check when no successfully fetched HTML page is available; failed, blocked, empty, and non-HTML responses are coverage evidence only.
- Suppress navigation, CTA, search, and continuity expectations on configured utility/legal/checkout paths.
- Do not require commercial destinations such as pricing/about/contact. A context-appropriate offering or support route is checked only on commercial-profile pages.
- Require site-search evidence only for a ≥5-page eligible sample or when all ≥3 sampled pages are documentation/editorial.
- Require breadcrumbs only on deep hierarchical pages; suppress shallow pages and editorial permalinks.
- Report absent continuity features only when at least two of three or more eligible pages have commercial evidence.

### Step 4 — Compile and return findings

Assemble all findings into the standard output schema below. Ensure unique `id`
generated via `lib/report.py` (`make_finding`), sort by severity (`critical` → `high` → `medium` → `low` → `info`),
and return the findings list to the audit orchestrator.

---

## Output (shared finding contract)

Findings use the marketplace-wide contract defined in `lib/report.py` (see the root
`README.md`). The complete executable path is `engagement_extractor.build_raw(...)` followed
by `EngagementValidator(raw).run_all()`. `scripts/engagement_analyzer.py` is a compatibility
adapter for callers that already hold `{url, html, status_code, content_type?}` records and
runs the same seven checks. Findings contain only the shared stable `id`; no sequential
`finding_id` is added.

```json
{
  "id": "eg-2a77b0",
  "title": "No visible breadcrumb on 4 interior page(s)",
  "severity": "low",
  "skill": "engagement-audit",
  "evidence": {
    "url": "https://example.com/products/widget-pro",
    "source": "html",
    "locator": "nav.breadcrumb",
    "observed": "interior pages without breadcrumb: /products/widget-pro, /products/widget-lite"
  },
  "suggested_action": {
    "summary": "Add visible breadcrumb navigation on interior pages so visitors arriving mid-site from an AI citation can orient to the site hierarchy.",
    "priority": "P3"
  }
}
```

**Required per finding:** `id`, `title`, `severity`, `evidence{url, source, observed}`, `suggested_action{summary, priority}`.

---

## Severity Guide

| Severity | Meaning | Examples |
|----------|---------|----------|
| **critical** | Successful HTML is functionally unsuitable for typical mobile layout. | Missing non-empty viewport metadata. |
| **high** | A strong accessibility or conversion blocker with page-type evidence. | Device width omitted, zoom restricted, or a commercial-profile page has no measurable action control. |
| **medium** | Repeated orientation/navigation gaps or thresholded structural risks. | Missing H1; >3 synchronous external head scripts; ≥50% and ≥2 images missing dimensions; content-rich sampled site has no search. |
| **low** | Secondary semantic, hierarchy, CTA, loading, or continuity risk. | Missing nav landmark, deep hierarchical breadcrumb gap, generic CTAs dominate, later images load eagerly, eligible commercial sample lacks continuity features. |
| **info** | Not emitted by the current validator. | Positive measurements remain available in raw extraction data. |

See `references/engagement-checks-detail.md` for the full severity decision tree and override rules.
