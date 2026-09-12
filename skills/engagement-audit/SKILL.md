---
name: engagement-audit
description: >
  Evaluates on-site experience for visitors who arrive via AI referrals —
  navigation clarity, orientation cues, load performance, mobile readiness,
  CTAs, and context retention — because a perfectly crawlable page can still
  bounce visitors if it disorients them on landing.
license: MIT
---

# Engagement Audit

> **Diagnostic question:** *Why do visitors who arrive not stay?*

This skill addresses the **on-site experience** layer. A page can be perfectly
crawlable, richly structured, and confidently cited by an AI assistant — and
still lose the visitor within seconds because it fails to orient, engage, or
retain them. This skill surfaces those failures.

It folds in the relevant implication from **Appendix E** (personalization):
assistants personalize using prior context, so a site with zero context
retention of its own gives returning visitors nothing to latch onto.

---

## Checks

| # | Check | What to look for |
|---|-------|------------------|
| 1 | **Navigational clarity** | Can a visitor reach key information (about, pricing, contact, products) within 1–2 clicks from any landing page? Is the nav structure obvious and consistent? |
| 2 | **Orientation cues** | Breadcrumbs present, clear headings, "what is this page for" signals near the top of the page (hero copy, value proposition), not just a wall of content. |
| 3 | **Load performance red flags** | Large unoptimized images, render-blocking resources, excessive third-party scripts, Largest Contentful Paint (LCP) concerns. |
| 4 | **Mobile responsiveness** | Viewport meta tag, touch-friendly tap targets, readable text without zoom, no horizontal scroll. |
| 5 | **Calls-to-action clarity** | Are primary CTAs visible, distinct, and actionable? Or are they buried, ambiguous ("Learn More" × 12), or absent? |
| 6 | **On-site search** | Is site search available? Is it discoverable (not hidden behind an icon with no label)? |
| 7 | **Context retention / returning-visitor features** | Recently viewed items, saved state, personalized recommendations, cookie/session-based continuity — features that give a returning visitor (especially one sent back by an assistant) something to anchor on. |

---

## Procedure

1. **Assess navigation** on the homepage and 2–3 interior pages:
   - Count clicks to reach key info (about, pricing, contact, products/services).
   - Check for consistent nav placement and labeling.
   - Note any orphaned pages (no nav path, only reachable via direct URL).
2. **Check orientation cues:**
   - Breadcrumbs on interior pages.
   - Clear `<h1>` that communicates page purpose.
   - Above-the-fold content that answers "what is this?" before requiring scroll.
3. **Evaluate load performance:**
   - Check for images without `width`/`height` or that are excessively large.
   - Count render-blocking `<script>` and `<link rel="stylesheet">` in `<head>`.
   - Note excessive third-party domains.
4. **Test mobile readiness:**
   - `<meta name="viewport">` present.
   - Content renders without horizontal scrollbar at 375px width.
   - Tap targets are ≥ 48×48 CSS pixels with adequate spacing.
5. **Audit CTAs:**
   - Primary CTA visibility and distinctiveness.
   - CTA text specificity (actionable vs. generic).
   - Number of competing CTAs per viewport.
6. **Check for site search** — form or search icon in header/nav.
7. **Check for context retention features** — recently viewed, saved/favorited items, personalized sections, login-gated history.
8. **Compile findings** in the standard schema.

---

## Output (shared finding contract)

Findings use the marketplace-wide contract defined in `lib/report.py` (see the root
`README.md`). The executable compiler `scripts/engagement_analyzer.py` runs the core checks
(mobile viewport, `<h1>` orientation, `<nav>` landmark, interior-page breadcrumbs, ambiguous
CTA overload) over the fetched HTML and emits contract findings. Deeper checks (context
retention, LCP, tap-target sizing, on-site search) are planned for a later pass.

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

| Severity | Meaning |
|----------|---------|
| **critical** | Key information unreachable within 2 clicks, or page is functionally broken on mobile. |
| **high** | No orientation cues on landing pages, or primary CTA is missing / invisible. |
| **medium** | Load performance concerns, missing breadcrumbs, generic CTAs, no site search. |
| **low** | Minor UX issues (search icon without label, no context retention features). |
| **info** | Observation with no immediate action needed. |
