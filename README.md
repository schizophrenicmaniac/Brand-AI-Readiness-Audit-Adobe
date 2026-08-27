# Brand AI Readiness Audit

A marketplace of Agent Skills that audit a website's readiness for AI-driven discovery, citation, and engagement.

## Skills

| Skill | Purpose |
|-------|---------|
| **audit-orchestrator** | Entrypoint that invokes all five detection skills in sequence, deduplicates findings, and assembles the final JSON report. |
| **crawl-access-audit** | Checks whether AI crawlers can reach, fetch, and index the site's pages (robots.txt, sitemaps, status codes, redirects, canonicals, latency). |
| **render-extraction-audit** | Detects content that is visually present to humans but invisible to machine readers (JS-only rendering, image-locked text, missing transcripts, poor text-to-boilerplate ratio). |
| **structured-data-audit** | Evaluates explicit machine-readable markup — JSON-LD/schema.org, Open Graph, meta tags, llms.txt — that hands facts to AI systems pre-labeled. |
| **freshness-corroboration-audit** | Assesses whether key facts are current, externally corroborated by independent sources, and unambiguously tied to this entity (not a name collision). |
| **engagement-audit** | Evaluates on-site experience for visitors arriving via AI referrals — navigation clarity, orientation cues, load performance, mobile readiness, CTAs, and context retention. |

## Architecture

```
audit-orchestrator (entrypoint)
 ├─ 1. crawl-access-audit           ← Can the crawler get in?
 ├─ 2. render-extraction-audit      ← Can the machine read what's there?
 ├─ 3. structured-data-audit        ← Are facts pre-labeled?
 ├─ 4. freshness-corroboration-audit ← Is it fresh and cross-confirmed?
 └─ 5. engagement-audit             ← Do visitors stay once they land?
```

Skills 1–4 form a pipeline mirroring Appendices A→D (reachable → readable → labeled → trusted). Skill 5 covers the on-site engagement layer. The orchestrator owns composition, deduplication, and the report contract.

Each skill follows the `SKILL.md` format and includes `scripts/` and `references/` directories.
