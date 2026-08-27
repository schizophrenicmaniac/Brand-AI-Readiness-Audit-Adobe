---
name: render-extraction-audit
description: >
  Detects content that is visually present to a human but structurally invisible
  to a machine reader — the core "plainly visible yet machine-invisible" failure
  mode from Appendix C.
license: MIT
---

# Render-Extraction Audit

> **Diagnostic question:** *Can the machine read what's there?*

This skill maps to **Appendix C** of the Brand AI Readiness framework. It
identifies facts and content that a human visitor can see on the rendered page
but that a simple text extractor (no JS engine, no OCR) would miss entirely.

---

## Checks

| # | Check | What to look for |
|---|-------|------------------|
| 1 | **Raw HTML vs. rendered DOM diff** | Content that only appears after JavaScript execution — client-rendered product descriptions, pricing, FAQs, reviews, etc. |
| 2 | **Image-only facts** | Key information (pricing, specs, hours, contact info, product names) embedded in `<img>`, `<svg>`, or CSS `background-image` with no `alt` text, `aria-label`, or adjacent text equivalent. |
| 3 | **PDF-only facts** | Important content locked inside linked PDFs with no HTML summary or extracted text on the page itself. |
| 4 | **Canvas / WebGL content** | Interactive demos, configurators, or infographics rendered in `<canvas>` with no text fallback. |
| 5 | **Video / audio without transcript** | Key facts communicated via `<video>` or `<audio>` with no transcript, caption track, or text summary nearby. |
| 6 | **Text-to-boilerplate ratio** | Pages where navigation, footer, legal text, and ads dominate — the unique, meaningful content is a small fraction of total text, making extraction noisy. |

---

## Procedure

1. **Fetch raw HTML** for each key page (homepage + up to 10 high-value pages). Extract all visible text from the raw source.
2. **Render the page** in a headless browser. Extract all visible text from the rendered DOM after JS execution completes.
3. **Diff the two extractions.** Identify content present in the rendered DOM but absent from the raw HTML. Flag any JS-dependent content that contains key facts (pricing, specs, contact info, hours).
4. **Scan for opaque media:**
   - `<img>` / `<svg>` / CSS background images → check for `alt`, `aria-label`, surrounding `<figcaption>`.
   - `<canvas>` → check for fallback content inside the tag.
   - `<video>` / `<audio>` → check for `<track kind="captions">`, nearby transcript text, or linked transcript.
   - Links to `.pdf` → note if any HTML summary of the PDF's content exists on the linking page.
5. **Compute text-to-boilerplate ratio** using a heuristic (e.g., content-to-tag ratio, main-content element isolation). Flag pages below threshold.
6. **Compile findings** in the standard schema.

---

## Output Schema (per finding)

```json
{
  "finding_id": "re-001",
  "skill": "render-extraction-audit",
  "severity": "high",
  "title": "Product pricing is JS-rendered only",
  "detail": "The price for 'Widget Pro' appears in the rendered DOM but not in raw HTML. A crawler without JS execution will miss it.",
  "affected_urls": ["/products/widget-pro"],
  "recommendation": "Server-side render pricing content, or provide it in JSON-LD structured data."
}
```

---

## Severity Guide

| Severity | Meaning |
|----------|---------|
| **critical** | Key facts (name, pricing, contact) exist only in opaque media (image, canvas, video) with zero text alternative. |
| **high** | Key facts are JS-rendered only — invisible to simple fetchers. |
| **medium** | Non-critical content is JS-only, or text alternatives exist but are incomplete / inaccurate. |
| **low** | Text-to-boilerplate ratio is poor, making extraction noisy but not impossible. |
| **info** | Observation with no immediate action needed. |
