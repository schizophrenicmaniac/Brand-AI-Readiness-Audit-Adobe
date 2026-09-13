#!/usr/bin/env python3
"""Compatibility adapter for cached HTML engagement analysis.

The preferred orchestrator integration is ``engagement_extractor.build_raw`` plus
``EngagementValidator.run_all``. ``compile_findings`` remains public for callers
that already hold ``[{url, html, status_code, content_type?}]`` pages.
"""

import argparse
import json
import os
import sys
from urllib.parse import urljoin

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from engagement_extractor import analyze_html_page  # noqa: E402
from engagement_validator import EngagementValidator  # noqa: E402

SKILL = "engagement-audit"


def compile_findings(pages) -> list[dict]:
    """Compile complete engagement findings from fetched HTML or extracted pages."""
    extracted = []
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        if any(page.get(key) for key in ("navigation", "orientation", "performance", "mobile", "ctas", "search", "context_retention")):
            extracted.append(page)
            continue
        extracted.append(analyze_html_page(
            page.get("url", ""), page.get("html", ""), page.get("status_code", 0),
            page.get("content_type", "text/html"), page.get("final_url"),
        ))
    site = next((p.get("url") for p in extracted if p.get("url")), "")
    return EngagementValidator({"site": site, "pages": extracted}).run_all()


def _fetch_pages(url, paths, max_pages=10):
    """Fetch raw HTML for the compatibility CLI/current orchestrator adapter."""
    import requests  # patched by the orchestrator's safe HTTP layer
    base = url if url.startswith(("http://", "https://")) else "https://" + url
    urls, seen = [], set()
    if isinstance(paths, str):
        paths = paths.split(",") if paths else []
    for candidate in [base] + list(paths or []):
        candidate = str(candidate).strip()
        if not candidate:
            continue
        full = urljoin(base, candidate).split("#", 1)[0]
        if full not in seen:
            seen.add(full)
            urls.append(full)
    output = []
    for target in urls[:max(0, int(max_pages))]:
        try:
            response = requests.get(target, timeout=15, headers={"User-Agent": "BrandAIReadinessAudit/1.0"}, allow_redirects=True)
            output.append({
                "url": target, "final_url": response.url, "status_code": response.status_code,
                "content_type": response.headers.get("Content-Type", ""), "html": response.text,
            })
        except Exception as exc:
            output.append({"url": target, "status_code": 0, "content_type": "", "html": "", "error": str(exc)[:300]})
    return output


def main():
    parser = argparse.ArgumentParser(description="Compile engagement findings from a URL or pages JSON.")
    parser.add_argument("--url")
    parser.add_argument("--pages", default="")
    parser.add_argument("--pages-file")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.pages_file:
        with open(args.pages_file, "r", encoding="utf-8") as f:
            pages = json.load(f)
    elif args.url:
        pages = _fetch_pages(args.url, args.pages.split(",") if args.pages else [], args.max_pages)
    else:
        parser.error("supply --url or --pages-file")
    findings = compile_findings(pages)
    output = json.dumps({"skill": SKILL, "total_findings": len(findings), "findings": findings}, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main()
