---
name: audit-orchestrator
description: >
  Entrypoint skill that takes a URL, invokes all five detection skills in
  sequence, collects findings, deduplicates, normalizes severity, and assembles
  the single fixed-schema JSON audit report.
license: MIT
---

# Audit Orchestrator

> **Role:** Composition and report assembly — not a detection skill itself.

This skill is the single entrypoint for the Brand AI Readiness Audit. It owns
the invocation order, the report contract, and the cross-skill deduplication
logic. None of the five detection skills are responsible for those concerns.

---

## Invocation Sequence

The orchestrator runs the five detection skills in this order, since each layer
gates the next:

```
1. crawl-access-audit          — Can the crawler even get in?
2. render-extraction-audit     — Can the machine read what's there?
3. structured-data-audit       — Are facts pre-labeled for machines?
4. freshness-corroboration-audit — Is the content fresh and cross-confirmed?
5. engagement-audit            — Do arriving visitors stay and engage?
```

> **Short-circuit rule:** If `crawl-access-audit` finds a page is completely
> blocked (severity = critical), downstream skills skip that page and note the
> dependency in their output.

---

## Procedure

1. **Accept input:** a URL (single page or site root) and optional configuration (max pages to audit, page-type hints).
2. **Run `crawl-access-audit`** on the target. Collect findings. Identify pages marked as unreachable/blocked.
3. **Run `render-extraction-audit`** on reachable pages. Collect findings.
4. **Run `structured-data-audit`** on reachable pages. Collect findings.
5. **Run `freshness-corroboration-audit`** on reachable pages. Collect findings.
6. **Run `engagement-audit`** on reachable pages. Collect findings.
7. **Deduplicate.** Where two skills flag the same underlying issue (e.g., missing JSON-LD found by both structured-data-audit and render-extraction-audit), keep the more specific finding and mark the other as a duplicate.
8. **Normalize severity.** Ensure severity labels are applied consistently across skills using the unified rubric below.
9. **Sort by priority:** critical → high → medium → low → info.
10. **Assemble the final report** in the fixed output schema.

---

## Output Schema

```json
{
  "site": "https://example.com",
  "audited_at": "2026-08-27T15:04:00Z",
  "pages_audited": 11,
  "summary": {
    "total_findings": 17,
    "by_severity": {
      "critical": 2,
      "high": 5,
      "medium": 6,
      "low": 3,
      "info": 1
    },
    "by_skill": {
      "crawl-access-audit": 3,
      "render-extraction-audit": 4,
      "structured-data-audit": 4,
      "freshness-corroboration-audit": 3,
      "engagement-audit": 3
    }
  },
  "findings": [
    {
      "finding_id": "ca-001",
      "skill": "crawl-access-audit",
      "severity": "critical",
      "title": "...",
      "detail": "...",
      "affected_urls": ["..."],
      "recommendation": "..."
    }
  ]
}
```

---

## Unified Severity Rubric

| Severity | Definition |
|----------|------------|
| **critical** | The issue completely prevents a machine or visitor from accessing or using the content. Immediate fix required. |
| **high** | The issue significantly degrades machine readability, citation confidence, or visitor experience. Should be fixed soon. |
| **medium** | The issue causes partial degradation — content is accessible but with friction or data loss. Plan to fix. |
| **low** | Minor hygiene issue that marginally affects quality. Fix opportunistically. |
| **info** | Observation or positive signal. No action needed. |
