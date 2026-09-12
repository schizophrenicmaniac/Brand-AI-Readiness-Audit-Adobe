# Brand AI Readiness Audit

A marketplace of Agent Skills that audits a website's readiness for AI-driven **discovery,
citation, and engagement**. Given any URL or bare domain, it composes five detection skills
over one safe, cached fetch pass and emits a single evidence-backed report with prioritized,
mechanism-sound fixes.

## Quick start

```bash
pip install -r requirements.txt
python skills/audit-orchestrator/scripts/run_audit.py https://example.com
# writes report.json + report.md, prints a one-line summary
```

Options: `--max-pages 8`, `--budget-seconds 240`, `--out-dir ./out`. Playwright is optional
(see `requirements.txt`); without it, render-extraction uses static heuristics.

## Skills

| Skill | Purpose |
|-------|---------|
| **audit-orchestrator** *(entrypoint)* | Normalizes the URL, enforces safety, selects pages, invokes the five detection skills over one cached fetch pass, de-dupes, and assembles + validates the final report. |
| **crawl-access-audit** | Can an AI/search crawler reach, fetch, and index the pages? robots.txt (AI-bot-aware), sitemaps, status/soft-404, redirects, canonicals, latency, bot-challenge, TLS, crawl depth/orphans. |
| **render-extraction-audit** | Is human-visible content machine-readable? JS/render gaps (SPA shell, AJAX, shadow DOM), opaque media (images without alt, canvas, video/audio without transcript, PDF-only facts), boilerplate ratio. |
| **structured-data-audit** | Are facts handed to machines pre-labeled? JSON-LD/schema.org by page type, syntactic/semantic validity, Open Graph, Twitter cards, title/meta, llms.txt, schema-vs-DOM consistency. |
| **freshness-corroboration-audit** | Are facts current, cross-confirmed, and unambiguous? temporal metadata, staleness signals, dead links, external corroboration, sameAs disambiguation, Wikipedia/Wikidata grounding. |
| **engagement-audit** | Do AI-referred visitors stay? mobile viewport, `<h1>` orientation, `<nav>` landmark, interior-page breadcrumbs, ambiguous-CTA overload. |

`marketplace.json` lists all six skills and marks exactly one (`audit-orchestrator`) as the
entrypoint. Each skill has a valid agentskills.io `SKILL.md` and can also be run standalone via
its own `scripts/`.

## Architecture

```
audit-orchestrator (entrypoint)  ── scripts/run_audit.py
 │   normalize + SSRF-check → safe cached fetch → robots-filtered page set
 ├─ 1. crawl-access-audit            can the crawler get in?
 ├─ 2. render-extraction-audit       can the machine read what's there?
 ├─ 3. structured-data-audit         are facts pre-labeled?
 ├─ 4. freshness-corroboration-audit is it fresh and cross-confirmed?
 └─ 5. engagement-audit              do arriving visitors stay?
 →   merge + dedupe + schema-validate → report.json + report.md
```

Shared code lives in `lib/`:
- **`lib/safe_http.py`** — `SafeSession` routes every HTTP call through one policy: SSRF guard
  (public hosts only, ports 80/443, no credentials, re-checked on redirects), per-origin rate
  spacing, a single global deadline, response size cap, and a response cache (a page is fetched
  once across all skills). `install()` swaps `requests.Session` so all skills inherit it.
- **`lib/report.py`** — the one finding + report contract (`make_finding`, `assemble_report`,
  `REPORT_SCHEMA`, `validate_report`).

The orchestrator selects a page once; all skills read from the shared cache, which keeps the
whole audit within the time budget and avoids hammering the origin.

## Output contract

Every finding:

```json
{
  "id": "ca-3f9a2c",
  "title": "AI search/retrieval crawlers blocked by robots.txt",
  "severity": "critical | high | medium | low | info",
  "skill": "crawl-access-audit",
  "evidence": { "url": "…", "source": "robots_txt", "locator": "Disallow", "observed": "…", "expected": "…?" },
  "suggested_action": { "summary": "specific, mechanism-sound fix", "priority": "P0" }
}
```

Every report guarantees at least:

```json
{
  "site": "example.com",
  "audited_at": "ISO-8601",
  "summary": { "total_findings": 0, "critical": 0, "high": 0, "medium": 0 },
  "findings": []
}
```

It also adds `pages_audited`, full `summary` (with `low`/`info`), a `coverage` block (pages
selected/skipped, checks run, render availability, deadline flag, errors), and
`proactive_improvements`. `validate_report()` enforces the contract before writing.

- **Stable ids:** derived from `check + evidence locator + url` — they don't shift when other
  findings are added or the list is re-sorted.
- **Priority:** critical→P0, high→P1, medium→P2, low/info→P3.

## Guardrails

Read-only, recommend-only. The auditor never modifies a live site, submits forms, or touches
authenticated/action endpoints (login, cart, admin, logout, … are excluded before any request).
It respects robots.txt for the audited origin, spaces requests per origin, caps total work under
a global time budget (default 240 s, target < 5 min), bounds response size, blocks non-public
network destinations (SSRF), and treats all crawled content as untrusted data — never as
instructions.

## Packaging

Ship the repo as-is (skills + `lib/` + `marketplace.json` + `README.md` + `requirements.txt`).
Exclude virtualenvs, `__pycache__`, browser binaries, and scratch output (see `.gitignore`).
The result is well under 50 MB and contains no pretrained model weights.

## Testing

```bash
python test_audit.py          # offline: analyzers, contract, schema, SSRF/robots guards
```

The test suite runs without network using crafted HTML/JSON fixtures, so it is deterministic
and safe in restricted environments. An end-to-end live run
(`python skills/audit-orchestrator/scripts/run_audit.py <url>`) requires outbound network.

## Limitations

- Without a sitemap, interior-page coverage falls back to homepage + crawl-graph discovery.
- Render-extraction's JS-gap evidence is strongest with Playwright installed.
- External corroboration reports confirmed sources only when corroboration data is supplied;
  otherwise it flags claims as pending verification (never fabricates a source).
- Engagement is the lean core set; deeper UX checks (context retention, LCP, tap targets,
  on-site search) are a planned follow-up.
