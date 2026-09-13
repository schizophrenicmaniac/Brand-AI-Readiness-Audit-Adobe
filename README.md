# Brand AI Readiness Audit

[![Adobe University Hackathon 2026](https://img.shields.io/badge/Adobe%20University%20Hackathon-2026-FF0000?style=flat&logo=adobe&logoColor=white)](https://unstop.com/hackathons/crp-adobe-university-hackathon-2026-adobe-1715333)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![Status](https://img.shields.io/badge/Status-Shippable%20%7C%20Verified-success?style=flat)](#verification--testing)
[![Standard](https://img.shields.io/badge/Standard-agentskills.io%20v1.0-blueviolet?style=flat)](https://agentskills.io/)

> **Submission for CRP - Adobe University Hackathon 2026**  
> **Team Name:** **Kasukabe Defence Group**  
> **Team Members:**
> * **Divyansh Yadav** (Team Leader)
> * **Shaurya Dwivedi**
> * **Atharva Ajmera**

---

## Executive Overview

The web is undergoing a structural shift from traditional search engines (ranked keyword links) to **autonomous AI agents and generative answer engines** (ChatGPT Search, Perplexity, Google AI Overviews, Claude). When an AI agent explores the web on behalf of a user, it evaluates websites through a fundamentally different lens than a human visitor.

If a site restricts AI crawlers in `robots.txt`, leaves critical facts trapped inside client-side JavaScript bundles, lacks machine-readable JSON-LD Schema.org entities, or has no `/llms.txt` orientation manifest, **the brand becomes invisible or misconstrued by AI models**.

**Brand AI Readiness Audit** is a production-grade, modular suite of Agent Skills built to the **agentskills.io** open standard. Given any domain or URL, it coordinates five domain-specific detection skills across a single safe, cached fetch pipeline and produces an evidence-backed audit report with prioritized (P0–P3), actionable remediations.

---

## System Architecture

```
                                [ Target URL / Domain ]
                                           │
                                           ▼
                       ┌──────────────────────────────────────┐
                       │      audit-orchestrator (CLI)        │
                       │    URL Normalization & SSRF Check    │
                       └───────────────────┬──────────────────┘
                                           │
                           Single Safe Cached Fetch Pass
                                           │
         ┌──────────────────┬──────────────┴─────┬──────────────────┬─────────────────┐
         ▼                  ▼                    ▼                  ▼                 ▼
 ┌───────────────┐  ┌───────────────┐    ┌───────────────┐  ┌───────────────┐ ┌───────────────┐
 │ crawl-access  │  │ render-       │    │ structured-   │  │ freshness-    │ │ engagement-   │
 │ audit         │  │ extraction    │    │ data-audit    │  │ corroboration │ │ audit         │
 │               │  │ audit         │    │               │  │ audit         │ │               │
 │ • AI robots   │  │ • JS DOM gaps │    │ • JSON-LD org │  │ • Timestamps  │ │ • Viewport    │
 │ • Sitemap XML │  │ • Shell/SPA   │    │ • Schema types│  │ • sameAs refs │ │ • <nav> mark  │
 │ • HTTP->HTTPS │  │ • Alt text    │    │ • OpenGraph/X │  │ • Wikidata    │ │ • <h1> hierarchy│
 │ • Bot barriers│  │ • Boilerplate │    │ • llms.txt    │  │ • Staleness   │ │ • CTA density │
 └───────┬───────┘  └───────┬───────┘    └───────┬───────┘  └───────┬───────┘ └───────┬───────┘
         │                  │                    │                  │                 │
         └──────────────────┴──────────────┬─────┴──────────────────┴─────────────────┘
                                           │
                                           ▼
                       ┌──────────────────────────────────────┐
                       │          lib/report Contract         │
                       │  • Deduplication & Repeat Collapsing │
                       │  • Severity/Priority Sort (P0 -> P3) │
                       │  • Strict JSON Schema Validation     │
                       └───────────────────┬──────────────────┘
                                           │
                                  ┌────────┴────────┐
                                  ▼                 ▼
                           [ report.json ]   [ report.md ]
```

### Core Design Principles

1. **Shared Single-Pass Network Layer (`lib/safe_http.py`)**:  
   All HTTP requests pass through an SSRF-hardened session. Private IPs (RFC 1918, cloud metadata endpoints, loopbacks) are blocked before and across redirects. Responses are cached per origin, guaranteeing that pages are fetched once across all skills, preventing origin hammering.
2. **Deterministic Contract (`lib/report.py`)**:  
   Findings conform to a strict JSON Schema Draft-07 specification. Each finding contains a deterministic, hash-based ID (`<skill>-<hash>`) that remains stable across multiple runs.
3. **Graceful Degradation**:  
   Headless browser rendering (Playwright) is automatically probed. If Playwright is not installed, the engine gracefully falls back to static AST heuristics without throwing exceptions or generating noisy logs.
4. **Safety & Zero-Side-Effect Guarantee**:  
   The audit engine never submits forms, never writes state to the target, and automatically excludes authentication, cart, checkout, or account action paths (`/login`, `/cart`, `/checkout`, `/admin`).

---

## Detection Skills Catalog

| Skill | Directory | Core Audit Capabilities |
| :--- | :--- | :--- |
| **audit-orchestrator** | `skills/audit-orchestrator/` | Central driver: handles URL normalization, safe fetch caching, sitemap-guided page sampling, deduplication, and schema validation. |
| **crawl-access-audit** | `skills/crawl-access-audit/` | Evaluates crawler accessibility: AI bot permissions (`GPTBot`, `ClaudeBot`, `PerplexityBot`), XML sitemaps, HTTP->HTTPS canonicalization, HSTS, and response latency. |
| **render-extraction-audit** | `skills/render-extraction-audit/` | Analyzes machine extraction: client-side JavaScript dependency, empty SPA shells, media accessibility (image `alt` tags), and content-to-boilerplate ratio. |
| **structured-data-audit** | `skills/structured-data-audit/` | Verifies machine entity grounding: JSON-LD Schema.org markup (`Organization`, `WebSite`, `Product`, `Article`), Open Graph metadata, Twitter Cards, and `/llms.txt`. |
| **freshness-corroboration-audit** | `skills/freshness-corroboration-audit/` | Evaluates content temporal freshness: date published/modified headers, stale copyright notices, and external knowledge grounding via Wikidata / Wikipedia entity links. |
| **engagement-audit** | `skills/engagement-audit/` | Audits post-referral visitor retention: mobile responsive viewport configuration, semantic `<nav>` landmarks, heading hierarchy (`<h1>`), and CTA density. |

Each skill contains a valid [SKILL.md](https://agentskills.io/) defining inputs, outputs, error handling, and standalone runnable scripts.

---

## Quickstart & Usage

### 1. Installation

Clone the repository and install the standard dependencies:

```bash
git clone https://github.com/schizophrenicmaniac/Brand-AI-Readiness-Audit-Adobe.git
cd Brand-AI-Readiness-Audit-Adobe

pip install -r requirements.txt
```

*(Playwright is completely optional for headless rendering. If omitted, the audit automatically runs high-accuracy static heuristics).*

### 2. Running an Audit

Run the orchestrator against any website:

```bash
python skills/audit-orchestrator/scripts/run_audit.py https://example.com
```

#### CLI Options

```bash
python skills/audit-orchestrator/scripts/run_audit.py <URL> [OPTIONS]

Options:
  --max-pages INTEGER      Maximum pages to sample (default: 8)
  --budget-seconds INTEGER Max total execution budget (default: 240s)
  --out-dir PATH           Output directory (default: current directory)
  --allow-private          Allow private IP ranges (for local dev testing only)
```

Outputs generated:
* `report.json` — Comprehensive, schema-validated JSON data contract.
* `report.md` — Professional, human-readable executive summary and action plan.

### 3. Running Standalone Skills

Every skill can also be executed independently on a single URL:

```bash
# Example: Run only Crawl Access Audit
python skills/crawl-access-audit/scripts/crawl_analyzer.py --url https://example.com

# Example: Run only Structured Data Audit
python skills/structured-data-audit/scripts/schema_validator.py --url https://example.com
```

---

## Verification & Testing

The repository includes a comprehensive, deterministic offline test suite covering all analyzers, contracts, schema validators, and SSRF guards:

```bash
python test_audit.py
```

Expected Output:
```text
  ok  test_combined_report_validates
  ok  test_crawl_analyzer
  ok  test_engagement_analyzer
  ok  test_fp_regressions
  ok  test_freshness_fp_fixes
  ok  test_render_analyzer_static
  ok  test_report_contract
  ok  test_ssrf_guard
  ok  test_structured_validator_gating_and_contract
ALL TESTS PASSED
```

---

## Report Data Contract

Every generated finding adheres to the uniform schema:

```json
{
  "id": "sd-4c69f0",
  "title": "Zero JSON-LD structured data detected across website",
  "severity": "critical",
  "skill": "structured-data-audit",
  "evidence": {
    "url": "https://example.com/",
    "source": "html",
    "locator": "script[type=application/ld+json]",
    "observed": "None of the audited pages contain Schema.org markup.",
    "expected": "Organization | WebSite"
  },
  "suggested_action": {
    "summary": "Implement baseline JSON-LD schema starting with Organization on the homepage.",
    "priority": "P0"
  }
}
```

### Priority Mapping

* **P0 (Critical)**: Immediate blockers for machine discovery or brand safety (e.g. AI crawlers blocked, zero schema).
* **P1 (High)**: Major impact on AI visibility, citation, or conversion.
* **P2 (Medium)**: Material friction in crawling, freshness, or schema depth.
* **P3 (Low / Info)**: Code hygiene, minor metadata, or strategic proactive opportunities (e.g. `llms.txt`).

---

## Hackathon Submission Details

* **Hackathon:** [CRP - Adobe University Hackathon 2026 (Adobe)](https://unstop.com/hackathons/crp-adobe-university-hackathon-2026-adobe-1715333)
* **Team:** **Kasukabe Defence Group**
* **Repository:** [Brand-AI-Readiness-Audit-Adobe](https://github.com/schizophrenicmaniac/Brand-AI-Readiness-Audit-Adobe)
* **License:** Apache 2.0
