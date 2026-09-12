# Engagement Checks — Detailed Reference

> **Purpose:** Deep-reference document for the agent and scripts executing `engagement-audit`.
> Contains severity decision trees, DOM parsing heuristics, performance red-flag rubrics,
> mobile readiness criteria, call-to-action effectiveness scoring, site search discoverability,
> context retention markers, and false-positive suppression rules.
>
> **Machine-Readable SSOT Datasets:**
> - Audit Configuration & Thresholds: [`references/engagement-config.json`](file:///c:/Users/Atharva/OneDrive/Desktop/WebD/Brand-AI-Readiness-Audit-Adobe/skills/engagement-audit/references/engagement-config.json)

---

## 1. Severity Decision Tree

Use this flowchart to assign severity deterministically for each finding in `engagement-audit`.

```
START: Is key information unreachable or is page functionally broken on mobile?
│
├─ YES (e.g., missing viewport meta tag causing unreadable desktop scale on phones;
│       or core commercial destinations like pricing/about/contact unreachable within 2 clicks;
│       or horizontal viewport overflow breaking page layout)
│  └─► severity = CRITICAL
│
├─ NO → Are primary orientation cues absent on landing pages or primary CTAs missing?
│  │
│  ├─ YES (Landing page lacks an <h1> or any visible above-the-fold value proposition;
│  │       or commercial/product page has zero actionable call-to-action buttons)
│  │  └─► severity = HIGH
│  │
│  ├─ NO → Are there significant load performance concerns, missing breadcrumbs, or generic CTAs?
│  │  │
│  │  ├─ YES (Multiple render-blocking scripts in <head>; images missing dimensions or lazy-loading;
│  │  │       deep catalog/product pages lacking breadcrumbs; CTAs dominated by generic "Learn More";
│  │  │       no discoverable site search on content-heavy site)
│  │  └─► severity = MEDIUM
│  │
│  ├─ NO → Are there minor UX or continuity gaps?
│  │  │
│  │  ├─ YES (Search icon lacking accessible label; no returning-visitor context retention widgets;
│  │  │       single stylesheet or minor asset hygiene issue)
│  │  └─► severity = LOW
│  │
│  └─ NO → Positive verification or informational item
│     └─► severity = INFO
```

### Severity Override Rules

| Condition | Override | Rationale |
|-----------|----------|-----------|
| Missing `<meta name="viewport">` tag | Always **critical** | Completely breaks mobile rendering; AI-referred visitors on mobile devices will bounce immediately. |
| Key destinations (`/pricing`, `/about`, `/contact`, `/products`) completely omitted from main navigation | Always **critical** (or **high** if partially discoverable in footer) | AI assistants send visitors looking for specific facts; failure to find nav paths within 1–2 clicks leads to immediate abandonment. |
| Viewport disables user scaling (`user-scalable=no` or `maximum-scale=1.0`) | **high** | Violates WCAG accessibility standards and frustrates low-vision mobile visitors. |
| Product or pricing page has zero visible actionable CTA buttons | **high** | Arriving visitors from AI citations have purchase/evaluation intent; lack of CTA causes funnel drop-off. |
| Landing page lacks a clear `<h1>` or visible value proposition above-the-fold | **high** | Disorients visitors who arrive without previous site context. |
| > 3 render-blocking `<script>` tags in `<head>` | **medium** | Directly increases First Contentful Paint (FCP) and delays DOM rendering for referred traffic. |
| Catalog or interior pages lack breadcrumbs | **medium** | Visitors arriving via deep links from AI citations have no sense of site hierarchy. |
| Primary CTAs dominated by ambiguous generic text ("Learn More" × 3+) | **medium** | Reduces conversion velocity and confuses decision-making. |
| Content-rich site (>5 pages) lacks discoverable on-site search | **medium** | Impedes visitors attempting to find topics discussed in AI summaries. |
| No returning-visitor context retention features (history, saved items) | Cap at **low** | Hygiene enhancement for long-term retention; does not block initial landing experience. |
| Utility or auth flows (`/login`, `/signup`, `/checkout`) | **Suppress** | Nav and CTA rules do not apply to focused conversion funnels. |

---

## 2. Check Catalog & Criteria

### EG-01: Navigational Clarity
Evaluates whether visitors arriving from AI search citations can navigate the site effortlessly:
- **Key Destinations Check**: Scans main navigation (`<nav>`, `[role="navigation"]`, `.navbar`) for links to core destinations:
  - **About**: Company background, mission, or identity (`/about`, `/company`, `/who-we-are`).
  - **Pricing / Plans**: Commercial terms, subscriptions, or rates (`/pricing`, `/plans`).
  - **Products / Services**: Offerings, catalog, solutions (`/products`, `/services`, `/features`, `/solutions`).
  - **Contact / Support**: Getting in touch, customer care (`/contact`, `/support`, `/help`).
- **Click Depth**: Key destinations must be accessible within 1 click from the header or footer nav.
- **Orphaned Page Detection**: Checks whether internal pages link back to the main site structure.

### EG-02: Orientation Cues
Evaluates whether an arriving visitor immediately understands page context and hierarchy:
- **`<h1>` Heading Quality**:
  - Exactly one descriptive `<h1>` per page.
  - Length between 5 and 100 characters.
  - Must not be generic boilerplate ("Home", "Welcome", "Page").
- **Above-The-Fold Value Proposition**:
  - Detects hero copy (introductory paragraph, subheading, or tagline near the top of the body).
  - Minimum 30 characters of explanatory text before deep scrolling.
- **Breadcrumb Navigation**:
  - Evaluates presence of visible breadcrumb UI (`.breadcrumb`, `[aria-label="breadcrumb"]`) and JSON-LD `BreadcrumbList`.
  - Mandatory on deep interior pages (URL path depth ≥ 2, e.g., `/products/enterprise-suite`).

### EG-03: Load Performance Red Flags
Identifies common DOM and asset architectural choices that delay visual rendering:
- **Render-Blocking Scripts**: Counts `<script>` tags in `<head>` that lack `async` or `defer` attributes (excluding JSON-LD scripts). Threshold: > 3 render-blocking scripts triggers `medium`.
- **External Stylesheets**: Counts `<link rel="stylesheet">` tags in `<head>`. Threshold: > 4 external sheets triggers `medium`.
- **Image Dimension Attributes**: Checks `<img>` tags for explicit `width` and `height` attributes to prevent Cumulative Layout Shift (CLS).
- **Image Lazy Loading**: Checks if images below the initial fold specify `loading="lazy"`.
- **Third-Party Domain Sprawl**: Counts unique external hostnames referenced in script, style, and iframe sources. Threshold: > 10 external domains triggers `medium`.

### EG-04: Mobile Responsiveness
Validates that the page is prepared for mobile touch visitors:
- **Viewport Meta Tag**:
  - `<meta name="viewport" content="...">` must be present in `<head>`.
  - Must include `width=device-width`.
  - Must include `initial-scale=1` (or `1.0`).
- **Accessibility Anti-Patterns**:
  - Must NOT restrict zooming via `user-scalable=no` or `maximum-scale=1.0`.
- **Responsive Media**:
  - Evaluates whether images use `srcset` or `<picture>` elements for responsive sizing.

### EG-05: Calls-to-Action (CTA) Clarity
Audits the effectiveness and discoverability of conversion paths:
- **Actionable vs. Generic Copy**:
  - Analyzes button and prominent link text.
  - Flags excessive repetition of generic phrases ("Learn More", "Click Here", "Read More").
  - Evaluates presence of high-intent actionable verbs ("Start Free Trial", "Request Demo", "Book a Call", "Buy Now", "Get Started").
- **CTA Density & Competing Buttons**:
  - Identifies if a page has zero primary CTAs (high severity on commercial/product pages).
  - Flags pages with > 3 competing primary action buttons causing decision paralysis.

### EG-06: On-Site Search
Verifies discoverability of site search functionality:
- **Search Element Presence**:
  - Checks for `<input type="search">`, `input[name="q"]`, `form[action*="search"]`, or search role `[role="search"]`.
  - Checks for search trigger icons/buttons in header navigation.
- **Discoverability**:
  - Flags search inputs that are completely absent on multi-page documentation, blog, or catalog sites.
  - Flags icon-only search triggers that lack `aria-label`, `title`, or screen-reader text.

### EG-07: Context Retention & Continuity
Evaluates whether returning visitors (especially those referred back by an AI assistant) find stateful continuity:
- **History & Recently Viewed**: Detects components or hooks for browsing history, recently viewed products/articles, or "continue where you left off".
- **Saved Items & Personalization**: Checks for save, favorite, or bookmark buttons (`wishlist`, `saved`, `favorites`).
- **Account & Session Anchors**: Checks for user profile or account authentication links providing continuity.

---

## 3. False-Positive Suppression Rules

To prevent noise, apply these suppression rules:

1. **Utility & Conversion Funnel Pages**:
   - Suppress navigation and search checks on `/login`, `/signup`, `/cart`, `/checkout`, and `/auth`. These pages intentionally minimize navigation to focus visitor completion.
2. **Legal & Policy Pages**:
   - Suppress CTA clarity and context retention checks on `/privacy-policy`, `/terms`, `/legal`, `/cookie-policy`.
3. **Single-Topic Minimalist Microsites**:
   - For websites with ≤ 3 total pages, suppress on-site search requirements.
4. **Single-Product Landing Pages**:
   - Breadcrumbs are suppressed on pages with path depth = 1 (e.g., `/features`, `/pricing`).
5. **Brand Nav Variations**:
   - If a brand uses alternative standard terms (e.g., "Documentation" or "Docs" instead of "Help", or "Solutions" instead of "Products"), treat as fulfilling the navigation criteria.
