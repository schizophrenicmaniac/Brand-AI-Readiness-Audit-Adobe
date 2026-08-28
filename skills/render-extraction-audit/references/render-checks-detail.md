# Render-Extraction Checks — Detailed Reference

> **Purpose:** Deep-reference document for the agent executing `render-extraction-audit`.
> Contains severity decision trees, false-positive suppression rules, key-fact
> classification, SPA detection heuristics, and image alt quality assessment.
>
> **Machine-Readable SSOT Datasets:**
> - Configuration & Thresholds: `references/render-config.json`
> - Key Fact Patterns: `references/key-fact-patterns.json`
> - Opaque Media Signatures: `references/opaque-media-signatures.json`

---

## 1. Severity Decision Tree

Use this flowchart to assign severity deterministically for each finding.

```
START: What type of content extraction problem is this?
│
├─ A) Content is in OPAQUE MEDIA (image, canvas, video, audio, PDF)
│  │  with NO text alternative?
│  │
│  ├─ Does the opaque media contain KEY FACTS?
│  │  │  (pricing, contact, product identity, reviews — see §2)
│  │  │
│  │  ├─ YES → severity = CRITICAL
│  │  │  Examples: pricing table as image with no alt, contact info
│  │  │  only in video with no transcript, product specs in canvas
│  │  │
│  │  └─ NO (non-critical content: decorative, supplementary)
│  │     └─► severity = MEDIUM
│  │        Examples: team photo gallery without alt, infographic
│  │        without text summary
│  │
├─ B) Content is JS-RENDERED ONLY (present in rendered DOM but
│  │  absent from raw HTML)?
│  │
│  ├─ Is the ENTIRE PAGE a JS shell (SPA with no SSR)?
│  │  ├─ YES → severity = HIGH
│  │  │  (The whole page is invisible to simple fetchers)
│  │  │
│  │  └─ NO → Is the JS-only content a KEY FACT?
│  │     ├─ YES → severity = HIGH
│  │     │  Examples: pricing loaded via AJAX, product description
│  │     │  rendered by React, contact form populated by JS
│  │     │
│  │     └─ NO → severity = MEDIUM
│  │        Examples: related products sidebar, social proof counter,
│  │        dynamic navigation menu
│  │
├─ C) Content is IN THE DOM but GATED behind interaction?
│  │
│  ├─ Is the content ABSENT from the DOM entirely (loaded on click)?
│  │  ├─ YES and contains KEY FACTS → severity = HIGH
│  │  ├─ YES but non-critical content → severity = MEDIUM
│  │  │
│  │  └─ NO (content is in DOM but CSS-hidden, e.g., display:none)
│  │     └─► severity = LOW
│  │        (Most crawlers can still read CSS-hidden DOM text)
│  │
├─ D) Content is EXTRACTABLE but quality is degraded?
│  │
│  ├─ Text-to-boilerplate ratio < 30%?
│  │  └─► severity = LOW
│  │     (Content is there but drowning in noise)
│  │
│  ├─ Icon fonts injecting ligature text into extracted content?
│  │  └─► severity = LOW
│  │     (Pollutes extraction but doesn't hide content)
│  │
│  └─ Image alt text exists but is generic/inaccurate?
│     └─► severity = MEDIUM
│        (Text alternative exists but is useless)
│
└─ E) No extraction problem detected
   └─► severity = INFO (positive signal)
```

### Severity Override Rules

| Condition | Override |
|-----------|----------|
| Entire page is SPA shell with zero `<noscript>` content | Always at least **high** |
| SPA page with adequate `<noscript>` fallback (>100 chars of real content) | Downgrade from high to **medium** |
| Opaque media contains pricing or contact info | Always **critical** |
| Video/audio with `<track kind="captions">` present | Downgrade to **info** |
| Tab/accordion content present in DOM (just CSS-hidden) | Cap at **low** |
| Tab/accordion content absent from DOM (JS-loaded on click) with key facts | At least **high** |
| Image with `role="presentation"` or `aria-hidden="true"` | Suppress (decorative) |
| PDF link with adequate HTML summary on linking page (>50 chars) | Cap at **low** |
| Iframe from suppressed source (analytics, ads, social) | Suppress |
| Iframe with same-origin content that IS indexable independently | Cap at **low** |
| Cross-origin iframe with business-critical content | At least **high** |

---

## 2. Key-Fact Classification

Content is classified as a "key fact" if it matches patterns from `references/key-fact-patterns.json`.
When key facts are detected in JS-only or opaque-media content, severity is escalated.

### Key Fact Categories (ordered by severity impact)

| Category | Examples | Severity if Opaque | Severity if JS-only |
|----------|----------|-------------------|---------------------|
| **Pricing / Cost** | Dollar amounts, "pricing", "per month", "free trial" | critical | high |
| **Contact Info** | Phone numbers, email, hours, "contact us" | critical | high |
| **Product Identity** | Product names, specs, features, availability | high | high |
| **Reviews / Ratings** | Star ratings, testimonials, review counts | high | medium |
| **FAQ / Help** | FAQ sections, how-to content | medium | medium |
| **Location** | Addresses, store locators, directions | high | medium |
| **General content** | Anything not matching above categories | medium | medium |

### How to Apply

1. Extract the text that is JS-only or locked in opaque media.
2. Run each key-fact category's regex patterns against the extracted text.
3. The highest-severity category match determines the finding severity.
4. If no category matches, use the "general content" severity.

---

## 3. SPA Shell Detection Heuristics

A page is classified as an **SPA shell** if:

1. The raw HTML body text (stripped of all tags) is **< 200 characters** AND
2. The page contains JavaScript bundle references (`<script src="...">`) AND
3. The page contains a container div pattern:
   - `<div id="root">`, `<div id="app">`, `<div id="__next">`,
   - `<div id="__nuxt">`, `<div id="___gatsby">`, `<main-app>`, etc.

### Framework-Specific Detection

Use the signatures in `references/opaque-media-signatures.json` → `spa_frameworks` to identify the framework.

### SSR Detection

If the page matches SPA framework markers BUT the raw HTML body text is **> 200 characters** with meaningful content, the page is using **Server-Side Rendering (SSR)** — this is a positive signal. Report as `info`.

### Noscript Quality Assessment

When a SPA shell is detected, check `<noscript>` content:

| Noscript Content | Severity Impact |
|-----------------|-----------------|
| No `<noscript>` tag at all | No mitigation — full severity applies |
| `<noscript>` with "JavaScript is required" message only | No mitigation |
| `<noscript>` with partial content (>100 chars of real text) | Downgrade by one level |
| `<noscript>` with comprehensive content (>500 chars) | Downgrade by two levels |

---

## 4. Image Alt Text Quality Assessment

### Quality Rules (apply in order)

| # | Rule | Finding? | Severity |
|---|------|----------|----------|
| 1 | Image has `role="presentation"` or `aria-hidden="true"` or is a tracking pixel (1x1, spacer) | **Suppress** — decorative/non-content | N/A |
| 2 | Image has no `alt` attribute at all AND appears informational | **Yes** | Per key-fact escalation |
| 3 | Image has `alt=""` (empty) AND appears informational (not decorative) | **Yes** | Per key-fact escalation |
| 4 | Image alt matches generic pattern: "image", "photo", "picture", "img", "untitled", etc. | **Yes** | medium |
| 5 | Image alt is a filename: matches `\.(jpe?g\|png\|gif\|webp\|svg)$` | **Yes** | medium |
| 6 | Image alt is very short (< 5 chars) for a complex image | **Yes** | low |
| 7 | Image alt is excessively long (> 300 chars) — keyword stuffing risk | **Yes** | low |
| 8 | Image has descriptive alt (5–300 chars, not generic, not filename) | **No** — positive signal | info |

### Determining if an Image is "Informational"

An image is considered informational (not decorative) if any of:
- It is inside `<main>`, `<article>`, or a content container
- Its `src`/filename suggests content: contains "product", "pricing", "menu", "team", "banner", "hero"
- It has a `<figcaption>` sibling (implies it carries meaning)
- Its dimensions are significant (width > 100px AND height > 100px, if inferable)
- It is NOT a tracking pixel, spacer, divider, or background pattern

---

## 5. Text-to-Boilerplate Ratio Calculation

### Algorithm

1. **Identify main content area:** Look for `<main>`, `<article>`, `[role="main"]`, `#content`, `#main-content`, `.content`, `.main-content` elements.
2. **Extract main content text:** Strip tags from the identified main content area(s).
3. **Extract full body text:** Strip tags from the entire `<body>`.
4. **Calculate ratio:** `main_content_text_length / full_body_text_length`
5. **If no main content area is identified:** Use a heuristic — exclude text from `<nav>`, `<header>`, `<footer>`, `<aside>`, `[role="navigation"]`, `[role="banner"]`, `[role="contentinfo"]`, `.nav`, `.header`, `.footer`, `.sidebar` elements.

### Threshold

| Ratio | Severity | Meaning |
|-------|----------|---------|
| ≥ 0.50 | info | Good — majority of text is main content |
| 0.30 – 0.49 | low | Marginal — boilerplate is significant but content is still identifiable |
| < 0.30 | low | Poor — main content is drowning in navigation, footer, legal text |
| No main content area identifiable | medium | Cannot isolate main content at all — page structure is poor |

---

## 6. CSS Pseudo-Element Content Detection

CSS `content` property on `::before` / `::after` pseudo-elements places text that is:
- Visible to the human eye
- NOT in the DOM (not in `innerHTML` or `textContent`)
- Invisible to most text extractors

### Detection Method

1. Parse `<style>` blocks and inline styles for `content:` declarations.
2. Ignore `content: ""`, `content: none`, `content: normal`, `content: counter(...)`.
3. Ignore `content` used with `list-style-type` or `quotes`.
4. Flag `content` declarations that contain quoted text strings with real words (not just symbols like "•", "→", "×").
5. Note: External stylesheets are NOT parsed (too expensive). Only inline `<style>` blocks and inline `style` attributes are checked.

### What to Record

- The CSS selector (or element) using the `content` property
- The actual content string
- Whether it appears to contain meaningful text vs. decorative symbols

---

## 7. Shadow DOM Detection

### Detection Method (Headless Browser)

```javascript
// Run inside page.evaluate()
const shadowHosts = [];
document.querySelectorAll('*').forEach(el => {
  if (el.shadowRoot) {
    shadowHosts.push({
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      class: el.className || null,
      innerTextLength: el.shadowRoot.textContent.length,
      innerTextPreview: el.shadowRoot.textContent.substring(0, 200)
    });
  }
});
return shadowHosts;
```

### Severity Assignment

| Condition | Severity |
|-----------|----------|
| Shadow DOM contains key facts (pricing, contact, specs) | high |
| Shadow DOM contains substantial text (>200 chars) | medium |
| Shadow DOM contains minimal text (<200 chars) | low |
| No shadow DOM detected | info (positive signal) |

---

## 8. False-Positive Suppression Rules

To avoid noisy, unhelpful findings, suppress these patterns:

| Pattern | Why Suppress |
|---------|-------------|
| `<img>` with `role="presentation"` or `role="none"` | Explicitly marked as decorative |
| `<img>` with `aria-hidden="true"` | Intentionally hidden from assistive tech |
| `<img>` with dimensions ≤ 5×5 pixels (tracking pixels) | Not content |
| `<img>` with src containing "spacer", "pixel", "blank", "transparent", "tracking", "beacon" | Tracking/layout pixels |
| Screen-reader-only hiding patterns (`.sr-only`, `.visually-hidden`, `clip-rect(0,0,0,0)`, `position: absolute; left: -9999px`) | This is GOOD practice — content IS available to machines via text, just visually hidden |
| `display: none` on mobile nav toggle elements at desktop viewport | Responsive design, not content hiding |
| JS-dependent content where `<noscript>` provides an adequate equivalent | Fallback exists |
| PDF links where the linking page includes ≥50 chars summarizing the PDF content | Summary exists |
| Icon fonts used for decorative/navigational icons (hamburger, close, arrow, search) | Non-informational |
| `<iframe>` from suppressed sources (analytics, ads, social buttons, chat widgets) — see `render-config.json` | Not content iframes |
| Tabs/accordions where ALL content is present in the DOM (just CSS-hidden) | Most crawlers can read CSS-hidden content |
| `<canvas>` used for background effects, particle animations, or decorative graphics | Not informational content |
| `<video>` / `<audio>` used for background ambiance (autoplay, muted, loop, no controls) | Not informational content |

---

## 9. Iframe Content Assessment

### Classification Matrix

| Iframe Source | Content Type | Severity |
|---------------|-------------|----------|
| Same-origin, content-bearing (pricing calc, product config) | **Content** | high (if no alternative) |
| Same-origin, independently indexable page | **Content** | low (page exists on its own) |
| Cross-origin, content-bearing (booking widget, review platform) | **Content** | high |
| Cross-origin, supplementary (embedded map, YouTube video) | **Supplementary** | low |
| Third-party service (analytics, ads, chat, social) | **Non-content** | Suppress |

### What to Record

For each content-bearing iframe:
- The `src` URL
- Whether it's same-origin or cross-origin
- Whether equivalent content exists elsewhere on the page
- The iframe's approximate position/context on the page

---

## 10. Infinite Scroll / Load-More Detection

### Detection Signals (from raw HTML)

1. Presence of "Load More" / "Show More" buttons (text matching patterns in `opaque-media-signatures.json`)
2. Data attributes: `data-infinite-scroll`, `data-load-more`, `data-next-page`
3. CSS classes: `infinite-scroll`, `load-more`, `scroll-sentinel`
4. Very few list items in a container that appears designed for many (e.g., `<ul>` with 5 items and a pagination indicator)

### Detection Signals (from rendered DOM)

1. `IntersectionObserver` setup targeting a sentinel element
2. Scroll event listeners that trigger content loading
3. Pagination metadata (e.g., `data-total-pages="20"`, `data-page="1"`)

### What to Record

- The content container element
- Number of items visible in initial DOM
- Whether pagination metadata indicates more content exists
- The total expected items (if determinable)
