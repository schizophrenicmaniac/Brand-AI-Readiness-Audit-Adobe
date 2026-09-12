# Brand AI Readiness Audit — adobe.com

*Audited: 2026-09-12T06:40:24.710936+00:00 · Pages audited: 1*

## Summary

- **Total findings:** 3
- 🔴 Critical: 0  ·  🟠 High: 1  ·  🟡 Medium: 0  ·  ⚪ Low: 2  ·  🔵 Info: 0

## Coverage

- Pages selected: 1
- Checks run: crawl-access-audit, render-extraction-audit, structured-data-audit, freshness-corroboration-audit, engagement-audit
- Rendering (headless browser) available: False

## Findings

### 🟠 [HIGH · P1] Page request timed out

- **Skill:** crawl-access-audit  ·  **ID:** `ca-2f8aa7`
- **Evidence:** `ttfb` on https://adobe.com/ — Timeout (>15s)
- **Fix:** Investigate server latency/timeouts; pages that time out are dropped by crawlers.

### ⚪ [LOW · P3] Sitemap not declared in robots.txt

- **Skill:** crawl-access-audit  ·  **ID:** `ca-ca76a2`
- **Evidence:** `Sitemap` on https://adobe.com/robots.txt — no Sitemap: directive
- **Fix:** Add a `Sitemap:` line to robots.txt to speed up sitemap discovery.

### ⚪ [LOW · P3] Missing /llms.txt machine-facing summary file

- **Skill:** structured-data-audit  ·  **ID:** `sd-904f56`
- **Evidence:** `llms_txt` on https://adobe.com/llms.txt — No /llms.txt file was found at https://adobe.com//llms.txt. Emerging AI web agents use llms.txt to quickly parse site purpose, structure, and documentation.
- **Fix:** Create an /llms.txt markdown file at the domain root with an H1 brand title, a blockquote summary, and links to core docs.

## Proactive improvements

- **Publish an llms.txt index for AI agents** — Add /llms.txt summarizing the brand, key products, and canonical doc links so AI web agents can orient quickly.
- **Add FAQPage / HowTo schema where you have Q&A or step content** — Structured Q&A and step markup are directly consumable by AI answer engines and increase citation odds.
- **Keep sameAs + Wikidata grounding current** — Maintain sameAs links and a Wikidata item so assistants disambiguate the brand and trust its facts.
