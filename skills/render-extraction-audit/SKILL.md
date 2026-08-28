---
name: render-extraction-audit
description: >
  Detects content that is visually present to a human but structurally invisible
  to a machine reader — the core "plainly visible yet machine-invisible" failure
  mode from Appendix C. Checks raw-HTML-vs-rendered-DOM gaps (JS-only content,
  SPA shells, AJAX-loaded blocks), opaque media without text alternatives (images
  without alt, SVG text, canvas, video/audio without transcripts, PDF-only facts,
  icon fonts, CSS pseudo-element text), interaction-gated content (tabs/accordions
  not in DOM, shadow DOM, iframes, infinite scroll), and signal-to-noise ratio
  (boilerplate-heavy pages). Each finding includes evidence and severity escalated
  by key-fact classification (pricing, contact, product identity).
license: MIT
tools:
  - run_command
  - read_url_content
  - view_file
---

# Render-Extraction Audit

> **Diagnostic question:** *Can the machine read what's there?*

This skill maps to **Appendix C** of the Brand AI Readiness framework. It
identifies facts and content that a human visitor can see on the rendered page
but that a simple text extractor (no JS engine, no OCR) would miss entirely.
A failure at this layer means the content is technically reachable (crawl-access
passed) but the machine cannot extract or quote the actual facts.

---

## When to Use

Invoke this skill when:
- Auditing a website's readiness for AI-driven discovery and citation.
- Diagnosing why a brand's specific facts (pricing, specs, contact) are missing
  from AI assistant answers even though the pages are crawlable.
- The audit orchestrator invokes it as **step 2** in the pipeline (after
  crawl-access-audit confirms the pages are reachable).

This skill receives the list of reachable pages from the orchestrator. Pages
flagged as unreachable/blocked by crawl-access-audit are skipped.

---

## Inputs

| Parameter | Required | Description |
|-----------|----------|-------------|
| `url` | Yes | Site root URL (e.g., `https://example.com`). Provided by the audit orchestrator. |
| `pages` | No | Comma-separated list of specific page paths to audit. If omitted, defaults to homepage only (orchestrator typically provides a page list). |
| `max_pages` | No | Maximum pages to analyze (default: 10). |

---

## Checks

### Group A — JS Rendering Gap (Content only appears after JS execution)

| ID | Check | What to look for |
|----|-------|------------------|
| **RE-01** | **Raw HTML vs. rendered DOM content diff** | Text content present in the rendered DOM (after JS execution) but absent from raw HTML source. Focus on key facts: pricing, product names, descriptions, specs, contact info, hours, FAQs, reviews. Severity escalated if key facts are JS-dependent (see `references/key-fact-patterns.json`). |
| **RE-02** | **Client-side routing / SPA shell** | Pages that return a near-empty HTML shell (`<div id="root"></div>`) with all content injected by JS frameworks (React, Vue, Angular, Svelte, Next, Nuxt, Gatsby, Astro, Remix). Check for `<noscript>` fallback presence and quality. Framework detected via `references/opaque-media-signatures.json`. |
| **RE-03** | **AJAX / fetch-dependent content blocks** | DOM sections populated by asynchronous API calls after initial page load. Detected by comparing text length at DOMContentLoaded vs. network-idle. Large deltas indicate content that arrives late and may be missed by crawlers that don't wait. |

### Group B — Opaque Media (Facts locked in non-text formats)

| ID | Check | What to look for |
|----|-------|------------------|
| **RE-04** | **Images without text alternatives** | `<img>`, `<picture>`, CSS `background-image` carrying information with no `alt`, empty `alt=""`, generic `alt` (e.g., "image", "photo"), no `aria-label`, no `<figcaption>`, no surrounding text equivalent. Alt quality assessed per `references/render-checks-detail.md` §4. Decorative images suppressed. |
| **RE-05** | **SVG with embedded text** | `<svg>` elements containing `<text>`, `<tspan>`, or `<foreignObject>` with meaningful text that has no equivalent outside the SVG. Inline SVGs checked in raw HTML. |
| **RE-06** | **Canvas / WebGL without fallback** | `<canvas>` elements used for content (not decoration) with no fallback content inside the tag and no accessible text nearby. Background effects/animations suppressed. |
| **RE-07** | **Video / audio without transcript** | `<video>` and `<audio>` carrying information with no `<track kind="captions">`, no `<track kind="subtitles">`, and no nearby transcript link. Background videos (autoplay + muted + loop + no controls) suppressed. |
| **RE-08** | **PDF-only facts** | Links to `.pdf` files where the linking page provides no HTML summary of the PDF's content. Severity depends on whether the PDF likely contains key facts. |
| **RE-09** | **Icon fonts / CSS pseudo-element text** | Meaningful text conveyed via icon font ligatures (Material Icons risk: element text = icon name) or CSS `::before`/`::after` `content` property with real words. Detection uses `references/opaque-media-signatures.json`. |

### Group C — DOM-Present but Interaction-Gated

| ID | Check | What to look for |
|----|-------|------------------|
| **RE-10** | **Tabs / accordions with hidden content** | Content inside tabbed interfaces or accordions that is either: (a) absent from the DOM until clicked (JS-loaded → high severity), or (b) present in DOM but CSS-hidden (`display: none`, `aria-hidden="true"` → low severity). Distinguished using rendered DOM analysis. |
| **RE-11** | **Shadow DOM with critical content** | Custom web components using Shadow DOM where key content lives inside the shadow tree, inaccessible to `querySelector` on the main document. Detected via Playwright `shadowRoot` scan. |
| **RE-12** | **Iframe-embedded content** | Key content loaded via `<iframe>`. Cross-origin iframes are higher severity. Suppressed for non-content iframes (analytics, ads, social, chat) per `references/render-config.json`. |
| **RE-13** | **Infinite scroll / load-more gated content** | Pages where the initial DOM contains only a partial listing and more loads on scroll or "Load More" click. Detected via button text patterns, sentinel elements, and pagination metadata. |

### Group D — Signal-to-Noise

| ID | Check | What to look for |
|----|-------|------------------|
| **RE-14** | **Text-to-boilerplate ratio** | Pages where navigation, footer, sidebar, legal text dominate — unique main content is < 30% of total text. Uses `<main>`, `<article>`, `role="main"` to isolate content vs. boilerplate. Algorithm in `references/render-checks-detail.md` §5. |

---

## Procedure

> **Runtime target:** < 2 minutes for a typical site (homepage + up to 10 pages).

### Step 1 — Fetch raw HTML and perform static analysis

Run the bundled script:

```bash
python3 scripts/html_fetcher.py --url <site_root_url> [--pages <paths>] [--max-pages 10]
```

From the JSON output, extract per page:
- `raw_text` and `raw_text_length` — baseline for JS diff
- `spa_detection` — framework, shell status, noscript quality
- `text_ratio` — boilerplate analysis
- `images`, `svgs`, `canvases`, `videos`, `audios`, `iframes`, `pdf_links` — media inventory
- `tabs_accordions`, `infinite_scroll_markers` — interaction-gated patterns
- `icon_font_elements`, `css_content_declarations` — pseudo-text patterns
- `key_facts_in_raw_text` — facts already extractable without JS

Save the output to a temporary file for Step 2.

### Step 2 — Render pages in headless browser

Run the bundled script, passing the raw data for diff computation:

```bash
python3 scripts/rendered_dom_extractor.py --url <site_root_url> [--pages <paths>] [--max-pages 10] [--raw-data <path_to_step1_output.json>]
```

From the JSON output, extract per page:
- `rendered_text` and `rendered_text_length` — post-JS text
- `js_content_diff` — text present only after JS execution
- `ajax_content_delta` — text that arrived between DOMContentLoaded and network-idle
- `shadow_dom_components` — shadow DOM hosts with inner text
- `hidden_panels` — tab/accordion panels that are hidden
- `lazy_loaded_images` — images with lazy loading
- `load_more_patterns` — infinite scroll / load-more buttons

### Step 3 — Generate findings from analysis

For each page, compare Step 1 and Step 2 outputs to generate findings:

**RE-01 (JS rendering gap):**
- If `js_content_diff.js_dependent_text_length` > 50 chars: flag.
- Run key-fact detection on `js_dependent_text`. Escalate severity if key facts found.
- Severity: high if key facts, medium if general content.

**RE-02 (SPA shell):**
- If `spa_detection.is_shell` is true: flag.
- Severity: high if `noscript_quality` is "missing" or "insufficient", medium if noscript is "adequate".

**RE-03 (AJAX-loaded content):**
- If `ajax_content_delta` > 500 chars: flag.
- Severity: medium (content arrives late, fragile for crawlers).

**RE-04 (Images without alt):**
- For each image where `is_decorative` is false AND `alt_quality` is "missing", "empty", "generic", or "filename": flag.
- Severity: per key-fact escalation (check if image context suggests key facts) or medium default.

**RE-05 (SVG text):**
- For each SVG without `aria_label` and without adjacent text equivalent: flag.
- Severity: medium (exact content unknown without rendered inspection).

**RE-06 (Canvas without fallback):**
- For each canvas where `has_fallback_content` is false AND not decorative: flag.
- Severity: high if context suggests content (id/class contains "chart", "product", "config").

**RE-07 (Video/audio without transcript):**
- For each video/audio where `is_background` is false AND `has_track_captions` is false AND `has_transcript_link` is false: flag.
- Severity: high (information locked in media).

**RE-08 (PDF-only facts):**
- For each PDF link where surrounding text is thin (<50 chars summary): flag.
- Severity: medium default, high if link text suggests key facts.

**RE-09 (Icon fonts / CSS pseudo-text):**
- For icon fonts with `ligature_risk` true: flag (pollutes extracted text).
- For CSS content declarations with meaningful text: flag.
- Severity: low (noise pollution, not content hiding).

**RE-10 (Tabs/accordions):**
- For each `hidden_panels` entry where `isCssHidden` is true: low severity (content in DOM).
- For tabs/accordions detected in Step 1 but with NO matching rendered DOM content: high severity (content absent from DOM).

**RE-11 (Shadow DOM):**
- For each shadow DOM component with `innerTextLength` > 50: flag.
- Severity: high if text contains key facts, medium otherwise.

**RE-12 (Iframes):**
- For each iframe where `is_suppressed` is false: flag.
- Severity: high if cross-origin and content-bearing, medium if same-origin, low if same-origin and independently indexable.

**RE-13 (Infinite scroll):**
- If `infinite_scroll_markers` found in Step 1 OR `load_more_patterns` found in Step 2: flag.
- Severity: medium (initial DOM shows partial content).

**RE-14 (Boilerplate ratio):**
- If `text_ratio.ratio` < 0.30: flag.
- If `text_ratio.main_content_identified` is false: flag (medium — can't isolate content).
- Severity: low for ratio < 0.30, medium if no main content area identifiable.

### Step 4 — Apply false-positive suppression

Before finalizing findings, suppress false positives using the rules in
`references/render-checks-detail.md` §8:
- Don't flag decorative images (`role="presentation"`, `aria-hidden="true"`, tracking pixels).
- Don't flag screen-reader-only hidden text (`.sr-only`, `.visually-hidden`) — this is GOOD practice.
- Don't flag JS-dependent content if `<noscript>` provides an adequate equivalent.
- Don't flag PDF links with adequate HTML summary (>50 chars).
- Don't flag icon fonts used for decorative/navigational icons.
- Don't flag tabs/accordions where hidden content IS in the DOM (just CSS-hidden) — downgrade to low.
- Don't flag iframes from suppressed sources (analytics, ads, social, chat widgets).
- Don't flag canvas used for background effects (no controls, decorative classes).
- Don't flag background videos (autoplay + muted + loop + no controls).

### Step 5 — Compile and return findings

Assemble all findings into the output schema below. Assign a unique `finding_id`
(`re-001`, `re-002`, ...) to each. Sort by severity (critical → info). Return
the findings list to the audit orchestrator.

---

## Output Schema (per finding)

```json
{
  "finding_id": "re-001",
  "skill": "render-extraction-audit",
  "severity": "critical",
  "title": "Product pricing is embedded in an image with no alt text",
  "detail": "The pricing table on /pricing is an <img> (pricing-table.png) with alt=\"\". A machine reader cannot extract any pricing data from this page. 3 product images on the page lack text alternatives.",
  "affected_urls": ["/pricing"],
  "recommendation": "Convert the pricing table from an image to HTML <table> markup. If an image must be used, provide comprehensive alt text describing all pricing tiers. Also add Product/Offer JSON-LD structured data for machine-readable pricing."
}
```

**Required fields per finding:** `finding_id`, `skill`, `severity`, `title`, `detail`, `affected_urls`, `recommendation`.

---

## Severity Guide

| Severity | Meaning | Examples |
|----------|---------|----------|
| **critical** | Key facts (pricing, contact, product name) exist only in opaque media with zero text alternative. Information is completely invisible to machines. | Pricing table as image with no alt; contact info only in a video with no transcript; product specs in a canvas widget. |
| **high** | Key facts are JS-rendered only (invisible to simple fetchers) OR entire page is an SPA shell with no SSR/noscript fallback. | Product descriptions rendered by React with no SSR; pricing loaded via AJAX only; FAQ section is JS-dependent; cross-origin iframe with booking widget. |
| **medium** | Non-critical content is JS-only, text alternatives exist but are incomplete/inaccurate, or content is gated behind interaction (tabs/accordions not in DOM). AJAX content delta is large. | Blog content partially JS-dependent; image alt is generic; product specs in a tab not in initial DOM; content behind "Load More" button; no main content area identifiable. |
| **low** | Text-to-boilerplate ratio is poor, CSS-hidden-but-DOM-present content, icon font ligature noise, or minor fallback quality issues. | Page has 80% boilerplate; accordion content in DOM but CSS-hidden; Material Icons injecting ligature text; noscript provides partial fallback; PDF link with thin summary. |
| **info** | Observation with no immediate action needed, or a positive signal. | SSR detected (content available without JS); all images have descriptive alt text; video has captions track; good text-to-boilerplate ratio. |

See `references/render-checks-detail.md` for the full severity decision tree and override rules.
