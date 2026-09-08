# Freshness & Corroboration Checks — Detailed Reference

> **Purpose:** Deep-reference document for the agent and scripts executing `freshness-corroboration-audit`.
> Contains severity decision trees, date extraction heuristics, content staleness criteria,
> external corroboration rubrics, entity disambiguation methodologies, Wikipedia/Wikidata grounding
> verification, and false-positive suppression rules.
>
> **Machine-Readable SSOT Datasets:**
> - Audit Configuration & Thresholds: [`references/freshness-config.json`](file:///c:/Users/Atharva/OneDrive/Desktop/WebD/Brand-AI-Readiness-Audit-Adobe/skills/freshness-corroboration-audit/references/freshness-config.json)

---

## 1. Severity Decision Tree

Use this flowchart to assign severity deterministically for each finding in `freshness-corroboration-audit`.

```
START: Is there a core commercial or identity claim that is visibly stale and isolated?
│
├─ YES (e.g. pricing page shows multi-year-old rates with no update signal and zero external corroboration,
│       or discontinued flagship product promoted with no disambiguation)
│  └─► severity = CRITICAL
│
├─ NO → Is there a brand collision without disambiguation, or are core facts uncorroborated?
│  │
│  ├─ YES (Brand name collides with well-known entity and zero sameAs links exist;
│  │       or key claims like founding date / HQ / leadership are isolated and unverified)
│  │  └─► severity = HIGH
│  │
│  ├─ NO → Are key pages missing date signals, or are facts corroborated but conflicting?
│  │  │
│  │  ├─ YES (Commercial/product pages lack any Last-Modified / dateModified / visible byline;
│  │  │       or dates are >12 months old on active service pages; or contradictory dates/stats across sources)
│  │  └─► severity = MEDIUM
│  │
│  ├─ NO → Are there minor staleness signals or hygiene gaps?
│  │  │
│  │  ├─ YES (Blog post dates >12mo on evergreen content; missing Wikidata/Wikipedia entry for small entity;
│  │  │       occasional dead external reference link; missing secondary social profile in sameAs)
│  │  └─► severity = LOW
│  │
│  └─ NO → Positive verification or informational item
│     └─► severity = INFO
```

### Severity Override Rules

| Condition | Override | Rationale |
|-----------|----------|-----------|
| Commercial/pricing page shows rates > 24 months old with no update stamp | Always **critical** | AI assistants will either quote outdated pricing confidently or hallucinate corrections, harming conversions and trust. |
| Brand name collides with high-authority entity (e.g., generic English word, established global brand) and lacks `sameAs` | Always **high** | AI models conflate entities and attribute facts to the wrong organization in zero-shot answers. |
| Core brand claims (founding year, leadership, HQ) found on 0 independent external sources | Always **high** | "Isolated facts" fail AI multi-source consensus filters and are dropped from conversational summaries. |
| Key product / service page has no date signal (`Last-Modified`, `<meta>`, JSON-LD, or visible) | Cap at **medium** | Lack of freshness metadata creates uncertainty for temporal crawlers (e.g., Google freshness boost, Perplexity recency filtering). |
| Missing Wikipedia or Wikidata entry for an established mid/large enterprise | **medium** | Lack of knowledge graph node severely impedes zero-shot entity grounding. |
| Missing Wikipedia/Wikidata entry for small/local business | Cap at **low** or **suppress** | Local businesses rarely meet Wikipedia notability guidelines; NAP consistency in local directories takes precedence. |
| Blog post / news article published > 12 months ago | Cap at **low** (or **info** if clearly an archived article) | Historical editorial content naturally ages; should not penalize overall site readiness unless masquerading as current. |
| Single dead outbound link on non-critical page | Cap at **low** | Minor hygiene issue that indicates slight maintenance neglect. |

---

## 2. Check Catalog & Criteria

### FC-01: Last-Modified / Published Dates
Evaluates whether machine and human temporal signals exist on key pages:
- **HTTP Header**: Checks `Last-Modified` and `Date` response headers. A missing `Last-Modified` header deprives search crawlers of conditional GET (`If-Modified-Since`) cache efficiency.
- **Metadata Tags**: Checks `<meta property="article:modified_time">`, `<meta property="article:published_time">`, `<meta name="dcterms.modified">`, etc.
- **JSON-LD Schema**: Checks `datePublished`, `dateModified`, `uploadDate` inside `Article`, `Product`, `WebPage`, or `FAQPage`.
- **Visible Bylines**: Checks for visible "Updated on [Date]", "Published [Date]", or regex-matched date strings near headings or footers.
- **Evaluation Criteria**:
  - Pages with **zero** date signals of any kind are flagged as `medium`.
  - Pages with date signals older than **12 months** on dynamic/service pages are flagged as `medium`.
  - Pricing pages with date signals older than **24 months** or zero dates are flagged as `critical` or `high`.

### FC-02: Content Staleness Signals
Detects visible text and link signals indicating abandoned or stale content:
- **Future-Tense Historical References**: Scans for phrases describing past years as upcoming (e.g., "coming in 2022", "roadmap for 2023", "upcoming 2024 conference").
- **Outdated Commercial Copy**: Detects phrases like "2022 pricing", "new for 2021", or seasonal promotions from previous calendar years.
- **Dead Outbound Links**: Inspects external hyperlinks to verify they return valid HTTP 2xx/3xx codes; dead links (404, 410, connection errors) signal stale content.
- **Outdated Team / Contact Info**: Detects stale copyright years (>2 years behind current calendar year) in footer boilerplate.

### FC-03: External Corroboration
Assesses whether core claims made on the site exist across independent authoritative sources:
- **Target Claims (3–5 selected)**:
  1. Official brand / legal name
  2. Founding year
  3. Headquarters / principal office location
  4. Flagship product / service names
  5. Executive leadership (CEO / Founders)
- **Evaluation Method**:
  - Query web search using query templates: `"<Brand Name>" "<Claim Value>"` or `"<Brand Name>" headquarters` etc.
  - An independent source is one whose root domain is not owned by or affiliated with the audited entity.
  - Claims found **only** on the target domain are classified as **isolated claims**.
  - Conflicting claims across independent sources are flagged for factual ambiguity.

### FC-04: Entity Disambiguation
Checks whether the entity is uniquely distinguishable from other entities:
- **Name Collision Risk**: Evaluates if the brand name matches dictionary words, common acronyms, or well-known organizations.
- **`sameAs` Schema Links**: Checks JSON-LD `Organization` / `LocalBusiness` for external authority links:
  - Wikipedia URL (`https://en.wikipedia.org/wiki/...`)
  - Wikidata URI (`https://www.wikidata.org/wiki/Q...`)
  - LinkedIn company profile (`https://www.linkedin.com/company/...`)
  - Crunchbase organization page (`https://www.crunchbase.com/organization/...`)
  - Official social profiles (X/Twitter, YouTube, GitHub)
- **NAP Consistency (for Local Entities)**: For businesses with physical locations, verifies that Name, Address, and Telephone are explicitly declared in structured markup and match across citations.

### FC-05: Wikipedia / Wikidata Grounding Presence
Evaluates whether the entity exists in primary knowledge graphs used by LLMs and AI search engines:
- **Wikidata**: Checks whether an entity item (`QID`) exists via the Wikidata Search API. Wikidata is the definitive structured knowledge base powering Google Knowledge Graph, Perplexity entity cards, and ChatGPT citations.
- **Wikipedia**: Checks whether an English or local-language Wikipedia article exists. Serves as the primary training corpus anchor for LLM parametric memory.
- **Finding Nuance**: For non-notable or small entities where Wikipedia is unachievable, recommends establishing strong Wikidata, Crunchbase, and LinkedIn presence instead.

---

## 3. Corroboration & Disambiguation Heuristics

### Claim Extraction & Search Templates

| Claim Type | Extraction Source | Search Query Template | Corroboration Target |
|------------|-------------------|-----------------------|----------------------|
| **Brand Identity** | `Organization.name`, `<title>`, `og:site_name` | `"<Brand Name>" "company" OR "about"` | Corporate registries, Bloomberg, Crunchbase |
| **Founding Year** | `Organization.foundingDate`, About page copy | `"<Brand Name>" "founded" OR "established"` | News articles, industry directories, Wikidata |
| **Headquarters** | `Organization.address`, Contact/About page | `"<Brand Name>" "headquarters" OR "HQ"` | Google Maps, Crunchbase, Business directories |
| **Flagship Product** | `Product.name`, navigation menus, pricing tiers | `"<Brand Name>" "<Product Name>"` | G2, Capterra, Product Hunt, media reviews |
| **Leadership** | `Organization.founder`, About/Team page | `"<Brand Name>" "CEO" OR "founder"` | LinkedIn, press releases, Forbes, Reuters |

### Corroboration Scoring Rubric

- **Corroborated (Pass)**: Claim matches across at least 1 authoritative third-party source (news media, verified directory, encyclopedia, review platform).
- **Inconsistent (Warning)**: Claim appears externally, but values disagree (e.g., brand claims founded in 2018, external sources report 2019).
- **Isolated (Fail)**: Claim appears nowhere outside the audited domain. Flagged under FC-03.

---

## 4. False-Positive Suppression Rules

To maintain high signal-to-noise ratio in audit findings, apply these suppression rules:

1. **Evergreen Pages (FC-01)**:
   - Do not flag missing or old dates on purely evergreen pages: `/about`, `/mission`, `/history`, `/values`, `/privacy-policy`, `/terms`, `/legal`.
   - Only flag if copy on these pages explicitly mentions outdated time-bound claims.
2. **Archived Editorial Content (FC-01)**:
   - Blog posts or press releases with clear historical publication dates (e.g., an article from 2021) should **not** be flagged as stale unless the page claims to be a living guide or current documentation.
3. **Wikipedia Notability Threshold (FC-05)**:
   - Do not escalate missing Wikipedia presence beyond `low` (or suppress entirely) for local businesses, early-stage startups, or niche SMBs that do not meet Wikipedia's General Notability Guideline (GNG). Focus recommendation on Wikidata and Crunchbase instead.
4. **Utility and Auth Pages (FC-01, FC-02)**:
   - Suppress date and staleness checks on `/login`, `/signup`, `/cart`, `/checkout`, `/account`, `/search`.
5. **Brand Collisions for Distinctive Names (FC-04)**:
   - If a brand name is coined, unique, or trademarked with zero search ambiguity (e.g., "Spotify", "DuckDuckGo"), suppress name collision warnings even if `sameAs` links are sparse.
