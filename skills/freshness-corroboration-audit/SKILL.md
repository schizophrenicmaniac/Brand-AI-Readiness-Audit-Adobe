---
name: freshness-corroboration-audit
description: >
  Evaluates whether key facts are fresh, externally corroborated, and
  unambiguously attributable to this entity — the trust and disambiguation
  layer from Appendix D. Checks HTTP and DOM temporal metadata (Last-Modified,
  datePublished, dateModified), content staleness signals (outdated pricing years,
  past-tense future events, dead outbound links, stale copyright), external
  multi-source corroboration of core claims, entity disambiguation (sameAs links,
  NAP consistency, brand collisions), and Wikipedia/Wikidata grounding anchors.
license: MIT
tools:
  - run_command
  - read_url_content
  - search_web
  - view_file
---

# Freshness & Corroboration Audit

> **Diagnostic question:** *Is this fact trustworthy, cross-confirmed, and unambiguous?*

This skill maps to **Appendix D** of the Brand AI Readiness framework. It
checks whether the site's claims are current, verifiable from external sources,
and tied to a clearly disambiguated entity. Both isolation (a fact stated
nowhere else) and ambiguity (a name that collides with other entities) reduce
the odds an AI assistant will repeat the fact confidently in zero-shot answers.

---

## When to Use

Invoke this skill when:
- Auditing a website's readiness for AI-driven discovery, summarization, and citation.
- Diagnosing why an AI assistant gives outdated pricing, conflates the brand with another entity, or hesitates to cite core company facts.
- The audit orchestrator invokes it as **step 4** in the pipeline (after `crawl-access-audit`, `render-extraction-audit`, and `structured-data-audit`).

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
| **FC-01** | **Last-modified / published dates** | Missing date signals across key pages, or dates that are visibly stale relative to content (e.g., pricing pages older than 24 months, active pages older than 12 months). Checks `Last-Modified` HTTP header, `<meta>` date tags, JSON-LD `datePublished` / `dateModified`, and visible date bylines. |
| **FC-02** | **Content staleness signals** | References to past events as upcoming (e.g., "upcoming in 2022"), outdated pricing year designations (e.g., "2021 pricing"), stale footer copyright years (>2 years behind), and broken outbound reference links (404/410/connection errors). |
| **FC-03** | **External corroboration** | Whether core claims (founding date, headquarters location, flagship products, pricing, leadership) appear consistently across independent authoritative sources (Bloomberg, Crunchbase, LinkedIn, G2, news media). Claims appearing only on the brand's site are flagged as "isolated." |
| **FC-04** | **Entity disambiguation** | Whether the brand name collides with other entities, acronyms, or dictionary terms. Evaluates presence and diversity of `sameAs` schema links (Wikipedia, Wikidata, LinkedIn, Crunchbase), and validates consistent NAP (Name-Address-Phone) identifiers for local entities. |
| **FC-05** | **Wikipedia / Wikidata presence** | Whether the entity has an active English Wikipedia article or Wikidata item (`QID`), which AI search engines (Perplexity, Google SGE, ChatGPT Search) use as grounding anchors for entity resolution. |

---

## Procedure

> **Runtime target:** < 1 minute for a typical site (homepage + up to 10 pages).

### Step 1 — Extract freshness and entity metadata

Run the bundled extractor script:

```bash
python scripts/freshness_extractor.py --url <site_root_url> [--pages <paths>] [--max-pages 10] --output /tmp/freshness-raw.json
```

From the JSON output, verify extraction of:
- `date_signals`: HTTP headers (`Last-Modified`), meta dates, schema dates, visible dates, and freshest timestamp.
- `staleness_markers`: outdated pricing keywords, future-tense past events, stale copyright, and dead outbound links.
- `entity`: detected brand name, aggregated claims (founding date, HQ, leadership, products, pricing), and `sameAs` links.
- `grounding`: Wikipedia search results and Wikidata entity status.
- `corroboration_templates`: search query templates for external claim verification.

### Step 2 — Cross-reference core claims via web search (FC-03)

If the entity has extracted core claims, the agent executes `search_web` using the query templates in `corroboration_templates` to verify:
1. Does the claim appear on at least one independent third-party domain?
2. Are external values consistent with site claims?

Save any verified corroboration results to `/tmp/corroboration.json` or pass directly to the validator.

### Step 3 — Validate freshness and disambiguation

Run the bundled validator script:

```bash
python scripts/freshness_validator.py --raw-data /tmp/freshness-raw.json [--corroboration-data /tmp/corroboration.json] --output /tmp/freshness-findings.json
```

The script evaluates checks **FC-01** through **FC-05** and outputs standardized findings.

### Step 4 — Apply false-positive suppression

Before finalizing findings, verify suppression rules in `references/freshness-checks-detail.md`:
- Do not flag missing dates on evergreen content (`/about`, `/mission`, `/privacy-policy`, `/terms`).
- Do not flag archived historical blog posts or press releases with clear past publication dates.
- Do not escalate missing Wikipedia presence beyond `low` (or suppress) for small/local businesses that do not meet Wikipedia notability standards. Focus on Wikidata and Crunchbase.
- Suppress date and staleness checks on utility and auth paths (`/login`, `/signup`, `/cart`, `/checkout`).
- Suppress name collision warnings for distinctive, coined, or trademarked brand names.

### Step 5 — Compile and return findings

Assemble all findings into the standard output schema below. Ensure unique `finding_id`
(`fc-001`, `fc-002`, ...), sort by severity (`critical` → `high` → `medium` → `low` → `info`),
and return the findings list to the audit orchestrator.

---

## Output Schema (per finding)

```json
{
  "finding_id": "fc-001",
  "skill": "freshness-corroboration-audit",
  "severity": "critical",
  "title": "Commercial pricing page dates exceed 24 months staleness threshold",
  "detail": "The /pricing page shows 'Starting at $29/mo' but has no published/modified date since 2023. AI assistants cannot confirm pricing accuracy.",
  "affected_urls": ["/pricing"],
  "recommendation": "Add dateModified in JSON-LD and include a visible 'Last updated' stamp to confirm pricing is current."
}
```

**Required fields per finding:** `finding_id`, `skill`, `severity`, `title`, `detail`, `affected_urls`, `recommendation`.

---

## Severity Guide

| Severity | Meaning | Examples |
|----------|---------|----------|
| **critical** | Core commercial or identity claims appear to be stale and are not corroborated anywhere else. | Pricing page dates >24 months old with outdated pricing copy; discontinued flagship product still promoted without disambiguation. |
| **high** | Brand name collides with another well-known entity without `sameAs` markup; commercial pages lack date signals; or core identity claims are completely isolated. | Brand name is a common English word with no `sameAs` schema; `/pricing` has zero date signals; past events described as future ("upcoming in 2023"). |
| **medium** | Key pages lack temporal metadata, or facts are corroborated but inconsistently stated across sources. | Service pages older than 12 months; multiple broken outbound reference links; company founded date conflicts between site and external sources; incomplete NAP for local business. |
| **low** | Minor staleness hygiene or knowledge graph gaps for non-enterprise entities. | Stale copyright year in footer; server does not emit `Last-Modified` header; entity lacks Wikipedia article but has Wikidata; single dead outbound link. |
| **info** | Observation or positive verification signal. | Active timestamps (<30 days) verified; strong knowledge graph grounding on Wikipedia and Wikidata; rich `sameAs` profile array detected. |

See `references/freshness-checks-detail.md` for the full severity decision tree, corroboration scoring rubrics, and override rules.
