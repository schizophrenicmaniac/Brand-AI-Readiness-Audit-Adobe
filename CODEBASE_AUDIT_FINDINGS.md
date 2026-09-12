# Complete Codebase Inspection Findings

**Project:** Brand AI Readiness Audit â€” Adobe University Hackathon 2026 Round 3  
**Inspection date:** 2026-09-11  
**Purpose:** One consolidated record of the existing implementation, useful assets, requirement coverage, defects, false-positive risks, safety gaps, and recommended next steps.

## 1. Scope and verification status

This document records the repository inspection performed before implementation. The source scripts, six skill definitions, reference checklists, configuration datasets, dependency files, marketplace manifest, root README, and ignore rules were inspected. Recent Git history was checked to understand the implementation sequence.

**Important distinction:** These are code-inspection findings, not results from auditing a live website. Existing functionality below means that implementation is present; it does not mean it has passed an end-to-end test. No live-site benchmark, browser execution, complete regression suite, official skill-format validator, or release ZIP validation was completed during this inspection.

The original request was to improve the implementation. The task was subsequently redirected to producing this document. No existing runtime code was changed. The unused initializer created immediately before the interruption was removed; this findings document is the intended deliverable.

Priority labels in this document describe **engineering work on this repository**, not the severity to assign to a website:

- **P0:** Submission contract, central safety, or end-to-end execution blockers.
- **P1:** Major detection correctness, evidence, reliability, or generalization issues.
- **P2:** Important coverage, usability, and maintainability improvements.
- **P3:** Optional enhancements after the essentials are correct.

## 2. Executive assessment

The repository is **not an empty scaffold**. It already contains substantial extraction logic, two finding-producing validators, useful reference datasets, and a sensible six-skill separation. Rebuilding it wholesale would discard useful work.

The strongest existing design decision is the separation between access, readability, structured information, trust/entity attribution, and engagement. The manifest already lists all six skills and marks exactly one entrypoint.

The largest gaps are:

1. The orchestrator is a written workflow, not an executable end-to-end pipeline.
2. The report contract does not match the requested fields and omits explicit evidence and prioritized action objects.
3. Engagement has instructions but no executable checks.
4. Safety is fragmented: robots rules, redirects, external URLs, browser traffic, authentication boundaries, and request budgets are not enforced consistently.
5. Several important findings are generated from proxies rather than direct evidence: missing JSON-LD becomes a critical defect; old metadata becomes stale facts; missing `sameAs` becomes lack of corroboration; short brand names become entity collisions.
6. No shared snapshots, global runtime budget, or completed validation harness establish repeatability and the under-five-minute requirement.

**Recommended direction:** Preserve the six skill boundaries and reusable extraction helpers. Add safe shared collection, an executable composition layer, one finding/report contract, conservative evidence-backed validation, and representative verification.

## 3. Existing repository inventory

### 3.1 Root files

| File | Existing purpose | Assessment |
|---|---|---|
| `marketplace.json` | Marketplace name, team, six skill paths, entrypoint designation | All existing skills are listed; only `audit-orchestrator` has `entrypoint: true`. |
| `README.md` | Skill overview and composition diagram | Useful architecture explanation; lacks practical setup, execution, safety, report, and packaging guidance. |
| `.gitignore` | Python environments, build output, editor files, secrets, scratch artifacts | Useful baseline. `.env/` and `.env` are both excluded; local test/scratch directories are excluded. |

No repository `AGENTS.md` was found in the recursive workspace inspection. No existing test suite, root runtime package, root report schema, root dependency setup, or release packaging script was found.

### 3.2 Skills and scripts

| Skill | Existing executable assets | Role and current limitation |
|---|---|---|
| `audit-orchestrator` | None; `scripts/` and `references/` contain placeholders | Defines composition and report assembly in prose only. |
| `crawl-access-audit` | `robots_analyzer.py`, `sitemap_validator.py`, `page_fetcher.py`, `url_utils.py` | Substantial collection and access signals; findings must be interpreted by an agent. |
| `render-extraction-audit` | `html_fetcher.py`, `rendered_dom_extractor.py` | Static inventory and optional Playwright extraction; no standalone finding compiler. |
| `structured-data-audit` | `structured_data_extractor.py`, `schema_validator.py` | Extraction and executable findings; several overbroad heuristics and validation defects. |
| `freshness-corroboration-audit` | `freshness_extractor.py`, `freshness_validator.py` | Date/entity extraction and executable findings; external corroboration still needs supplied evidence. |
| `engagement-audit` | None; `scripts/` and `references/` contain placeholders | UX checklist only; no automated orientation or context-retention implementation. |

### 3.3 Reference assets worth retaining

- Crawl: crawler registry in JSON and Markdown, challenge signatures, configurable limits, detailed access checklist.
- Render: framework/media signatures, key-fact patterns, thresholds, detailed extraction checklist.
- Structured data: schema type registry, configuration, detailed checklist.
- Freshness: date/entity configuration and detailed corroboration checklist.

These assets should be corrected and consolidated, not automatically removed. Some claim more coverage or certainty than the scripts actually provide; those differences are detailed below.

### 3.4 Environment observed

- Python: **3.12.10**.
- Installed packages reported: `requests` **2.32.5**, `beautifulsoup4` **4.15.0**, `jsonschema` **4.25.1**.
- `playwright` and `PyYAML` were not installed in the inspected Python environment.
- Chromium availability for Playwright was not established.
- Existing requirements use lower bounds rather than a tested reproducible dependency set.
- Local network access is restricted in the coding environment; this is an environment limitation, not a website defect.

## 4. Requirement-by-requirement mapping

| Requirement | Current status | What is needed |
|---|---|---|
| Inspect before changing | Completed | This document records the inspection. |
| Accept any website URL/domain | Partial | Normalize bare domains and preserve requested paths consistently; restrict unsafe targets without calling them website defects. |
| Marketplace containing skills | Present | Retain six meaningful skills. |
| Every skill complies with agentskills.io | Not validated; compatibility concerns | Remove unsupported/provider-specific frontmatter, verify names/descriptions, and run format validation. |
| `marketplace.json` lists every skill | Present | Add automated manifest-to-directory validation. |
| Exactly one entrypoint | Present in manifest | Validate uniqueness automatically and make entrypoint executable. |
| Entrypoint composes other skills | Written only | Implement invocation, shared inputs, dependency handling, result merging, and partial reports. |
| One final report | Written only | Implement report assembly and schema validation. |
| Root README explains skills/composition | Present | Extend with installation, commands, output contract, guardrails, limitations, and packaging. |
| Crawlability detection | Substantial partial implementation | Repair robots safety, bounded discovery, challenge/soft-404 precision, and evidence generation. |
| JavaScript/rendering gaps | Partial implementation | Fix text comparison, baseline/error handling, parser state, and safe browser collection. |
| Missing/invalid structured data | Implemented with correctness risks | Separate optional markup from defects; fix context, type, nested properties, and explicit contradictions. |
| Facts hidden in non-text | Partial inventory | Establish that media contains a fact and lacks an equivalent; do not infer contents from filenames alone. |
| Stale facts | Weak proxy-based implementation | Use semantic date roles, dynamic time comparisons, archive suppression, and actual contradictions. |
| Uncorroborated facts | External/manual dependency | Require reviewed independent sources and disclose bounded search coverage. |
| Entity ambiguity | Heuristic only | Compare entity identifiers and observed conflicting identities; do not infer collisions from name length. |
| Weak on-site orientation | Checklist only | Add executable checks and scoped evidence. |
| Lack of context retention | Checklist only | Verify relevant read-only state continuity; use `not_checked` when behavior cannot be established. |
| Additional repeatable root causes | Some useful candidates exist | Improve precision on broken references, canonical conflicts, late content, ambiguous page identity, and state-losing navigation. |
| Finding `id`, `title`, `severity`, `evidence`, `suggested_action` | Not satisfied | Replace/adapt the legacy finding contract. |
| Action `summary` and `priority` | Not satisfied | Add structured action objects and a consistent priority rubric. |
| Minimum summary counters | Not satisfied | Add direct `summary.critical`, `summary.high`, and `summary.medium` counters. |
| Specific, mechanism-sound fixes | Mixed | Remove unsupported guarantees, timestamp-only freshness fixes, and mandatory directory/profile advice. |
| Proactive improvements beyond defects | No separate representation | Add optional improvements without inflating defect counts. |
| Under five minutes, typical website | Not established | Shared page cache, fewer requests, a global deadline, bounded rendering, and measured representative runs. |
| ZIP at most 50 MB | Likely feasible, not verified | Build and inspect an explicit release ZIP; exclude environments, browsers, caches, and scratch output. |
| No pretrained model weights | None found in source inventory | Add a release artifact check. |
| Read-only/recommend-only | Intent mostly aligned | Enforce in the transport, browser, skill instructions, and report actions. |
| Never modify live websites | No explicit site-writing command found | Browser JavaScript and action-like GET URLs still require restriction. |
| No authenticated-area actions | Not enforced | Exclude sensitive/auth/action URLs and do not import credentials or browser profiles. |
| No rate abuse | Not enforced globally | Per-origin spacing, finite requests, cooldown on 429/503, and crawl-delay handling. |
| Respect robots.txt | Partial and inconsistent | One policy-aware collection path for every request and redirect. |
| Portable/provider-neutral | Partial | Remove assumed tool names, local absolute links, and fixed `/tmp` workflow dependencies. |

## 5. What already works conceptually and should be preserved

### 5.1 Crawl/access foundation

`robots_analyzer.py` implements grouped user agents, multiple directive groups, empty-rule handling, wildcard matching, end anchors, effective bot rules, and Allow winning equal-length ties. It distinguishes training-oriented policies from search/retrieval restrictions instead of treating all bot blocks identically.

`url_utils.py` normalizes URL identities, removes selected tracking parameters, and deliberately avoids collapsing trailing-slash differences. This is a useful starting point for repeatable crawl identities.

`sitemap_validator.py` supports sitemap indexes, child sitemaps, URL sets, duplicate analysis, lastmod analysis, parameter-variant candidates, and deterministic sample selection. Sample checks can fall back from HEAD to GET.

`page_fetcher.py` records status, redirect chain, timing, canonical data, robot directives, challenge indicators, TLS health, and a bounded BFS graph. CDN/WAF headers alone are already treated as contextual infrastructure signals rather than proof of a challenge.

### 5.2 Render/extraction foundation

`html_fetcher.py` inventories text, images, SVGs, canvases, videos, audio, iframes, PDF links, styles, tabs, scroll markers, and framework signatures. It has useful suppression concepts for decoration and background media.

`rendered_dom_extractor.py` captures visible/all text, DOMContentLoaded versus later text, hidden panels, lazy images, shadow roots, and load-more markers. Reusing these concepts is preferable to inventing an unrelated rendering subsystem.

### 5.3 Structured-data foundation

The extractor records JSON-LD blocks and JSON parse locations, handles graph-shaped data, extracts social/document metadata, and gathers visible headings/prices/dates. The validator already has a reusable CLI and separate check methods.

### 5.4 Freshness/entity foundation

The extractor handles several date formats, captures multiple date sources, extracts organization attributes and `sameAs`, gathers some claims, and generates search queries. The validator accepts a separate corroboration file, which is a useful provider-neutral integration point if its schema and evidence rules are strengthened.

### 5.5 Architecture and hygiene

The skill boundaries are genuine, files are mostly self-locating through `__file__`, configuration is externalized, and the README explains the diagnostic sequence clearly. These are useful assets to keep.

## 6. Composition, schema, and skill-format findings

### ARCH-01 â€” No executable orchestrator [P0]

**Evidence:** `skills/audit-orchestrator/scripts/` contains only `.gitkeep`; `SKILL.md` describes the sequence in prose.

**Impact:** There is no deterministic command that accepts a domain, composes all five detectors, handles failures, and emits the final report. An agent can follow the instructions, but this does not establish reliable automated composition.

**Recommendation:** Add a small entrypoint that collects a bounded snapshot, invokes genuine skill-owned analyses, merges validated findings, and emits JSON plus a readable presentation. Keep detection logic out of the orchestrator.

### ARCH-02 â€” Legacy finding/report schema violates the requested contract [P0]

**Evidence:** Skill examples and validators produce `finding_id`, `detail`, `affected_urls`, and `recommendation`. They do not produce the required `id`, `evidence`, or `suggested_action: {summary, priority}`. The orchestrator nests counts under `summary.by_severity`, whereas the minimum report needs direct counters.

**Recommendation:** Define one JSON Schema and a shared finding constructor/adapter. Retain useful details as additional fields, but always satisfy the required minimum. Validate actual output, not only examples.

### ARCH-03 â€” Evidence is not a first-class contract [P0]

**Evidence:** Some details quote parse errors or values, but the shared finding shape has no evidence object, selector/header name, source classification, observation timestamp, or explicit baseline.

**Impact:** Findings cannot consistently be traced back to the observation that caused them. Narrative claims may overstate what was actually collected.

**Recommendation:** Use evidence records such as `{url, source, locator, observed}` with the report timestamp and, where necessary, per-fetch timestamps. A contradiction needs both values and their attribution. A negative claim needs the inspected scope.

### ARCH-04 â€” Skip logic is based on severity rather than access state [P1]

**Evidence:** The orchestrator says downstream skills skip pages marked completely blocked with critical severity.

**Impact:** A particular bot's restriction is not the same as the audit client being forbidden to fetch. `noindex` does not mean the page is inaccessible. A timeout is not proof of a permanent site failure.

**Recommendation:** Pass explicit states: allowed, disallowed, fetched, non-HTML, challenged, unavailable, and not checked. Skip for concrete safety/access reasons, not a general severity label.

### ARCH-05 â€” No shared snapshot or reproducible evidence identity [P1]

Each skill refetches pages independently. The resulting page versions, request times, redirects, and rendered states may differ. This increases traffic and can create artificial contradictions.

**Recommendation:** Share immutable bounded page responses and observation metadata. Cache by safe request identity. Preserve source URLs and timestamps when composing skill results.

### ARCH-06 â€” Deduplication and IDs are only partly specified [P2]

The orchestrator describes deduplication but implements none. Existing validators renumber findings after sorting; IDs can shift when unrelated findings are added.

**Recommendation:** Use stable check identifiers and evidence-derived finding IDs. Merge only findings about the same mechanism and observation; do not merge distinct root causes merely because they affect the same URL.

### FORMAT-01 â€” Nonstandard/provider-specific frontmatter and tools [P1]

Render, structured-data, and freshness skills declare a `tools:` list including names such as `run_command`, `read_url_content`, `view_file`, and `search_web`. These are host-specific assumptions, not a provider-neutral guaranteed runtime. The standard optional field is `allowed-tools`, not an arbitrary `tools` list.

**Recommendation:** Use supported frontmatter and describe required capabilities in portable terms. Do not require a specific tool name for external search. Validate every listed skill independently.

### FORMAT-02 â€” Skill files duplicate lengthy checklists [P2]

Several `SKILL.md` files repeat detailed criteria already present under `references/`. This makes discovery expensive and lets severity rules drift between files.

**Recommendation:** Keep each skill focused on inputs, execution, outputs, safety, and references to its detailed checks. Do not remove useful detail; move it to a single maintained source.

### FORMAT-03 â€” Local absolute links and Unix-specific workflow examples [P2]

Some reference Markdown links point to developer-specific `file:///Users/...` or `file:///c:/Users/...` locations. Several commands use fixed `/tmp/...` files.

**Recommendation:** Use repository-relative links and user-selected output paths or platform-neutral temporary directories.

### FORMAT-04 â€” License declaration needs a consistency check [P2]

The skills declare `license: MIT`, but no root license file appeared in the inspected inventory. This is a distribution/documentation gap, not proof of a licensing violation.

**Recommendation:** Confirm the intended license and include its text if authorized; do not invent authorship or licensing terms.

## 7. Safety and runtime findings

### SAFE-01 â€” Untrusted URLs can reach non-public destinations [P0]

**Evidence:** Network functions accept input URLs, sitemap locations, canonical URLs, outbound links, and redirects without a shared public-destination policy.

**Impact:** Running against an untrusted site could request loopback/private/link-local infrastructure or an unintended service accessible from the auditor's machine. Checking only the initial URL is insufficient.

**Recommendation:** Restrict schemes and ports, reject URL credentials, resolve and reject non-public addresses, validate each redirect and child request, and address DNS rebinding rather than validating one resolution and connecting through another. Test IPv4, IPv6, encoded URLs, and hostname edge cases.

### SAFE-02 â€” Robots enforcement is incomplete and can fail open [P0]

**Evidence:** `load_audit_robots()` returns `None` when policy loading fails; callers continue when groups are `None`. Render, structured-data, and freshness fetch paths do not independently enforce robots. Redirect and canonical-target requests also bypass a complete policy check.

**Recommendation:** Centralize robots-aware fetching, cache policy per origin, and distinguish absent policy from unavailable policy. Conservatively skip ambiguous failures in this recommend-only auditor. Record the skip as coverage, not a content defect.

### SAFE-03 â€” Robots checks omit query strings and can use the wrong origin's policy [P0]

**Evidence:** BFS and sitemap sample checks pass only `urlparse(...).path` into `evaluate_bot()`. A sitemap may contain another host, but sample checking uses the original groups.

**Recommendation:** Evaluate the path plus query where applicable and load policy for the actual destination origin. Re-evaluate every redirect target before requesting it.

### SAFE-04 â€” Browser execution is not read-only by construction [P0]

**Evidence:** Playwright navigates pages with JavaScript enabled but has no route/method restrictions, sensitive-path filtering, service-worker restrictions, or browser request budget.

**Impact:** Page JavaScript can initiate POSTs or requests to authenticated/action endpoints even though the auditor itself never clicks a button.

**Recommendation:** Use a fresh credential-free context, block mutation methods, downloads and unsupported channels, restrict all browser requests through the same safety policy, and document that blocking traffic can make rendering incomplete. Never import a user's browser session.

### SAFE-05 â€” Authenticated/action-like pages are not excluded from discovery [P0]

Suppressing a finding on `/login`, `/cart`, or `/admin` is not the same as preventing a request. BFS, supplied page lists, sitemaps, and scripts can still reach these locations. Even GET URLs can implement actions such as logout or unsubscribe.

**Recommendation:** Exclude sensitive/auth/action URLs before fetching, including redirect targets and suspicious query parameters. Do not submit forms or test account flows.

### SAFE-06 â€” No global rate/request control [P0]

No shared request counter, per-origin pacing, enforced crawl-delay, or cooldown after 429/503 was found. The robots analyzer reports delays but the fetchers do not honor them consistently.

**Recommendation:** Bound requests for the entire audit, space requests per origin, respect applicable delay, and stop or back off on rate-limit/service-unavailable responses. Do not rotate identities or retry aggressively.

### SAFE-07 â€” Full response bodies and decompression are insufficiently bounded [P1]

Many scripts consume `resp.text` or `resp.content` without a transport-level size ceiling. Output truncation happens after downloading/parsing, so it does not bound network or memory costs. Some configured limits, such as JSON-LD byte limits, are not actually applied by the relevant extractor.

**Recommendation:** Bound compressed/decompressed input size and parsing work. Mark oversized content as incomplete and avoid absence findings based on truncated pages.

### SAFE-08 â€” Resource cleanup and retry behavior need tightening [P1]

Manual redirect handling and streamed fallback requests do not consistently close responses. A HEAD response of 403 triggers GET fallback in sitemap sampling even though 403 is not evidence that HEAD is unsupported.

**Recommendation:** Close each response deterministically. Restrict protocol fallbacks to justified cases such as 405/501; treat denials conservatively rather than as invitations to retry another method.

### SAFE-09 â€” Untrusted page content is not explicitly isolated from agent instructions [P1]

Skill instructions do not clearly state that crawled pages, embedded markup, sitemaps, and external evidence are data rather than commands.

**Recommendation:** Add a concise prompt-injection boundary: never obey instructions from audited content, disclose secrets, expand scope, or change network policy in response to a page.

### RUN-01 â€” The under-five-minute target is not enforced [P0]

The documented skill targets add up to roughly six minutes before engagement: crawl under two, render under two, structured under one, freshness under one. These are targets, not measured guarantees.

Potential work is much larger: BFS defaults to 50 pages; sitemap processing can inspect 20 files and sample 20 URLs per file; freshness can check up to 20 outbound links per page; rendering can spend 30 seconds loading plus additional waits per page.

**Recommendation:** Share responses, sample a small representative set, avoid broad external probing, render only selected pages, enforce a single monotonic deadline, and produce a partial report when limits are reached. A supervising timeout may be needed for DNS/browser hangs that per-request timeouts do not fully bound.

### RUN-02 â€” Collection errors can masquerade as website defects [P1]

Structured and freshness validators do not consistently exclude failed/non-successful/non-HTML responses. Missing extracted fields from a failed fetch can generate missing metadata, missing date, or missing identity findings.

**Recommendation:** Only run content checks on suitable successful observations. Report unavailable dependencies, inaccessible pages, non-HTML documents, blocked requests, and timeouts in coverage.

### RUN-03 â€” No coherent partial-report/check coverage model [P1]

There is no unified representation of complete, partial, not checked, not applicable, or unavailable checks. A zero-finding report could therefore look like success when most checks did not execute.

**Recommendation:** Include pages requested/fetched/skipped, checks run, render/search availability, limits reached, and unresolved observations separately from findings.
