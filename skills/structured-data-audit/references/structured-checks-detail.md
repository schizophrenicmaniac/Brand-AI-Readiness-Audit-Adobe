# Structured-Data Checks — Detailed Reference

> **Purpose:** Deep-reference document for the agent executing `structured-data-audit`.
> Contains severity decision trees, Schema.org validation rules, Open Graph / Twitter Card
> completeness rubrics, llms.txt parsing specifications, DOM-to-schema cross-reference heuristics,
> and false-positive suppression rules.
>
> **Machine-Readable SSOT Datasets:**
> - Schema.org Type Registry: [`references/schema-type-registry.json`](file:///c:/Users/Atharva/OneDrive/Desktop/WebD/Brand-AI-Readiness-Audit-Adobe/skills/structured-data-audit/references/schema-type-registry.json)
> - Audit Configuration & Thresholds: [`references/structured-data-config.json`](file:///c:/Users/Atharva/OneDrive/Desktop/WebD/Brand-AI-Readiness-Audit-Adobe/skills/structured-data-audit/references/structured-data-config.json)

---

## 1. Severity Decision Tree

Use this flowchart to assign severity deterministically for each finding in `structured-data-audit`.

```
START: Is structured data completely absent on a page where it is mandatory?
│
├─ YES (e.g. homepage has zero Schema.org markup, or e-commerce product page has no Product/Offer markup)
│  └─► severity = CRITICAL (if entire high-value domain identity/pricing is omitted)
│       or HIGH (if key entity markup is absent)
│
├─ NO → Is existing structured data syntactically invalid or broken?
│  │
│  ├─ YES (JSON syntax errors, broken JSON-LD strings, unparseable objects)
│  │  └─► severity = HIGH (or CRITICAL if it breaks page parser execution)
│  │
│  ├─ NO → Are required properties missing, or does schema contradict page content?
│  │  │
│  │  ├─ YES (Missing @context, missing required fields like name/url/price,
│  │  │       or schema price/name directly contradicts rendered <h1>/DOM)
│  │  │  └─► severity = HIGH
│  │  │
│  │  ├─ NO → Are recommended fields or standard social tags missing?
│  │  │  │
│  │  │  ├─ YES (Missing recommended properties like logo/sameAs/author,
│  │  │  │       missing Open Graph image/description, missing Twitter card)
│  │  │  │  └─► severity = MEDIUM
│  │  │  │
│  │  │  └─ NO → Are there minor hygiene issues?
│  │  │     │
│  │  │     ├─ YES (Meta description slightly too long/short, missing llms.txt,
│  │  │     │       minor title formatting issue, favicon missing high-res alternate)
│  │  │     │  └─► severity = LOW
│  │  │     │
│  │  │     └─ NO → Positive verification or informational item
│  │  │        └─► severity = INFO
```

### Severity Override Rules

| Condition | Override |
|-----------|----------|
| Homepage has no `Organization` or `LocalBusiness` schema | Always **high** (promoted to **critical** if site has zero structured data anywhere) |
| E-commerce product page missing `Offer` / price markup | **high** (prevents AI assistants from quoting price accurately) |
| Invalid JSON syntax inside `<script type="application/ld+json">` | Always **high** (wastes crawler parsing budget and invalidates entity graph) |
| JSON-LD declared `name` or `price` directly contradicts visible DOM text | Always **high** (causes AI models to detect conflicting signals and discount source) |
| Missing `sameAs` array on `Organization` schema | Cap at **low** (hygiene/linkage issue, entity still recognizable) |
| Missing `llms.txt` at site root | **low** (emerging standard, not yet a hard crawl failure) |
| Non-commercial/blog interior page lacking schema | **medium** or **low** depending on content value |

---

## 2. Check Catalog & Criteria

### SD-01: JSON-LD / Schema.org Presence
Checks whether appropriate semantic markup exists for each evaluated page:
- **Homepage**: Expects `Organization`, `Corporation`, or `LocalBusiness` as well as `WebSite`.
- **Product Pages**: Expects `Product` with nested or referenced `Offer`.
- **Articles & Blog Posts**: Expects `Article`, `NewsArticle`, or `BlogPosting` with authorship and publication timestamps.
- **FAQ / Help Pages**: Expects `FAQPage` with nested `Question` and `Answer` entities.
- **Interior Pages**: Expects `BreadcrumbList` reflecting navigation hierarchy.

### SD-02: JSON-LD Syntactic & Semantic Validity
Checks the validity of extracted JSON-LD blocks:
- **JSON Syntax**: Validated using standard strict JSON decoder. Common faults include unescaped newlines in descriptions, trailing commas, and unquoted keys.
- **@context**: Must point to `https://schema.org` (or `http://schema.org`).
- **@type**: Must match a recognized Schema.org type listed in [`references/schema-type-registry.json`](file:///c:/Users/Atharva/OneDrive/Desktop/WebD/Brand-AI-Readiness-Audit-Adobe/skills/structured-data-audit/references/schema-type-registry.json).
- **Required Properties**: Evaluated against the registry.
- **Dummy Data / Placeholder Detection**: Values like `"TODO"`, `"Lorem Ipsum"`, `"example.com"`, `"John Doe"` on live sites trigger high severity.

### SD-03: Open Graph Protocol Tags
Validates that explicit citation and preview metadata is present:
- **Required**: `og:title`, `og:description`, `og:image`, `og:url`, `og:type`.
- **Validation**:
  - `og:image` must be an absolute HTTPS URL.
  - `og:title` should not be empty or generic.
  - `og:type` should match standard Open Graph types (`website`, `article`, etc.).

### SD-04: Twitter / X Cards
Validates Twitter card markup:
- **Required**: `twitter:card`, `twitter:title`, `twitter:description`.
- **Recommended**: `twitter:image`, `twitter:site`.
- `twitter:card` must be one of `summary`, `summary_large_image`, `app`, `player`.

### SD-05: Title, Meta Description & Favicon
Evaluates core HTML metadata quality:
- **`<title>`**: Length between 10 and 65 characters. Must not match generic boilerplate (`"Home"`, `"Welcome"`, `"Untitled"`).
- **`<meta name="description">`**: Length between 50 and 165 characters. Must summarize page content accurately.
- **Favicon**: Checks for `<link rel="icon">`, `shortcut icon`, or `apple-touch-icon`.

### SD-06: llms.txt Machine Summary
Evaluates machine-facing documentation designed for LLMs:
- Checks `/llms.txt`, `/llms-full.txt`, and `/.well-known/llms.txt`.
- Validates that content is structured in Markdown with standard headers (`#`, `##`, `>`).
- Flags missing files as `low` severity recommendations.

---

## 3. Cross-Reference Logic (Schema vs. Visible Content)

Structured data is most dangerous to AI confidence when it claims facts different from what human visitors see.

1. **Title vs. Schema Name**:
   - Extract visible `<h1>` and document `<title>`.
   - Compare with Schema.org `name` / `headline`.
   - If token overlap is < 40%, flag as potential content mismatch.
2. **Pricing Discrepancies**:
   - Extract numeric prices from Schema `Offer.price`.
   - Scan visible text for currency symbols and numeric amounts.
   - If Schema price is missing from visible text, or differs significantly, flag as high severity.
3. **Publication Date Conflicts**:
   - Compare `datePublished` and `dateModified` in schema with visible date stamps in the article header or footer.

---

## 4. False-Positive Suppression Rules

To avoid noisy findings, suppress:
1. **Missing Product markup on non-product pages**: Only flag missing `Product` if page URL or visible content contains commerce signals (add to cart, checkout, SKU, pricing tables).
2. **Missing FAQ schema on general pages**: Only flag if page explicitly features accordion Q&A sections or FAQ headings.
3. **Missing `llms.txt` escalation**: Never escalate missing `llms.txt` beyond `low`.
4. **Social tags on utility pages**: Do not flag missing Open Graph / Twitter tags on `/login`, `/privacy-policy`, `/terms`, or search result pages.
5. **Short titles on concise brands**: If brand name is short (e.g. "Box", "Vercel") and combined with single word, do not flag title length if brand identity is clearly conveyed.
