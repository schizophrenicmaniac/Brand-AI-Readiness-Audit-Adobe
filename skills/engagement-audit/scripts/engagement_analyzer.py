#!/usr/bin/env python3
"""engagement_analyzer.py — lean on-site engagement checks in the report contract.

Core-first scope: a small set of high-signal, low-false-positive checks over the page
HTML that AI-referred visitors land on — mobile viewport, page orientation (<h1>),
navigation landmark, breadcrumbs on interior pages, and ambiguous call-to-action
overload. Deeper checks (context retention, LCP, tap-target sizing, on-site search) are
deferred to a later pass.

Import compile_findings(pages) — pages = [{"url", "html", "status_code"}] — from the
orchestrator (HTML already fetched once via the shared safe session), or run standalone.
"""

import argparse
import json
import os
import re
import sys
from urllib.parse import urljoin, urlparse

try:
    from bs4 import BeautifulSoup
except ImportError:
    print(json.dumps({"error": "Missing dependency: beautifulsoup4. pip install -r requirements.txt"}),
          file=sys.stderr)
    sys.exit(1)

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.normpath(os.path.join(_SCRIPTS_DIR, "..", "..", "..", "lib"))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from report import make_finding  # noqa: E402

SKILL = "engagement-audit"

_GENERIC_CTA = {"learn more", "read more", "click here", "more", "details", "see more",
                "view more", "find out more", "go", "here", "submit"}


def _is_home(url):
    return urlparse(url).path.strip() in ("", "/", "/index.html", "/index.htm")


def _has_breadcrumb(soup):
    if soup.find(attrs={"class": re.compile(r"breadcrumb", re.I)}):
        return True
    if soup.find("nav", attrs={"aria-label": re.compile(r"breadcrumb", re.I)}):
        return True
    for el in soup.find_all(attrs={"itemtype": True}):
        if "breadcrumblist" in str(el.get("itemtype", "")).lower():
            return True
    return False


def compile_findings(pages) -> list:
    findings = []

    def add(check_id, severity, title, summary, url, observed, locator="", source="html"):
        findings.append(make_finding(
            skill=SKILL, check_id=check_id, severity=severity, title=title,
            action_summary=summary, evidence_url=url, evidence_source=source,
            evidence_observed=observed, evidence_locator=locator))

    missing_viewport, missing_h1, multi_h1 = [], [], []
    missing_nav, missing_breadcrumb, generic_cta = [], [], []

    for page in pages:
        if page.get("status_code") != 200 or not page.get("html"):
            continue
        url = page["url"]
        soup = BeautifulSoup(page["html"], "html.parser")

        if not soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)}):
            missing_viewport.append(url)

        h1s = soup.find_all("h1")
        if len(h1s) == 0:
            missing_h1.append(url)
        elif len(h1s) > 1:
            multi_h1.append(url)

        if not (soup.find("nav") or soup.find(attrs={"role": "navigation"})):
            missing_nav.append(url)

        if not _is_home(url) and not _has_breadcrumb(soup):
            missing_breadcrumb.append(url)

        cta_texts = []
        for el in soup.find_all(["a", "button"]):
            t = el.get_text(" ", strip=True).lower()
            if t in _GENERIC_CTA:
                cta_texts.append(t)
        if len(cta_texts) >= 4:
            generic_cta.append((url, len(cta_texts)))

    if missing_viewport:
        add("EG-mobile-viewport", "high",
            f"No mobile viewport meta on {len(missing_viewport)} page(s)",
            "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> so pages "
            "render correctly on phones — where most AI-referred visitors land.",
            missing_viewport[0], f"{len(missing_viewport)} page(s) missing viewport meta",
            locator="meta[name=viewport]")

    if missing_h1:
        add("EG-orientation-h1", "medium",
            f"No <h1> heading on {len(missing_h1)} page(s)",
            "Give each page a single clear <h1> stating what the page is, so arriving visitors (and "
            "AI summaries) immediately understand the page's purpose.",
            missing_h1[0], f"pages without <h1>: {', '.join(missing_h1[:5])}", locator="h1")

    if multi_h1:
        add("EG-orientation-multi-h1", "low",
            f"Multiple <h1> headings on {len(multi_h1)} page(s)",
            "Use exactly one <h1> per page for a clear primary topic; demote the rest to <h2>.",
            multi_h1[0], f"pages with >1 <h1>: {', '.join(multi_h1[:5])}", locator="h1")

    if missing_nav:
        add("EG-nav-landmark", "low",
            f"No <nav> navigation landmark on {len(missing_nav)} page(s)",
            "Wrap primary navigation in a <nav> (or role=navigation) landmark so visitors and "
            "assistive tech can find their way around the site.",
            missing_nav[0], f"{len(missing_nav)} page(s) without a nav landmark", locator="nav")

    if missing_breadcrumb:
        add("EG-breadcrumb", "low",
            f"No visible breadcrumb on {len(missing_breadcrumb)} interior page(s)",
            "Add visible breadcrumb navigation on interior pages so visitors arriving mid-site from "
            "an AI citation can orient to the site hierarchy.",
            missing_breadcrumb[0], f"interior pages without breadcrumb: {', '.join(missing_breadcrumb[:5])}",
            locator="nav.breadcrumb")

    if generic_cta:
        worst = max(generic_cta, key=lambda x: x[1])
        add("EG-ambiguous-cta", "low",
            f"Ambiguous/generic calls-to-action on {len(generic_cta)} page(s)",
            "Replace generic CTA labels (\"Learn more\", \"Click here\") with specific action text "
            "(\"See pricing\", \"Start free trial\") so the next step is unambiguous.",
            worst[0], f"{worst[1]} generic CTAs on {worst[0]}", locator="a/button text")

    return findings


def _fetch_pages(url, paths):
    import requests  # patched to SafeSession when run under the orchestrator
    base = url if url.startswith(("http://", "https://")) else "https://" + url
    urls = [base] + [urljoin(base, p.strip()) for p in (paths or []) if p.strip()]
    out = []
    seen = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        try:
            r = requests.get(u, timeout=15, headers={"User-Agent": "BrandAIReadinessAudit/1.0"})
            out.append({"url": u, "status_code": r.status_code, "html": r.text})
        except Exception as e:
            out.append({"url": u, "status_code": 0, "html": "", "error": str(e)[:200]})
    return out


def main():
    ap = argparse.ArgumentParser(description="Compile engagement findings from a live URL or a pages JSON.")
    ap.add_argument("--url", help="Site root URL")
    ap.add_argument("--pages", help="Comma-separated interior page paths")
    ap.add_argument("--pages-file", help="JSON array of {url, html, status_code}")
    ap.add_argument("--output")
    args = ap.parse_args()

    if args.pages_file:
        with open(args.pages_file, "r", encoding="utf-8") as f:
            pages = json.load(f)
    elif args.url:
        paths = args.pages.split(",") if args.pages else None
        pages = _fetch_pages(args.url, paths)
    else:
        ap.error("supply --url or --pages-file")

    findings = compile_findings(pages)
    report = {"skill": SKILL, "total_findings": len(findings), "findings": findings}
    out = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()
