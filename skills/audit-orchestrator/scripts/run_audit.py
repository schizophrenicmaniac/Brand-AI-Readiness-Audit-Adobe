#!/usr/bin/env python3
"""run_audit.py — the Brand AI Readiness Audit entrypoint.

Takes a website URL/domain, composes the five detection skills over ONE safe, cached
fetch pass, and emits a single schema-valid report (JSON + readable Markdown).

Composition:
    1. crawl-access        — robots, sitemap, status/redirect/canonical/TLS, crawl graph
    2. render-extraction   — JS/render gaps, opaque media (static; browser if available)
    3. structured-data     — JSON-LD / OG / meta / llms.txt
    4. freshness-corrob.   — dates, staleness, entity grounding, corroboration
    5. engagement          — mobile viewport, orientation, nav, breadcrumbs, CTAs

Safety (all fetches route through lib/safe_http.SafeSession):
    read-only GET/HEAD only, SSRF guard (public hosts / ports 80,443 / no credentials,
    re-checked on redirects), per-origin rate spacing, a single global deadline, response
    size cap, and response caching so pages are fetched once across all skills. Robots is
    enforced here at page selection (only allowed URLs enter the fetch set); auth/action
    URLs (login, cart, admin, logout, …) are excluded before any request.

Usage:
    python run_audit.py https://example.com
    python run_audit.py example.com --max-pages 8 --out-dir ./out --budget-seconds 240
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

# --- import paths: shared lib + every skill's scripts dir --------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
_SKILLS = os.path.join(_ROOT, "skills")
for _p in [
    os.path.join(_ROOT, "lib"),
    os.path.join(_SKILLS, "crawl-access-audit", "scripts"),
    os.path.join(_SKILLS, "render-extraction-audit", "scripts"),
    os.path.join(_SKILLS, "structured-data-audit", "scripts"),
    os.path.join(_SKILLS, "freshness-corroboration-audit", "scripts"),
    os.path.join(_SKILLS, "engagement-audit", "scripts"),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import safe_http                                   # noqa: E402
from safe_http import DeadlineExceeded, UnsafeRequestError  # noqa: E402
from report import assemble_report, validate_report  # noqa: E402

UA = "BrandAIReadinessAudit"
_AUTH_PATTERNS = re.compile(
    r"(^|/)(login|log-in|signin|sign-in|signup|sign-up|register|logout|log-out|sign-out|"
    r"account|my-account|cart|checkout|basket|admin|wp-admin|wp-login|dashboard|oauth|sso|"
    r"unsubscribe|billing|payment)(/|$|\.)", re.IGNORECASE)
_AUTH_QUERY = re.compile(r"(?:^|&)(action|logout|token|unsubscribe|confirm)=", re.IGNORECASE)


def _is_auth_path(url: str) -> bool:
    p = urlparse(url)
    return bool(_AUTH_PATTERNS.search(p.path or "") or _AUTH_QUERY.search(p.query or ""))


def _normalize_input(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    p = urlparse(raw)
    origin = f"{p.scheme}://{p.netloc}"
    return origin + (p.path if p.path else "/")


def _reg_host(h: str) -> str:
    """Registrable host for same-site comparison: drop a leading 'www.'. Most sites
    canonicalize apex<->www (e.g. input example.com but the sitemap lists www.example.com),
    so an exact-hostname match would reject every interior page and audit only the homepage."""
    h = (h or "").lower()
    return h[4:] if h.startswith("www.") else h


def _choose_pages(home, sitemap_page_urls, robots_groups, max_pages):
    import url_utils
    from robots_analyzer import evaluate_bot
    host = _reg_host(urlparse(home).hostname)
    priority_kw = ("/about", "/pricing", "/price", "/product", "/services", "/service",
                   "/contact", "/blog", "/faq", "/features", "/plans", "/solutions")

    def score(u):
        pl = urlparse(u).path.lower()
        return 0 if any(k in pl for k in priority_kw) else 1

    seen = {url_utils.normalize_url(home)}
    candidates = []
    for u in sorted(set(sitemap_page_urls or [])):
        pu = urlparse(u)
        if _reg_host(pu.hostname) != host:
            continue
        n = url_utils.normalize_url(u)
        if n in seen:
            continue
        seen.add(n)
        candidates.append(u)
    candidates.sort(key=lambda u: (score(u), u))

    chosen = [home]
    skipped_auth, skipped_robots = [], []
    for u in candidates:
        if len(chosen) >= max_pages:
            break
        if _is_auth_path(u):
            skipped_auth.append(u)
            continue
        path = urlparse(u).path or "/"
        if robots_groups and evaluate_bot(robots_groups, UA, path)["blocked"]:
            skipped_robots.append(u)
            continue
        chosen.append(u)
    return chosen, skipped_auth, skipped_robots


def _run_skill(name, fn, coverage):
    """Run one skill; deadline/errors degrade to an empty list + a coverage note."""
    try:
        result = fn() or []
        coverage["checks_run"].append(name)
        return result
    except DeadlineExceeded:
        coverage["deadline_hit"] = True
        coverage["incomplete"].append(name)
        return []
    except Exception as e:  # never let one skill sink the whole report
        coverage["errors"].append(f"{name}: {str(e)[:200]}")
        return []


def audit(raw_url, max_pages=8, budget_seconds=240, allow_private=False):
    home = _normalize_input(raw_url)
    site_host = urlparse(home).hostname or home

    coverage = {
        "requested": home, "max_pages": max_pages, "budget_seconds": budget_seconds,
        "pages_selected": [], "skipped_auth_action": [], "skipped_by_robots": [],
        "checks_run": [], "incomplete": [], "errors": [],
        "render_available": False, "deadline_hit": False,
    }

    # Refuse to audit unsafe targets (SSRF): emit a valid, empty report explaining why.
    safe_http.install(allow_private=allow_private, spacing=0.3)
    safe_http.set_deadline_seconds(budget_seconds)
    try:
        safe_http.validate_url(home, allow_private=allow_private)
    except UnsafeRequestError as e:
        coverage["errors"].append(f"target refused by safety policy: {e}")
        safe_http.uninstall()
        return assemble_report(site_host, [], coverage=coverage, pages_audited=0,
                               proactive_improvements=[])

    import robots_analyzer
    import sitemap_validator
    import page_fetcher
    import html_fetcher
    import render_analyzer
    import crawl_analyzer
    import engagement_analyzer
    import structured_data_extractor
    import freshness_extractor
    from schema_validator import SchemaValidator
    from freshness_validator import FreshnessValidator

    findings = []
    robots = sitemap = pages_raw = {}
    robots_groups = []

    # --- discovery: robots + sitemap + page selection -----------------------
    try:
        robots = robots_analyzer.analyze(home, robots_analyzer.DEFAULT_CRITICAL_PATHS)
        robots_groups = robots_analyzer.parse_robots_txt(robots.get("raw_content") or "")
    except DeadlineExceeded:
        coverage["deadline_hit"] = True
    except Exception as e:
        coverage["errors"].append(f"robots: {str(e)[:200]}")

    try:
        sitemap = sitemap_validator.analyze(home, robots.get("sitemap_urls") or None)
    except DeadlineExceeded:
        coverage["deadline_hit"] = True
    except Exception as e:
        coverage["errors"].append(f"sitemap: {str(e)[:200]}")

    coverage["discovery_resources"] = {
        "robots_txt": robots.get("url") if robots.get("status") == 200 else None,
        "sitemaps": [s.get("url") for s in (sitemap or {}).get("sitemaps_found", []) if s.get("url")],
        "llms_txt": None,
    }

    chosen, skipped_auth, skipped_robots = _choose_pages(
        home, (sitemap or {}).get("page_urls"), robots_groups, max_pages)
    coverage["pages_selected"] = chosen
    coverage["skipped_auth_action"] = skipped_auth
    coverage["skipped_by_robots"] = skipped_robots
    interior = chosen[1:]

    # --- crawl-access -------------------------------------------------------
    def _crawl():
        nonlocal pages_raw
        pages_raw = page_fetcher.analyze(
            home, page_paths=chosen,
            max_pages=min(max_pages, 8), max_depth=2,
            sitemap_urls=(sitemap or {}).get("page_urls"))
        return crawl_analyzer.compile_findings(robots, sitemap, pages_raw)
    findings += _run_skill("crawl-access-audit", _crawl, coverage)

    # Coverage fallback: when the site has no usable sitemap, page selection above yields
    # only the homepage. Reuse the internal links the crawl graph already discovered so the
    # content skills (render/structured/freshness/engagement) audit real interior pages too,
    # not just the homepage. Robots/auth filtering is re-applied by _choose_pages.
    if len(chosen) <= 1:
        discovered = (pages_raw.get("crawl_graph", {}) or {}).get("discovered_urls", [])
        if discovered:
            chosen, extra_auth, extra_robots = _choose_pages(home, discovered, robots_groups, max_pages)
            interior = chosen[1:]
            coverage["pages_selected"] = chosen
            coverage["skipped_auth_action"] = sorted(set(skipped_auth + extra_auth))
            coverage["skipped_by_robots"] = sorted(set(skipped_robots + extra_robots))
            coverage["interior_from_crawl_graph"] = bool(interior)

    # --- render-extraction (static; browser if Playwright present) ----------
    def _render():
        raw_html = html_fetcher.analyze(home, page_paths=chosen, max_pages=max_pages)
        rendered = None
        try:
            import playwright  # noqa: F401 - probe for playwright before importing extractor
            import rendered_dom_extractor
            rendered = rendered_dom_extractor.analyze(home, page_paths=chosen,
                                                      max_pages=max_pages, raw_data=raw_html)
            coverage["render_available"] = True
        except (ImportError, SystemExit, Exception):
            rendered = None       # Playwright not installed -> degrade cleanly to static-only
        return render_analyzer.compile_findings(raw_html, rendered)
    findings += _run_skill("render-extraction-audit", _render, coverage)

    # --- structured-data ----------------------------------------------------
    import requests  # patched to SafeSession by install()

    def _structured():
        session = requests.Session()
        session.headers.update({"User-Agent": UA})
        raw = structured_data_extractor.build_raw(home, interior, session, max_pages)
        if raw.get("llms_txt", {}).get("/llms.txt", {}).get("present"):
            coverage["discovery_resources"]["llms_txt"] = raw["llms_txt"]["/llms.txt"].get("url")
        return SchemaValidator(raw).run_all()
    findings += _run_skill("structured-data-audit", _structured, coverage)

    # --- freshness-corroboration -------------------------------------------
    def _freshness():
        session = requests.Session()
        session.headers.update({"User-Agent": UA})
        raw = freshness_extractor.build_raw(home, interior, session, max_pages, check_dead_links=True)
        return FreshnessValidator(raw).run_all()
    findings += _run_skill("freshness-corroboration-audit", _freshness, coverage)

    # --- engagement ---------------------------------------------------------
    def _engagement():
        pages = engagement_analyzer._fetch_pages(home, [urlparse(u).path for u in interior])
        return engagement_analyzer.compile_findings(pages)
    findings += _run_skill("engagement-audit", _engagement, coverage)

    safe_http.uninstall()

    report = assemble_report(
        site_host, findings, coverage=coverage,
        pages_audited=len(chosen),
        proactive_improvements=_proactive(findings))
    return report


def _proactive(findings):
    """Forward-looking opportunities beyond detected defects (not counted as findings)."""
    has_valid_llms = any(
        "valid /llms.txt" in f.get("title", "").lower()
        or "sd-06-llms-valid" in f.get("id", "")
        for f in findings
    )
    items = []
    if not has_valid_llms:
        items.append({
            "title": "Publish an llms.txt index for AI agents",
            "summary": "Add /llms.txt summarizing the brand, key products, and canonical doc links "
                       "so AI web agents can orient quickly.",
            "priority": "P3",
        })
    items.extend([
        {"title": "Add FAQPage / HowTo schema where you have Q&A or step content",
         "summary": "Structured Q&A and step markup are directly consumable by AI answer engines "
                    "and increase citation odds.",
         "priority": "P3"},
        {"title": "Keep sameAs + Wikidata grounding current",
         "summary": "Maintain sameAs links and a Wikidata item so assistants disambiguate the brand "
                    "and trust its facts.",
         "priority": "P3"},
    ])
    return items


# ---------------------------------------------------------------------------
# Professional Markdown Report Formatting
# ---------------------------------------------------------------------------

def to_markdown(report):
    s = report["summary"]
    audited_time = report.get("audited_at", "")
    try:
        dt = datetime.fromisoformat(audited_time)
        formatted_date = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        formatted_date = audited_time

    lines = [
        f"# Brand AI Readiness Audit Report",
        "",
        f"**Target Site:** `{report['site']}`  ",
        f"**Audited At:** {formatted_date}  ",
        f"**Pages Audited:** {report.get('pages_audited', 0)}  ",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        "| Severity | Findings | Priority | Action Guidance |",
        "| :--- | :---: | :---: | :--- |",
        f"| **Critical** | {s.get('critical', 0)} | P0 | Immediate blocker for machine discovery or brand safety |",
        f"| **High** | {s.get('high', 0)} | P1 | High impact on AI visibility, citation, or conversion |",
        f"| **Medium** | {s.get('medium', 0)} | P2 | Material friction in crawling, freshness, or schema depth |",
        f"| **Low** | {s.get('low', 0)} | P3 | Quality hygiene, minor schema, or edge optimization |",
        f"| **Info** | {s.get('info', 0)} | P3 | Informational observation or positive verification |",
        f"| **Total** | **{s['total_findings']}** | - | - |",
        "",
        "---",
        "",
    ]
    cov = report.get("coverage", {})
    disc = cov.get("discovery_resources", {})
    disc_items = []
    if disc.get("robots_txt"):
        disc_items.append("`robots.txt`")
    if disc.get("sitemaps"):
        disc_items.append(f"{len(disc['sitemaps'])} XML sitemap(s)")
    if disc.get("llms_txt"):
        disc_items.append("`llms.txt`")
    disc_line = f"- **Discovery & Protocol Resources:** {', '.join(disc_items)}" if disc_items else ""

    lines += [
        "## Audit Scope & Coverage",
        "",
        f"- **Audited Pages:** {len(cov.get('pages_selected', []))} pages analyzed",
    ]
    if disc_line:
        lines.append(disc_line)
    lines += [
        f"- **Detection Skills Executed:** {', '.join(cov.get('checks_run', [])) or 'None'}",
        f"- **Headless Browser Rendering:** {'Enabled (Playwright)' if cov.get('render_available') else 'Static Analysis Only'}",
    ]
    if cov.get("deadline_hit"):
        lines.append(f"- **Notice:** Global time budget reached. Incomplete checks: "
                     f"{', '.join(cov.get('incomplete', [])) or 'N/A'}")
    if cov.get("skipped_auth_action"):
        lines.append(f"- **Excluded Paths (Auth/Conversion Safety):** {len(cov['skipped_auth_action'])} URLs skipped")
    if cov.get("errors"):
        lines.append(f"- **Execution Notes:** {'; '.join(cov['errors'])}")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines += ["## Detailed Findings", ""]
    if not report.get("findings"):
        lines.append("_No findings identified. The audited properties meet baseline criteria._")
        lines.append("")
    for f in report.get("findings", []):
        sev = f["severity"].upper()
        act = f.get("suggested_action", {})
        ev = f.get("evidence", {})
        priority = act.get("priority", "P3")

        expected_str = f" *(Expected: `{ev['expected']}`)*" if ev.get("expected") else ""
        lines += [
            f"### [{sev} | {priority}] {f['title']}",
            "",
            f"- **Severity:** {f['severity'].capitalize()} (`{priority}`)",
            f"- **Skill:** `{f.get('skill', '')}`  |  **Finding ID:** `{f['id']}`",
            f"- **Target URL:** {ev.get('url', '')}",
            f"- **Evidence:** `{ev.get('locator') or ev.get('source', '')}` - {ev.get('observed', '')}{expected_str}",
            f"- **Remediation:** {act.get('summary', '')}",
            "",
        ]

    if report.get("proactive_improvements"):
        lines += ["---", "", "## Strategic Proactive Opportunities", ""]
        for p in report["proactive_improvements"]:
            lines.append(f"#### {p.get('title')}")
            lines.append(f"{p.get('summary')}")
            lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Brand AI Readiness Audit — one report for a website.")
    ap.add_argument("url", help="Website URL or bare domain (e.g. https://example.com or example.com)")
    ap.add_argument("--max-pages", type=int, default=8, help="Max pages to audit (default 8)")
    ap.add_argument("--budget-seconds", type=int, default=240, help="Global time budget (default 240)")
    ap.add_argument("--out-dir", default=".", help="Directory for report.json + report.md")
    ap.add_argument("--allow-private", action="store_true",
                    help="Allow private/loopback targets (LOCAL TESTING ONLY)")
    ap.add_argument("--print", dest="do_print", action="store_true", help="Print JSON to stdout")
    args = ap.parse_args()

    started = time.monotonic()
    report = audit(args.url, max_pages=args.max_pages,
                   budget_seconds=args.budget_seconds, allow_private=args.allow_private)
    report["elapsed_seconds"] = round(time.monotonic() - started, 1)

    validate_report(report)  # raises if the contract is violated

    os.makedirs(args.out_dir, exist_ok=True)
    json_path = os.path.join(args.out_dir, "report.json")
    md_path = os.path.join(args.out_dir, "report.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(to_markdown(report))

    s = report["summary"]
    print(f"Audited {report['site']}: {s['total_findings']} findings "
          f"({s.get('critical', 0)} critical, {s.get('high', 0)} high, {s.get('medium', 0)} medium) "
          f"in {report['elapsed_seconds']}s")
    print(f"Report: {json_path}  +  {md_path}")
    if args.do_print:
        print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
