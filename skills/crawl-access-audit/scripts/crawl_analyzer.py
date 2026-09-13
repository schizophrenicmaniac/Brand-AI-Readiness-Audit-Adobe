#!/usr/bin/env python3
"""crawl_analyzer.py — turn crawl-access raw JSON into contract findings.

Consumes the JSON produced by robots_analyzer.analyze(), sitemap_validator.analyze(),
and page_fetcher.analyze() and emits findings in the shared report contract
(id/title/severity/evidence/suggested_action). This is the deterministic compiler for
the CA-01…CA-13 checks that previously lived only in SKILL.md prose.

Usable two ways:
  * import compile_findings(robots, sitemap, pages) from the orchestrator, or
  * run standalone: python crawl_analyzer.py --url https://example.com
"""

import argparse
import json
import os
import sys
from urllib.parse import urlparse

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.normpath(os.path.join(_SCRIPTS_DIR, "..", "..", "..", "lib"))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from report import make_finding  # noqa: E402

SKILL = "crawl-access-audit"


def _is_home(url: str) -> bool:
    path = urlparse(url).path.strip()
    return path in ("", "/", "/index.html", "/index.htm")


def _status_severity(status, is_home: bool) -> str:
    if status is None:
        return "high"
    if status >= 500:
        return "critical" if is_home else "high"
    if status in (403, 404, 410):
        return "high" if is_home else "medium"
    if status >= 400:
        return "medium"
    return "info"


def compile_findings(robots=None, sitemap=None, pages=None) -> list:
    findings = []

    def add(check_id, severity, title, summary, url, source, observed, locator="", expected=None):
        findings.append(make_finding(
            skill=SKILL, check_id=check_id, severity=severity, title=title,
            action_summary=summary, evidence_url=url, evidence_source=source,
            evidence_observed=observed, evidence_locator=locator, evidence_expected=expected))

    robots = robots or {}
    sitemap = sitemap or {}
    pages = pages or {}
    robots_url = robots.get("url", "")

    # ---- Group A: robots.txt permission layer ------------------------------
    if robots.get("wildcard_disallow_all"):
        add("CA-01-wildcard", "critical",
            "robots.txt blocks all crawlers on every path",
            "Remove the blanket `Disallow: /` for `User-agent: *`, or scope it to non-public "
            "paths only, so AI and search crawlers can reach public content.",
            robots_url, "robots_txt", "User-agent: *\\nDisallow: /", locator="User-agent: *")

    search_blocked = robots.get("ai_search_bots_blocked", [])
    if search_blocked:
        names = ", ".join(sorted({b.get("user_agent", "?") for b in search_blocked}))
        add("CA-01-searchbots", "critical",
            "AI search/retrieval crawlers blocked by robots.txt",
            f"Allow the search/retrieval crawlers ({names}) to fetch public pages; blocking them "
            "removes the brand from AI answer citations. Keep training-bot policy separate if desired.",
            robots_url, "robots_txt",
            f"Disallow rules match: {names}", locator="Disallow")

    training_blocked = robots.get("ai_training_bots_blocked", [])
    if training_blocked:
        names = ", ".join(sorted({b.get("user_agent", "?") for b in training_blocked}))
        add("CA-01-trainingbots", "info",
            "AI training crawlers blocked by robots.txt (policy choice)",
            "No action required unless you want your content used for model training. Blocking "
            "training bots does not affect search/retrieval citation.",
            robots_url, "robots_txt", f"Disallow rules match: {names}", locator="Disallow")

    cpb = robots.get("critical_path_blocks", [])
    if cpb:
        paths = ", ".join(sorted({b.get("path", "?") for b in cpb}))
        add("CA-02-critpath", "high",
            "High-value paths disallowed in robots.txt",
            f"Review Disallow rules covering high-value paths ({paths}); if these pages should be "
            "indexed, remove or narrow the rules.",
            robots_url, "robots_txt", f"Blocked high-value paths: {paths}", locator="Disallow")

    for cd in robots.get("crawl_delay_issues", []):
        sev = cd.get("severity", "low")
        if sev == "info":
            continue
        add("CA-04-crawldelay", sev,
            "Excessive Crawl-delay throttles crawlers",
            "Lower or remove the Crawl-delay directive; high values slow or discourage AI/search "
            "crawlers from fetching the full site.",
            robots_url, "robots_txt",
            f"{cd.get('user_agent')} Crawl-delay: {cd.get('crawl_delay')}s", locator="Crawl-delay")

    # ---- Group C: TLS / transport ------------------------------------------
    tls = pages.get("tls", {})
    domain = pages.get("domain", "")
    site_root = f"https://{domain}/" if domain else robots_url
    if tls:
        if tls.get("verification_failed"):
            add("CA-13-tls-invalid", "critical",
                "TLS certificate is invalid (verification failed)",
                "Fix the TLS certificate (validity, chain, hostname match). An invalid certificate "
                "blocks HTTPS crawlers and destroys trust signals.",
                site_root, "tls", str(tls.get("error"))[:300], locator="certificate")
        elif tls.get("error") and not tls.get("valid"):
            # A raw-socket connection/timeout failure (common behind proxies/CI) is NOT
            # evidence of a bad certificate — pages may still fetch fine over HTTPS.
            add("CA-13-tls-unverified", "low",
                "Could not verify TLS certificate (connection issue)",
                "Re-run the TLS check from a network that can open a direct socket to port 443; "
                "this is a probe limitation, not necessarily a certificate defect.",
                site_root, "tls", str(tls.get("error"))[:300], locator="certificate")
        elif tls.get("valid"):
            days = tls.get("days_until_expiry")
            if isinstance(days, int) and days <= 30:
                add("CA-13-tls-expiring", "high",
                    "TLS certificate expires within 30 days",
                    "Renew the TLS certificate before expiry to avoid an outage that would block all "
                    "HTTPS crawling.",
                    site_root, "tls", f"expires {tls.get('expires')} ({days} days)", locator="certificate")

    h2h = pages.get("http_to_https_redirect", {})
    if h2h and h2h.get("error") is None and h2h.get("redirects") is False:
        add("CA-13-no-https-redirect", "medium",
            "HTTP does not redirect to HTTPS",
            "Add a 301 redirect from http:// to https:// so crawlers consolidate on the secure, "
            "canonical origin.",
            site_root, "http_status", f"http:// final URL: {h2h.get('final_url')}", locator="http->https")

    # ---- Per-page fetch layer ----------------------------------------------
    hsts_missing = []
    for page in pages.get("pages", []):
        url = page.get("url", "")
        if page.get("skipped"):
            continue
        home = _is_home(url)
        status = page.get("status_code")
        err = page.get("error")

        if err and "loop" in str(err).lower():
            add("CA-09-loop", "critical", "Redirect loop detected",
                "Break the redirect loop; crawlers abandon looping URLs and the content becomes "
                "unreachable.", url, "http_status", str(err)[:200], locator="redirect_chain")
            continue
        if err and "timeout" in str(err).lower():
            add("CA-11-timeout", "high", "Page request timed out",
                "Investigate server latency/timeouts; pages that time out are dropped by crawlers.",
                url, "http_status", str(err)[:200], locator="ttfb")
            continue
        if status is None and err:
            add("CA-08-fetch-error", _status_severity(None, home), "Page could not be fetched",
                "Ensure the page returns a healthy 200 response to crawlers.",
                url, "http_status", str(err)[:200], locator="status")
            continue

        # CA-08 status
        if status is not None and status != 200:
            add("CA-08-status", _status_severity(status, home),
                f"Page returns HTTP {status}",
                "Return HTTP 200 for pages that should be indexed, or remove them from navigation "
                "and sitemaps if intentionally gone.",
                url, "http_status", f"HTTP {status}", locator="status", expected="200")
        # CA-08 soft-404
        if page.get("is_soft_404"):
            add("CA-08-soft404", "medium", "Soft-404: 200 status on a not-found page",
                "Return a real 404/410 for missing pages so crawlers don't index empty/duplicate "
                "content.", url, "http_status", str(page.get("soft_404_rule")), locator="soft_404")

        # CA-09 redirect chain
        rlen = page.get("redirect_chain_length", 0)
        if rlen > 5:
            add("CA-09-chain", "high", "Long redirect chain (>5 hops)",
                "Collapse redirect chains to a single hop; long chains waste crawl budget and can be "
                "abandoned.", url, "http_status", f"{rlen} redirect hops", locator="redirect_chain")
        elif rlen > 2:
            add("CA-09-chain", "medium", "Redirect chain longer than 2 hops",
                "Reduce the redirect chain to one hop to preserve crawl efficiency and signals.",
                url, "http_status", f"{rlen} redirect hops", locator="redirect_chain")

        # CA-03 robots directives
        directives = " ".join(str(x).lower() for x in (page.get("meta_robots"), page.get("x_robots_tag")) if x)
        loc = "meta[name=robots] / X-Robots-Tag"
        if "noindex" in directives:
            add("CA-03-noindex", "high" if not home else "critical",
                "Page is set to noindex",
                "Remove `noindex` from pages that should appear in AI/search results.",
                url, "http_header", directives[:200], locator=loc)
        if "nosnippet" in directives:
            add("CA-03-nosnippet", "high", "Page is set to nosnippet",
                "Remove `nosnippet` so AI assistants may quote the page in answers.",
                url, "http_header", directives[:200], locator=loc)
        if "noarchive" in directives:
            add("CA-03-noarchive", "medium", "Page is set to noarchive",
                "Remove `noarchive` if you want cached copies available to retrieval systems.",
                url, "http_header", directives[:200], locator=loc)

        # CA-10 canonical
        can = page.get("canonical", {})
        if can.get("href") and can.get("matches_request_url") is False:
            can_host = urlparse(can["href"]).netloc.lower()
            page_host = urlparse(url).netloc.lower()
            if can.get("target_status") == 404:
                add("CA-10-canonical-404", "high", "Canonical points to a 404",
                    "Point rel=canonical at a live, correct URL; a broken canonical splits or drops "
                    "indexing signals.", url, "html",
                    f"canonical -> {can['href']} (404)", locator="link[rel=canonical]")
            elif can_host and can_host != page_host:
                add("CA-10-canonical-offsite", "high", "Canonical points to a different domain",
                    "Verify the cross-domain canonical is intentional; otherwise it hands indexing to "
                    "another site.", url, "html",
                    f"canonical -> {can['href']}", locator="link[rel=canonical]")
            else:
                add("CA-10-canonical-mismatch", "medium", "Canonical differs from the page URL",
                    "Confirm the canonical target is the intended indexed URL.",
                    url, "html", f"canonical -> {can['href']}", locator="link[rel=canonical]")

        # CA-11 latency
        ttfb = page.get("ttfb_ms")
        if isinstance(ttfb, (int, float)):
            if ttfb > 10000:
                add("CA-11-ttfb", "critical", "Time to first byte over 10s",
                    "Reduce server response time; extreme latency causes crawlers to time out.",
                    url, "http_status", f"TTFB {int(ttfb)} ms", locator="ttfb")
            elif ttfb > 5000:
                add("CA-11-ttfb", "high", "Time to first byte over 5s",
                    "Improve server/CDN response time to keep pages within crawler patience windows.",
                    url, "http_status", f"TTFB {int(ttfb)} ms", locator="ttfb")
            elif ttfb > 3000:
                add("CA-11-ttfb", "medium", "Time to first byte over 3s",
                    "Optimize response time (caching/CDN) to improve crawl throughput.",
                    url, "http_status", f"TTFB {int(ttfb)} ms", locator="ttfb")

        # CA-12 challenge page
        ch = page.get("challenge_page", {})
        if ch.get("detected"):
            add("CA-12-challenge", "high" if home else "medium",
                "Bot-challenge/CAPTCHA page served to the audit client",
                "Allowlist legitimate AI/search crawlers past the bot-challenge (provider WAF rules); "
                "a challenge page hides real content from retrieval systems.",
                url, "http_status",
                f"{ch.get('provider')}: {ch.get('signal')}", locator="challenge")

        if page.get("hsts_header") is None and urlparse(url).scheme == "https":
            hsts_missing.append(url)

    if hsts_missing:
        add("CA-13-hsts", "low", "Strict-Transport-Security (HSTS) header missing",
            "Add an HSTS header to enforce HTTPS and protect against downgrade attacks.",
            hsts_missing[0], "http_header", f"{len(hsts_missing)} page(s) without HSTS",
            locator="Strict-Transport-Security")

    # ---- Group B: discovery layer (sitemaps + crawl graph) -----------------
    smaps = sitemap.get("sitemaps_found", [])
    if not smaps:
        add("CA-05-nositemap", "medium", "No XML sitemap found",
            "Publish an XML sitemap and declare it in robots.txt so crawlers can discover all pages.",
            site_root, "sitemap", "; ".join(sitemap.get("errors", [])) or "no sitemap discovered",
            locator="/sitemap.xml")
    else:
        bad_urls = []
        for sm in smaps:
            if sm.get("status") == 200 and sm.get("xml_valid") is False:
                add("CA-05-malformed", "medium", "Sitemap XML is malformed",
                    "Fix the sitemap XML so crawlers can parse it (well-formed <urlset>/<sitemapindex>).",
                    sm.get("url", site_root), "sitemap",
                    "; ".join(sm.get("errors", []))[:300], locator="xml")
            elif sm.get("status") not in (200, None):
                add("CA-05-status", "low", f"Sitemap returns HTTP {sm.get('status')}",
                    "Ensure the declared sitemap URL returns HTTP 200.",
                    sm.get("url", site_root), "sitemap", f"HTTP {sm.get('status')}", locator="status")
            for chk in sm.get("sample_checks", []):
                st = chk.get("status")
                if isinstance(st, int) and st >= 400:
                    bad_urls.append(f"{chk.get('url')} ({st})")
        if bad_urls:
            add("CA-05-deadurls", "medium",
                "Sitemap lists URLs that return errors",
                "Remove or fix sitemap URLs that 4xx/5xx; dead sitemap entries waste crawl budget.",
                site_root, "sitemap", "; ".join(bad_urls[:5]), locator="sitemap url sample")
        if sitemap.get("declared_in_robots_txt"):
            s_urls = [s.get("url") for s in smaps if s.get("url")]
            add("CA-05-declared", "info", "XML sitemap declared in robots.txt",
                "Maintain sitemap declarations in robots.txt as sitemap structure changes.",
                robots_url or site_root, "robots_txt",
                f"Sitemap declared in robots.txt: {', '.join(s_urls[:2]) if s_urls else 'verified'}", locator="Sitemap")
        else:
            add("CA-05-notdeclared", "low", "Sitemap not declared in robots.txt",
                "Add a `Sitemap:` line to robots.txt to speed up sitemap discovery.",
                robots_url or site_root, "robots_txt", "no Sitemap: directive", locator="Sitemap")

    if sitemap.get("duplicates", {}).get("duplicate_count"):
        dups = sitemap["duplicates"]
        add("CA-05-duplicates", "low", "Duplicate URLs in sitemap",
            "De-duplicate sitemap entries so crawlers don't waste budget on repeats.",
            site_root, "sitemap",
            f"{dups['duplicate_count']} duplicates e.g. {', '.join(dups.get('duplicate_examples', [])[:3])}",
            locator="urlset")

    prolif = sitemap.get("parameter_proliferation", {})
    if prolif.get("detected"):
        add("CA-05-params", "medium", "URL parameter proliferation (crawl-trap risk)",
            "Consolidate parameterized URLs (canonical tags / robots rules) to avoid crawl traps.",
            site_root, "sitemap",
            f"paths: {', '.join(prolif.get('affected_paths', [])[:3])}; patterns: {', '.join(prolif.get('pattern_examples', [])[:3])}",
            locator="query params")

    graph = pages.get("crawl_graph", {})
    if graph.get("deep_pages"):
        deep = graph["deep_pages"]
        add("CA-06-depth", "medium",
            f"{len(deep)} page(s) buried deeper than 3 clicks",
            "Flatten navigation so high-value pages are within ~3 clicks of the homepage.",
            site_root, "crawl_graph",
            "; ".join(f"{d.get('url')} (depth {d.get('depth')})" for d in deep[:4]), locator="depth")
    if graph.get("broken_internal_links"):
        broken = graph["broken_internal_links"]
        add("CA-06-broken", "medium",
            f"{len(broken)} broken internal link(s)",
            "Fix or remove broken internal links; they fragment the crawl graph and lose PageRank.",
            site_root, "crawl_graph",
            "; ".join(f"{b.get('url')} ({b.get('status')})" for b in broken[:4]), locator="internal links")

    orphans = pages.get("orphan_pages", {})
    if orphans.get("checked") and orphans.get("count"):
        conf = orphans.get("confidence", "")
        sev = "low" if conf == "bounded_crawl" else "medium"
        add("CA-06-orphans", sev,
            f"{orphans['count']} sitemap URL(s) not reachable via internal links",
            "Add internal links to orphaned pages so crawlers can discover them without relying on "
            "the sitemap alone.",
            site_root, "crawl_graph",
            f"{'; '.join(orphans.get('urls', [])[:4])} (confidence: {conf})", locator="orphan")

    return findings


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="Compile crawl-access findings from raw JSON or a live URL.")
    ap.add_argument("--url", help="Run robots/sitemap/page analyzers live for this site root")
    ap.add_argument("--robots-file")
    ap.add_argument("--sitemap-file")
    ap.add_argument("--pages-file")
    ap.add_argument("--output")
    args = ap.parse_args()

    robots = sitemap = pages = None
    if args.url:
        import robots_analyzer
        import sitemap_validator
        import page_fetcher
        robots = robots_analyzer.analyze(args.url, robots_analyzer.DEFAULT_CRITICAL_PATHS)
        sitemap = sitemap_validator.analyze(args.url, robots.get("sitemap_urls") or None)
        pages = page_fetcher.analyze(args.url, sitemap_urls=sitemap.get("page_urls"))
    if args.robots_file:
        robots = _load(args.robots_file)
    if args.sitemap_file:
        sitemap = _load(args.sitemap_file)
    if args.pages_file:
        pages = _load(args.pages_file)

    findings = compile_findings(robots, sitemap, pages)
    report = {"skill": SKILL, "total_findings": len(findings), "findings": findings}
    out = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()
