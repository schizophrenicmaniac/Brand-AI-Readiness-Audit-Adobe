---
name: freshness-corroboration-audit
description: >
  Evaluates whether key facts are fresh, externally corroborated, and
  unambiguously attributable to this entity — the trust and disambiguation
  layer from Appendix D.
license: MIT
---

# Freshness & Corroboration Audit

> **Diagnostic question:** *Is this fact trustworthy, cross-confirmed, and unambiguous?*

This skill maps to **Appendix D** of the Brand AI Readiness framework. It
checks whether the site's claims are current, verifiable from external sources,
and tied to a clearly disambiguated entity. Both isolation (a fact stated
nowhere else) and ambiguity (a name that collides with other entities) reduce
the odds an AI assistant will repeat the fact confidently.

---

## Checks

| # | Check | What to look for |
|---|-------|------------------|
| 1 | **Last-modified / published dates** | Key pages with no date, or dates that are visibly stale relative to the content (e.g., "2022 pricing" on a current product page). `Last-Modified` headers, `datePublished` / `dateModified` in schema, visible bylines. |
| 2 | **Content staleness signals** | References to past events as upcoming, discontinued products still listed, outdated team rosters, dead external links. |
| 3 | **External corroboration** | Whether core facts (company name, founding date, flagship products, key claims) appear consistently when searched via web search. Facts that appear *only* on the brand's own site are "isolated." |
| 4 | **Entity disambiguation** | Whether the brand name collides with other well-known entities. Presence of `sameAs` schema links (Wikipedia, Wikidata, LinkedIn, Crunchbase), consistent NAP (Name-Address-Phone) identifiers for local businesses, a distinguishing description in structured data. |
| 5 | **Wikipedia / Wikidata presence** | Whether the entity has a Wikipedia article or Wikidata entry, which AI systems use as grounding anchors. |

---

## Procedure

1. **For each key page**, extract date signals:
   - `Last-Modified` HTTP header.
   - `<meta>` date tags, `datePublished` / `dateModified` in JSON-LD.
   - Visible dates in the content.
   - Flag pages with no date signal at all, and pages where the date is > 12 months old.
2. **Scan content for staleness markers:** past-tense events described as future, outdated year references, broken outbound links.
3. **Cross-reference core facts via web search.** Pick 3–5 key claims (founding year, HQ location, flagship product name, pricing tier, leadership names). Search each and check whether the same fact appears on at least one independent source. Flag isolated facts.
4. **Check entity disambiguation:**
   - Search the brand name — are there colliding entities in the top results?
   - Check for `sameAs` links in JSON-LD (Wikipedia, Wikidata, social profiles).
   - For local businesses, check NAP consistency across directories.
5. **Compile findings** in the standard schema.

---

## Output Schema (per finding)

```json
{
  "finding_id": "fc-001",
  "skill": "freshness-corroboration-audit",
  "severity": "high",
  "title": "Pricing page has no date and reads as stale",
  "detail": "The /pricing page shows 'Starting at $29/mo' but has no published/modified date. The Wayback Machine shows this text unchanged since 2023.",
  "affected_urls": ["/pricing"],
  "recommendation": "Add dateModified schema and visible 'last updated' text. Confirm pricing is current."
}
```

---

## Severity Guide

| Severity | Meaning |
|----------|---------|
| **critical** | Core claims (pricing, product existence) appear to be stale and are not corroborated anywhere else. |
| **high** | Brand name has a strong collision with another entity and no disambiguating markup exists. |
| **medium** | Key pages lack any date signal, or facts are corroborated but inconsistently stated. |
| **low** | Minor staleness (blog post dates old but content is evergreen), missing `sameAs` links. |
| **info** | Observation with no immediate action needed. |
