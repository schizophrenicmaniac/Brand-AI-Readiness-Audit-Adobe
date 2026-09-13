#!/usr/bin/env python3
"""report.py — the one finding + report contract for the Brand AI Readiness Audit.

Every skill produces findings through `make_finding()` and the orchestrator assembles
them with `assemble_report()`. This is the single source of truth for the required
submission schema:

Finding:
    id, title, severity, skill, evidence{url, source, locator, observed[, expected]},
    suggested_action{summary, priority}

Report:
    site, audited_at, pages_audited, summary{total_findings, critical, high, medium,
    low, info}, coverage{...}, findings[], proactive_improvements[]

`validate_report()` enforces the schema so we never ship a malformed report.
"""

import hashlib
from datetime import datetime, timezone

try:
    from jsonschema import Draft7Validator
except ImportError:  # jsonschema is a hard dependency; fail loudly if missing.
    Draft7Validator = None

SEVERITIES = ("critical", "high", "medium", "low", "info")
_SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}

# severity -> action priority. Key-fact escalation is already baked into severity
# by the analyzers, so priority is a straight mapping.
_PRIORITY_BY_SEVERITY = {
    "critical": "P0",
    "high": "P1",
    "medium": "P2",
    "low": "P3",
    "info": "P3",
}

# skill id -> short finding-id prefix
SKILL_PREFIX = {
    "crawl-access-audit": "ca",
    "render-extraction-audit": "re",
    "structured-data-audit": "sd",
    "freshness-corroboration-audit": "fc",
    "engagement-audit": "eg",
    "audit-orchestrator": "or",
}

# allowed evidence.source values (where the observation came from)
EVIDENCE_SOURCES = (
    "http_header", "http_status", "html", "robots_txt", "sitemap",
    "rendered_dom", "tls", "external", "llms_txt", "crawl_graph",
)


def severity_to_priority(severity: str) -> str:
    return _PRIORITY_BY_SEVERITY.get(severity, "P3")


def stable_finding_id(skill: str, check_id: str, locator: str, url: str) -> str:
    """Deterministic id that does NOT shift when unrelated findings are added.

    Derived from the mechanism (check_id) + where it was observed (locator+url), so
    the same defect on the same page keeps the same id across runs.
    """
    prefix = SKILL_PREFIX.get(skill, "xx")
    digest = hashlib.sha1(f"{check_id}|{locator}|{url}".encode("utf-8")).hexdigest()[:6]
    return f"{prefix}-{digest}"


def make_finding(skill, check_id, severity, title, action_summary,
                 evidence_url, evidence_source, evidence_observed,
                 evidence_locator="", evidence_expected=None, priority=None):
    """Build one contract-shaped finding.

    check_id is the stable internal check (e.g. "CA-01") used only for the id hash;
    it is not part of the output shape.
    """
    if severity not in SEVERITIES:
        raise ValueError(f"invalid severity: {severity!r}")
    if evidence_source not in EVIDENCE_SOURCES:
        raise ValueError(f"invalid evidence.source: {evidence_source!r}")

    evidence = {
        "url": evidence_url,
        "source": evidence_source,
        "locator": evidence_locator,
        "observed": evidence_observed,
    }
    if evidence_expected is not None:
        evidence["expected"] = evidence_expected

    return {
        "id": stable_finding_id(skill, check_id, evidence_locator, evidence_url),
        "title": title,
        "severity": severity,
        "skill": skill,
        "evidence": evidence,
        "suggested_action": {
            "summary": action_summary,
            "priority": priority or severity_to_priority(severity),
        },
    }


def _sort_key(finding):
    return (
        _SEVERITY_RANK.get(finding.get("severity"), 99),
        finding.get("skill", ""),
        finding.get("id", ""),
    )


def dedupe_findings(findings):
    """Drop exact duplicates (same id). Distinct mechanisms keep distinct ids, so this
    only collapses the literal 'two skills reported the identical observation' case."""
    seen = {}
    for f in findings:
        seen.setdefault(f.get("id"), f)
    return list(seen.values())


def collapse_repeats(findings):
    """Collapse the same issue reported once per page into a single finding.

    Site-wide defects (e.g. a WebSite/Organization schema missing the same recommended
    properties on every page) otherwise produce N near-identical findings, which reads as
    noise in a non-expert report. Findings that share (skill, severity, title, fix summary)
    are merged into the first, and the other affected URLs are folded into evidence.observed.
    Page-specific findings (whose titles differ per page) are unaffected.
    """
    groups = {}
    order = []
    for f in findings:
        key = (f.get("skill"), f.get("severity"), f.get("title"),
               f.get("suggested_action", {}).get("summary"))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)

    out = []
    for key in order:
        group = groups[key]
        first = group[0]
        if len(group) > 1:
            urls = []
            for f in group:
                u = f.get("evidence", {}).get("url", "")
                if u and u not in urls:
                    urls.append(u)
            if len(urls) > 1:
                shown = ", ".join(urls[:6]) + (" …" if len(urls) > 6 else "")
                ev = first.setdefault("evidence", {})
                base = ev.get("observed", "")
                ev["observed"] = f"{base}  Affects {len(urls)} pages: {shown}."
        out.append(first)
    return out


def assemble_report(site, findings, coverage=None, pages_audited=0,
                    proactive_improvements=None, audited_at=None):
    findings = dedupe_findings(findings)
    findings = collapse_repeats(findings)
    findings.sort(key=_sort_key)

    counts = {s: 0 for s in SEVERITIES}
    for f in findings:
        sev = f.get("severity", "info")
        if sev in counts:
            counts[sev] += 1

    summary = {"total_findings": len(findings)}
    summary.update(counts)  # critical, high, medium, low, info

    return {
        "site": site,
        "audited_at": audited_at or datetime.now(timezone.utc).isoformat(),
        "pages_audited": pages_audited,
        "summary": summary,
        "coverage": coverage or {},
        "findings": findings,
        "proactive_improvements": proactive_improvements or [],
    }


REPORT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["site", "audited_at", "summary", "findings"],
    "properties": {
        "site": {"type": "string", "minLength": 1},
        "audited_at": {"type": "string", "minLength": 1},
        "pages_audited": {"type": "integer", "minimum": 0},
        "summary": {
            "type": "object",
            "required": ["total_findings", "critical", "high", "medium"],
            "properties": {
                "total_findings": {"type": "integer", "minimum": 0},
                "critical": {"type": "integer", "minimum": 0},
                "high": {"type": "integer", "minimum": 0},
                "medium": {"type": "integer", "minimum": 0},
                "low": {"type": "integer", "minimum": 0},
                "info": {"type": "integer", "minimum": 0},
            },
        },
        "coverage": {"type": "object"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "title", "severity", "evidence", "suggested_action"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "title": {"type": "string", "minLength": 1},
                    "severity": {"enum": list(SEVERITIES)},
                    "skill": {"type": "string"},
                    "evidence": {
                        "type": "object",
                        "required": ["url", "source", "observed"],
                        "properties": {
                            "url": {"type": "string"},
                            "source": {"type": "string"},
                            "locator": {"type": "string"},
                            "observed": {"type": "string"},
                            "expected": {"type": "string"},
                        },
                    },
                    "suggested_action": {
                        "type": "object",
                        "required": ["summary", "priority"],
                        "properties": {
                            "summary": {"type": "string", "minLength": 1},
                            "priority": {"enum": ["P0", "P1", "P2", "P3"]},
                        },
                    },
                },
            },
        },
        "proactive_improvements": {"type": "array"},
    },
}


def validate_report(report):
    """Raise if the report violates the schema or semantic contract."""
    if Draft7Validator is None:
        raise RuntimeError("jsonschema not installed; cannot validate report")
    errors = sorted(Draft7Validator(REPORT_SCHEMA).iter_errors(report),
                    key=lambda e: e.path)
    if errors:
        msgs = "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:8])
        raise ValueError(f"report failed schema validation: {msgs}")

    semantic_errors = []
    findings = report["findings"]
    seen_ids = set()
    duplicate_ids = set()
    for finding in findings:
        finding_id = finding["id"]
        if finding_id in seen_ids:
            duplicate_ids.add(finding_id)
        seen_ids.add(finding_id)
    duplicate_ids = sorted(duplicate_ids)
    if duplicate_ids:
        semantic_errors.append(f"finding ids must be unique: {', '.join(duplicate_ids[:8])}")

    actual_counts = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        actual_counts[finding["severity"]] += 1
    summary = report["summary"]
    if summary["total_findings"] != len(findings):
        semantic_errors.append(
            f"summary.total_findings is {summary['total_findings']}, expected {len(findings)}"
        )
    for severity in SEVERITIES:
        if severity in summary and summary[severity] != actual_counts[severity]:
            semantic_errors.append(
                f"summary.{severity} is {summary[severity]}, expected {actual_counts[severity]}"
            )

    audited_at = report["audited_at"]
    try:
        parsed_at = datetime.fromisoformat(audited_at.replace("Z", "+00:00"))
        if parsed_at.tzinfo is None or parsed_at.utcoffset() != timezone.utc.utcoffset(parsed_at):
            semantic_errors.append("audited_at must be an ISO 8601 timestamp in UTC")
    except (TypeError, ValueError):
        semantic_errors.append("audited_at must be a valid ISO 8601 timestamp in UTC")

    if semantic_errors:
        raise ValueError(f"report failed semantic validation: {'; '.join(semantic_errors[:12])}")
    return report
