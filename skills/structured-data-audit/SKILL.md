---
name: structured-data-audit
description: >
  Evaluates explicit machine-readable markup (JSON-LD, Open Graph, Twitter cards,
  meta tags, llms.txt) that hands facts to AI systems pre-labeled — the difference
  between "extractable with effort" and "citation-ready" from Appendix C. Checks
  JSON-LD presence by page type (Organization, Product, Article, FAQ, Breadcrumbs),
  syntactic and semantic validity, required/recommended property completeness,
  anti-pattern and placeholder detection, Open Graph and Twitter card preview tags,
  title and meta description quality, llms.txt presence, and cross-references
  structured markup against visible DOM content.
license: MIT
tools:
  - run_command
  - read_url_content
  - view_file
---

# Structured-Data Audit

> **Diagnostic question:** *Are facts handed to the machine pre-labeled?*

This skill maps to **Appendix C** of the Brand AI Readiness framework. It
addresses a distinct concern from render-extraction: not whether text is
*physically readable* in the rendered DOM, but whether facts are **explicitly
structured and semantically typed** so an AI crawler or LLM reasoning system can
consume and cite them without ambiguity.

A site with valid structured data enables zero-shot entity grounding, automated
pricing verification, and rich conversational citations (Appendix B).

---

## When to Use

Invoke this skill when:
- Auditing a website's readiness for AI-driven discovery, summarization, and citation.
- Diagnosing why an AI assistant gives incomplete or ungrounded answers about brand identity, products, or pricing.
- The audit orchestrator invokes it as **step 3** in the pipeline (after `crawl-access-audit` and `render-extraction-audit`).

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

### Group A — Schema.org / JSON-LD Markup

| ID | Check | What to look for |
|----|-------|------------------|
| **SD-01** | **JSON-LD presence by page type** | Whether pages carry appropriate Schema.org types: homepage (`Organization`, `WebSite`, `LocalBusiness`), product pages (`Product`, `Offer`), editorial articles (`Article`, `BlogPosting`), FAQ/support (`FAQPage`), and interior pages (`BreadcrumbList`). See `references/schema-type-registry.json`. |
| **SD-02** | **JSON-LD syntactic & semantic validity** | JSON syntax errors, unescaped characters, missing `@context` (`https://schema.org`), missing `@type`, missing required properties (e.g. `price` in `Offer`, `name` in `Organization`), and dummy/placeholder strings (e.g. "Lorem Ipsum", "TODO", "example.com"). |

### Group B — Social Preview Metadata

| ID | Check | What to look for |
|----|-------|------------------|
| **SD-03** | **Open Graph protocol tags** | `og:title`, `og:description`, `og:image`, `og:url`, `og:type` — presence, completeness, non-empty values, valid Open Graph types, and fully-qualified HTTPS `og:image` URLs. |
| **SD-04** | **Twitter / X Card tags** | `twitter:card`, `twitter:title`, `twitter:description`, `twitter:image` — valid card type (`summary_large_image`, `summary`) and completeness. |

### Group C — Document Metadata

| ID | Check | What to look for |
|----|-------|------------------|
| **SD-05** | **Title, meta description & favicon** | `<title>` presence and length (10–65 characters), avoidance of generic boilerplate titles ("Home", "Untitled"), `<meta name="description">` length (50–165 characters), and presence of `<link rel="icon">`. |

### Group D — Machine-Facing Summaries

| ID | Check | What to look for |
|----|-------|------------------|
| **SD-06** | **llms.txt / machine-facing summary** | Presence and structure of `/llms.txt` at domain root. Evaluates whether it contains markdown headings (`#`, `##`), a concise blockquote summary, and links to authoritative documentation. |

### Group E — Content Consistency & Grounding

| ID | Check | What to look for |
|----|-------|------------------|
| **SD-07** | **Schema vs. visible DOM cross-reference** | Discrepancies between values declared in Schema.org JSON-LD (e.g. `Product.name`, `price`, `headline`) and visible content displayed in `<h1>` or body text. |

---

## Procedure

> **Runtime target:** < 1 minute for a typical site (homepage + up to 10 pages).

### Step 1 — Extract structured data

Run the bundled extractor script:

```bash
python scripts/structured_data_extractor.py --url <site_root_url> [--pages <paths>] [--max-pages 10] --output /tmp/structured-data-raw.json
```

From the JSON output, verify extraction of:
- `json_ld.blocks`: raw JSON-LD blocks, parse errors, and parsed objects
- `open_graph`: dictionary of extracted Open Graph tags
- `twitter_card`: dictionary of Twitter Card tags
- `meta`: title, description, canonical, and favicon links
- `visible_cues`: visible `<h1>`, headings, detected prices, and sample text
- `llms_txt`: presence and structure of `/llms.txt`

### Step 2 — Validate schema and metadata

Run the bundled validator script:

```bash
python scripts/schema_validator.py --raw-data /tmp/structured-data-raw.json --output /tmp/structured-data-findings.json
```

The script evaluates checks **SD-01** through **SD-07** and outputs standardized findings.

### Step 3 — Apply false-positive suppression

Before finalizing findings, verify suppression rules in `references/structured-checks-detail.md`:
- Do not flag missing `Product` schema on pages that lack commerce or purchase intent.
- Do not flag missing `FAQPage` markup unless the page contains distinct Q&A formatting.
- Do not escalate missing `llms.txt` beyond `low` severity.
- Do not flag missing social preview tags on utility pages (`/login`, `/privacy-policy`, `/terms`).
- Do not flag short titles if the brand name is concise and clearly self-identifying.

### Step 4 — Compile and return findings

Assemble all findings into the standard output schema below. Ensure unique `finding_id`
(`sd-001`, `sd-002`, ...), sort by severity (`critical` → `high` → `medium` → `low` → `info`),
and return the findings list to the audit orchestrator.

---

## Output Schema (per finding)

```json
{
  "finding_id": "sd-001",
  "skill": "structured-data-audit",
  "severity": "high",
  "title": "No Organization schema on homepage",
  "detail": "The homepage has no JSON-LD Organization or WebSite schema. AI assistants cannot reliably extract the company name, logo, or official social profiles.",
  "affected_urls": ["/"],
  "recommendation": "Add a JSON-LD block with @type Organization including name, url, logo, and sameAs properties."
}
```

**Required fields per finding:** `finding_id`, `skill`, `severity`, `title`, `detail`, `affected_urls`, `recommendation`.

---

## Severity Guide

| Severity | Meaning | Examples |
|----------|---------|----------|
| **critical** | Structured data is completely absent across the domain, or JSON-LD is so corrupted that parsers crash. | Zero JSON-LD on entire website; fatal syntax corruption in main schema block. |
| **high** | Critical entity or commercial markup is missing, required fields are absent, or schema contradicts visible DOM. | Missing `Organization` on homepage; missing `Offer` / price on product page; malformed JSON in `<script type="application/ld+json">`; schema price differs from visible `<h1>`/price. |
| **medium** | Key recommended properties are missing, or social preview metadata is completely absent. | Missing `BreadcrumbList` on deep catalog pages; missing Open Graph tags (`og:image`, `og:title`); generic boilerplate `<title>` tag. |
| **low** | Minor metadata hygiene issues or emerging machine standards. | Missing `/llms.txt`; meta description slightly too short (<50 chars); title slightly over 65 chars; missing `twitter:creator`. |
| **info** | Observation or positive verification signal. | Valid `Organization` and `WebSite` markup detected; rich `FAQPage` schema present. |

See `references/structured-checks-detail.md` for the full severity decision tree, schema validation matrix, and override rules.
