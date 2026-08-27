---
name: structured-data-audit
description: >
  Evaluates explicit machine-readable markup (JSON-LD, Open Graph, meta tags,
  llms.txt) that hands facts to AI systems pre-labeled — the difference between
  "extractable with effort" and "citation-ready" from Appendix C.
license: MIT
---

# Structured-Data Audit

> **Diagnostic question:** *Are facts handed to the machine pre-labeled?*

This skill also maps to **Appendix C** but addresses a distinct concern from
render-extraction: not whether text is *present* in the DOM, but whether facts
are **explicitly structured** so a machine can consume them without guesswork.
This directly affects citation quality (Appendix B).

---

## Checks

| # | Check | What to look for |
|---|-------|------------------|
| 1 | **JSON-LD / schema.org presence** | Whether key page types carry appropriate schema (`Organization`, `Product`, `FAQ`, `Article`, `LocalBusiness`, `WebSite`, `BreadcrumbList`, etc.). |
| 2 | **JSON-LD validity** | Syntactic correctness (valid JSON), schema.org type/property compliance, required vs. recommended fields populated. |
| 3 | **Open Graph tags** | `og:title`, `og:description`, `og:image`, `og:type`, `og:url` — present, non-empty, and accurate. |
| 4 | **Twitter / X Card tags** | `twitter:card`, `twitter:title`, `twitter:description`, `twitter:image` — present and correct. |
| 5 | **Favicon & title & meta description** | Favicon exists and loads, `<title>` is descriptive (not generic), `<meta name="description">` is present and meaningful (not truncated boilerplate). |
| 6 | **llms.txt / machine-facing summary** | Whether an `llms.txt` (or equivalent machine-facing summary file) exists at the site root, and if so, whether it is well-formed and informative. |

---

## Procedure

1. **For each key page** (homepage + up to 10 high-value pages):
   a. Extract all `<script type="application/ld+json">` blocks. Parse as JSON. Validate against schema.org expectations for the page type.
   b. Extract all `<meta property="og:*">` and `<meta name="twitter:*">` tags. Check completeness and accuracy.
   c. Extract `<title>`, `<meta name="description">`, and favicon `<link>` tags. Evaluate quality (length, specificity, accuracy).
2. **Check for `llms.txt`** at the site root (`/llms.txt`). If present, parse and evaluate content quality and structure.
3. **Cross-reference structured data with page content.** Flag mismatches (e.g., JSON-LD `Product.name` differs from the visible `<h1>`).
4. **Compile findings** in the standard schema.

---

## Output Schema (per finding)

```json
{
  "finding_id": "sd-001",
  "skill": "structured-data-audit",
  "severity": "high",
  "title": "No Organization schema on homepage",
  "detail": "The homepage has no JSON-LD Organization or WebSite schema. AI assistants cannot reliably extract the company name, logo, or social profiles.",
  "affected_urls": ["/"],
  "recommendation": "Add a JSON-LD block with @type Organization including name, url, logo, and sameAs properties."
}
```

---

## Severity Guide

| Severity | Meaning |
|----------|---------|
| **critical** | No structured data of any kind on pages that strongly warrant it (homepage, product pages). |
| **high** | Structured data present but malformed (invalid JSON, wrong schema type, required fields missing). |
| **medium** | Structured data valid but incomplete (recommended fields missing, OG tags absent). |
| **low** | Minor issues (meta description slightly too long, favicon is low-res, no llms.txt). |
| **info** | Observation with no immediate action needed. |
