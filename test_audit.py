#!/usr/bin/env python3
"""test_audit.py — offline tests for the Brand AI Readiness Audit.

No network: every check runs against crafted HTML/JSON fixtures, so it is deterministic and
safe in restricted environments. Covers the finding/report contract, the SSRF guard, and each
skill's finding compiler (including the false-positive fixes and status-gating).

Run:  python test_audit.py   ->  prints "ALL TESTS PASSED" on success.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_SKILLS = os.path.join(_ROOT, "skills")
for _p in [
    os.path.join(_ROOT, "lib"),
    os.path.join(_SKILLS, "audit-orchestrator", "scripts"),
    os.path.join(_SKILLS, "crawl-access-audit", "scripts"),
    os.path.join(_SKILLS, "render-extraction-audit", "scripts"),
    os.path.join(_SKILLS, "structured-data-audit", "scripts"),
    os.path.join(_SKILLS, "freshness-corroboration-audit", "scripts"),
    os.path.join(_SKILLS, "engagement-audit", "scripts"),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import report
import safe_http
import crawl_analyzer
import page_fetcher
import robots_analyzer
import render_analyzer
import engagement_analyzer
from schema_validator import SchemaValidator
from freshness_validator import FreshnessValidator

REQUIRED_FINDING_KEYS = {"id", "title", "severity", "evidence", "suggested_action"}


def _assert_contract(findings, label):
    for f in findings:
        assert REQUIRED_FINDING_KEYS <= set(f), f"{label}: missing keys in {f}"
        ev = f["evidence"]
        assert {"url", "source", "observed"} <= set(ev), f"{label}: bad evidence {ev}"
        act = f["suggested_action"]
        assert act.get("summary") and act.get("priority") in ("P0", "P1", "P2", "P3"), \
            f"{label}: bad suggested_action {act}"
        assert f["severity"] in report.SEVERITIES, f"{label}: bad severity {f['severity']}"


def test_report_contract():
    f = report.make_finding(
        "crawl-access-audit", "CA-01", "critical", "t", "fix it",
        evidence_url="https://x/robots.txt", evidence_source="robots_txt",
        evidence_observed="Disallow: /", evidence_locator="UA:OAI-SearchBot")
    assert f["id"].startswith("ca-") and f["suggested_action"]["priority"] == "P0"
    # stable id: same inputs -> same id
    f2 = report.make_finding(
        "crawl-access-audit", "CA-01", "critical", "t", "fix it",
        evidence_url="https://x/robots.txt", evidence_source="robots_txt",
        evidence_observed="Disallow: /", evidence_locator="UA:OAI-SearchBot")
    assert f["id"] == f2["id"]

    rep = report.assemble_report("x.com", [f], pages_audited=1)
    report.validate_report(rep)
    assert rep["summary"]["critical"] == 1 and rep["summary"]["total_findings"] == 1

    # invalid inputs must raise
    for bad in [
        lambda: report.make_finding("x", "c", "SEV?", "t", "s", evidence_url="u",
                                    evidence_source="html", evidence_observed="o"),
        lambda: report.make_finding("x", "c", "high", "t", "s", evidence_url="u",
                                    evidence_source="not_a_source", evidence_observed="o"),
    ]:
        try:
            bad(); raise AssertionError("expected ValueError")
        except ValueError:
            pass

    # a report missing summary must fail validation
    broken = dict(rep); broken.pop("summary")
    try:
        report.validate_report(broken); raise AssertionError("expected schema failure")
    except ValueError:
        pass


def test_ssrf_guard():
    bad = ["http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/",
           "http://10.1.2.3/", "http://192.168.0.5/", "http://user:pass@8.8.8.8/",
           "ftp://8.8.8.8/", "http://8.8.8.8:22/", "http://0.0.0.0/"]
    for u in bad:
        try:
            safe_http.validate_url(u)
            raise AssertionError(f"SSRF guard allowed {u}")
        except safe_http.UnsafeRequestError:
            pass
    for u in ["http://8.8.8.8/", "https://93.184.216.34/", "http://8.8.8.8:80/"]:
        assert safe_http.validate_url(u) == u


def test_crawl_analyzer():
    robots = {"url": "https://example.com/robots.txt", "wildcard_disallow_all": True,
              "ai_search_bots_blocked": [{"user_agent": "OAI-SearchBot"}],
              "ai_training_bots_blocked": [], "critical_path_blocks": [],
              "crawl_delay_issues": []}
    sitemap = {"sitemaps_found": [], "errors": ["No sitemap found via robots.txt"],
               "duplicates": {"duplicate_count": 0}, "parameter_proliferation": {"detected": False},
               "declared_in_robots_txt": False, "page_urls": []}
    pages = {"domain": "example.com", "tls": {"valid": True, "days_until_expiry": 200},
             "http_to_https_redirect": {"redirects": True, "error": None},
             "pages": [{"url": "https://example.com/", "status_code": 200, "ttfb_ms": 120,
                        "redirect_chain_length": 0, "meta_robots": "noindex", "x_robots_tag": None,
                        "canonical": {"href": None}, "challenge_page": {"detected": False},
                        "hsts_header": "max-age=1", "is_soft_404": False}],
             "crawl_graph": {}, "orphan_pages": {"checked": False}}
    findings = crawl_analyzer.compile_findings(robots, sitemap, pages)
    _assert_contract(findings, "crawl")
    titles = " | ".join(f["title"] for f in findings)
    assert any(f["severity"] == "critical" for f in findings), titles
    assert "all crawlers" in titles.lower() or "search/retrieval" in titles.lower(), titles
    assert any("noindex" in f["title"].lower() for f in findings), titles
    assert any("sitemap" in f["title"].lower() for f in findings), titles


def test_render_analyzer_static():
    raw = {"pages": [{
        "url": "https://example.com/", "status_code": 200, "error": None,
        "spa_detection": {"is_shell": True, "noscript_quality": "missing",
                          "frameworks": [{"framework": "React"}]},
        "raw_text_length": 40,
        "images": [{"is_decorative": False, "alt_quality": "missing", "src": "/a.png"},
                   {"is_decorative": False, "alt_quality": "missing", "src": "/b.png"},
                   {"is_decorative": False, "alt_quality": "missing", "src": "/c.png"}],
        "canvases": [], "videos": [], "audios": [], "iframes": [], "pdf_links": [],
        "infinite_scroll_markers": [], "icon_font_elements": [], "css_content_declarations": [],
        "key_facts_in_raw_text": [], "text_ratio": {"full_text_length": 40},
    }]}
    findings = render_analyzer.compile_findings(raw, rendered=None)  # Playwright absent
    _assert_contract(findings, "render")
    titles = " | ".join(f["title"] for f in findings)
    assert any("shell" in f["title"].lower() for f in findings), titles
    assert any("image" in f["title"].lower() and f["severity"] == "high" for f in findings), titles


def test_structured_validator_gating_and_contract():
    raw = {"site": "https://example.com", "llms_txt": {"/llms.txt": {"present": False}},
           "pages": [
               {"url": "https://example.com/", "status_code": 200, "content_type": "text/html",
                "json_ld": {"blocks": []}, "open_graph": {}, "twitter_card": {},
                "meta": {"title": "Home", "description": "", "favicons": []},
                "visible_cues": {"h1": [], "h2_sample": [], "detected_prices": [],
                                 "detected_dates": [], "text_sample": ""}},
               # failed fetch: must NOT produce findings
               {"url": "https://example.com/broken", "status_code": 0, "error": "conn",
                "json_ld": {"blocks": []}, "meta": {}, "visible_cues": {}}]}
    findings = SchemaValidator(raw).run_all()
    _assert_contract(findings, "structured")
    assert any(f["severity"] == "critical" for f in findings)  # zero JSON-LD across site
    # status-gating: no finding may reference the failed page
    assert not any(f["evidence"]["url"].endswith("/broken") for f in findings)


def test_freshness_fp_fixes():
    raw = {"site": "https://example.com",
           "entity": {"detected_brand_name": "IBM", "aggregated_claims": {"founding_year": "1911"},
                      "sameAs_links": []},
           "grounding": {"wikipedia": {"has_entry": False}, "wikidata": {"has_entry": False}},
           "corroboration_templates": [{"claim_type": "founding_year"}],
           "pages": [{"url": "https://example.com/", "status_code": 200, "content_type": "text/html",
                      "date_signals": {"has_any_date_signal": False, "http_headers": {}},
                      "staleness_markers": {}, "entity_data": {"nap": {}}}]}
    findings = FreshnessValidator(raw).run_all()
    _assert_contract(findings, "freshness")
    titles = " | ".join(f["title"].lower() for f in findings)
    # short-name collision heuristic removed (IBM is 3 chars) -> no collision finding
    assert "collision" not in titles, titles
    # missing sameAs reported exactly once, at medium (not high, not duplicated by FC-03)
    sameas = [f for f in findings if "sameas" in f["title"].lower()]
    assert len(sameas) == 1 and sameas[0]["severity"] == "medium", [f["title"] for f in findings]

    # all-failed-fetch input -> no entity findings (content gating)
    raw2 = dict(raw)
    raw2["pages"] = [{"url": "https://example.com/", "status_code": 0, "error": "conn"}]
    assert FreshnessValidator(raw2).run_all() == []


def test_engagement_analyzer():
    html = ("<html><head></head><body>"
            "<a>Learn more</a><a>Read more</a><a>click here</a><a>more</a>"
            "</body></html>")
    pages = [{"url": "https://example.com/", "status_code": 200, "html": html}]
    findings = engagement_analyzer.compile_findings(pages)
    _assert_contract(findings, "engagement")
    titles = " | ".join(f["title"].lower() for f in findings)
    assert "viewport" in titles and "h1" in titles and "generic labels" in titles, titles


def test_combined_report_validates():
    all_findings = []
    all_findings += crawl_analyzer.compile_findings(
        {"url": "https://example.com/robots.txt", "wildcard_disallow_all": True,
         "ai_search_bots_blocked": [], "ai_training_bots_blocked": [],
         "critical_path_blocks": [], "crawl_delay_issues": []}, {}, {})
    all_findings += engagement_analyzer.compile_findings(
        [{"url": "https://example.com/about", "status_code": 200,
          "html": "<html><head></head><body><p>hi</p></body></html>"}])
    rep = report.assemble_report("example.com", all_findings, coverage={"checks_run": ["x"]},
                                 pages_audited=2, proactive_improvements=[{"title": "t"}])
    report.validate_report(rep)
    assert rep["summary"]["total_findings"] == len(report.dedupe_findings(all_findings))


def test_fp_regressions():
    """Locks in the false-positive / coverage fixes so they don't regress."""
    import time

    # (A) An HTTP Last-Modified/Date header (≈ now on CDNs) must NOT yield a positive
    #     "fresh" claim — only a real content date (meta/schema/visible) can.
    raw = {"site": "https://ex.com",
           "entity": {"detected_brand_name": "Ex", "aggregated_claims": {}, "sameAs_links": []},
           "grounding": {"wikipedia": {"has_entry": False}, "wikidata": {"has_entry": False}},
           "corroboration_templates": [],
           "pages": [{"url": "https://ex.com/", "status_code": 200, "content_type": "text/html",
                      "date_signals": {"has_any_date_signal": True,
                                       "freshest_date": {"source": "header:last-modified", "timestamp": time.time()},
                                       "http_headers": {"last-modified": "now"}},
                      "staleness_markers": {}, "entity_data": {"nap": {}}}]}
    ftitles = " | ".join(f["title"].lower() for f in FreshnessValidator(raw).run_all())
    assert "freshness signals verified" not in ftitles, ftitles

    # (B) soft-404 is conservative: SPA shell / short page and "oops"-in-JS are NOT soft-404;
    #     an explicit not-found title/heading IS.
    assert page_fetcher.detect_soft_404(200, "<html><body><div id='root'></div></body></html>", None)["detected"] is False
    assert page_fetcher.detect_soft_404(200, "<script>x='oops'</script><body>Welcome</body>", "Home")["detected"] is False
    assert page_fetcher.detect_soft_404(200, "<body><h1>Page not found</h1></body>", "Page Not Found")["detected"] is True

    # (C) TLS: a connection/timeout blip must NOT be CRITICAL; a real verification failure must be.
    def _tls(v):
        return crawl_analyzer.compile_findings({}, {}, {"domain": "x.com", "tls": v, "pages": []})
    t1 = _tls({"error": "TLS connection timeout", "valid": False, "verification_failed": False})
    assert all(f["severity"] != "critical" for f in t1) and any(f["severity"] == "low" for f in t1)
    t2 = _tls({"error": "cert verify failed", "valid": False, "verification_failed": True})
    assert any(f["severity"] == "critical" for f in t2)

    # (D) collapse_repeats folds the same site-wide issue reported per page into one.
    def mk(url):
        return report.make_finding("structured-data-audit", "SD-02", "medium",
                                   "Recommended properties missing in WebSite schema", "add props",
                                   evidence_url=url, evidence_source="html", evidence_observed="x")
    collapsed = report.collapse_repeats([mk("https://a/"), mk("https://a/b"), mk("https://a/c")])
    assert len(collapsed) == 1 and "Affects 3 pages" in collapsed[0]["evidence"]["observed"]

    # (E) apex/www: interior www pages ARE selected for an apex-domain input (no homepage-only audit).
    import run_audit
    assert run_audit._reg_host("www.x.com") == run_audit._reg_host("x.com")
    chosen, _, _ = run_audit._choose_pages(
        "https://x.com/", ["https://www.x.com/pricing", "https://www.x.com/about"], [], 8)
    assert len(chosen) >= 3, chosen

    # (F) sitemap declared in robots: when sitemap is declared, no "Sitemap not declared" finding.
    c_findings = crawl_analyzer.compile_findings(
        {"url": "https://x.com/robots.txt"},
        {"sitemaps_found": [{"url": "https://x.com/sitemap.xml", "status": 200, "xml_valid": True}],
         "declared_in_robots_txt": True, "errors": []},
        {"pages": []}
    )
    c_titles = " | ".join(f["title"] for f in c_findings)
    assert "not declared in robots.txt" not in c_titles.lower()
    assert any("declared in robots.txt" in f["title"].lower() and f["severity"] == "info" for f in c_findings)

    # (G) llms.txt: when present and valid, emit an info finding and don't recommend publishing it in _proactive.
    sd_raw = {"site": "https://x.com",
              "llms_txt": {"/llms.txt": {"present": True, "url": "https://x.com/llms.txt", "length_chars": 150}},
              "pages": [{"url": "https://x.com/", "status_code": 200, "json_ld": {"blocks": []}, "meta": {}, "visible_cues": {}}]}
    sd_findings = SchemaValidator(sd_raw).run_all()
    assert any("valid /llms.txt" in f["title"].lower() and f["severity"] == "info" for f in sd_findings)
    assert not any("missing /llms.txt" in f["title"].lower() for f in sd_findings)
    proactive = run_audit._proactive(sd_findings)
    assert not any("publish an llms.txt" in p["title"].lower() for p in proactive)


def test_hardening_regressions():
    """Production regressions found during Round-3 stress testing."""
    import requests

    # Read-only transport is enforced, not just documented.
    req = requests.Request("POST", "https://example.com/").prepare()
    try:
        safe_http.SafeSession().send(req)
        raise AssertionError("POST should be blocked")
    except safe_http.UnsafeRequestError:
        pass

    # Semantic report checks reject duplicate IDs, count drift, and non-UTC timestamps.
    f = report.make_finding(
        "crawl-access-audit", "CA-X", "high", "x", "fix",
        evidence_url="https://x/", evidence_source="html", evidence_observed="x")
    duplicate = report.assemble_report("x", [f])
    duplicate["findings"].append(dict(f))
    duplicate["summary"]["total_findings"] = 2
    duplicate["summary"]["high"] = 2
    try:
        report.validate_report(duplicate)
        raise AssertionError("duplicate ids should fail")
    except ValueError:
        pass
    drift = report.assemble_report("x", [f])
    drift["summary"]["high"] = 0
    try:
        report.validate_report(drift)
        raise AssertionError("summary drift should fail")
    except ValueError:
        pass
    local_time = report.assemble_report("x", [f], audited_at="2026-09-13T10:00:00")
    try:
        report.validate_report(local_time)
        raise AssertionError("naive timestamp should fail")
    except ValueError:
        pass

    # A bot-wide root block is reported once, not expanded into synthetic path defects.
    class FakeRobotsResponse:
        status_code = 200
        text = "User-agent: ChatGPT-User\nDisallow: /\n"
    original_get = robots_analyzer.requests.get
    robots_analyzer.requests.get = lambda *args, **kwargs: FakeRobotsResponse()
    try:
        analyzed = robots_analyzer.analyze("https://example.com", ["/", "/about", "/news"])
    finally:
        robots_analyzer.requests.get = original_get
    assert analyzed["ai_search_bots_blocked"]
    assert analyzed["critical_path_blocks"] == [], analyzed["critical_path_blocks"]

    # Cloudflare 403 responses are identified as a challenge/WAF mechanism.
    challenge = page_fetcher.detect_challenge_page(
        "<title>Just a moment...</title>", {"Server": "cloudflare", "CF-RAY": "abc"}, 403)
    assert challenge["detected"] and challenge["provider"] == "cloudflare", challenge

    # Rich news publisher markup must not be downgraded because of a nested minimal org.
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "NewsMediaOrganization", "@id": "https://news.example/#org",
             "name": "Example News", "url": "https://news.example/",
             "logo": "https://news.example/logo.png",
             "sameAs": ["https://en.wikipedia.org/wiki/Example_News"]},
            {"@type": "NewsArticle", "headline": "Test headline",
             "datePublished": "2026-09-13T09:00:00Z",
             "author": {"@type": "Person", "name": "Reporter"},
             "publisher": {"@type": "Organization", "name": "Example News"}},
            {"@type": "SpeakableSpecification", "cssSelector": ["h1", ".summary"]},
        ],
    }
    news_raw = {
        "site": "https://news.example/",
        "llms_txt": {"/llms.txt": {"present": False}},
        "pages": [{"url": "https://news.example/", "status_code": 200,
                   "content_type": "text/html", "json_ld": {"blocks": [{"index": 0, "data": graph}]},
                   "open_graph": {}, "twitter_card": {},
                   "meta": {"title": "Example News Homepage", "description": "A sufficiently descriptive summary for a news publisher homepage.", "favicons": ["/favicon.ico"]},
                   "visible_cues": {"h1": ["Test headline"], "h2_sample": [],
                                    "detected_prices": [], "detected_dates": [],
                                    "text_sample": "Test headline Reporter", "body_char_count": 100}}],
    }
    news_findings = SchemaValidator(news_raw).run_all()
    news_titles = " | ".join(x["title"].lower() for x in news_findings)
    assert "valid newsarticle" in news_titles, news_titles
    assert "valid speakablespecification" in news_titles, news_titles
    assert "missing required properties in organization" not in news_titles, news_titles

    # Historical editorial dates are observations, not active medium/high defects.
    old_ts = 1_704_067_200  # 2024-01-01 UTC
    archive_raw = {
        "site": "https://news.example/",
        "entity": {"detected_brand_name": "Example News", "aggregated_claims": {},
                   "sameAs_links": ["https://en.wikipedia.org/wiki/Example_News"]},
        "grounding": {"wikipedia": {"has_entry": True}, "wikidata": {"has_entry": True}},
        "corroboration_templates": [],
        "pages": [{"url": "https://news.example/news/2024/01/01/story", "status_code": 200,
                   "content_type": "text/html",
                   "date_signals": {"has_any_date_signal": True,
                                    "freshest_date": {"source": "schema:datePublished", "timestamp": old_ts, "raw": "2024-01-01"},
                                    "http_headers": {}},
                   "staleness_markers": {}, "entity_data": {"nap": {}}}],
    }
    archive_findings = FreshnessValidator(archive_raw).run_all()
    assert not any(x["severity"] in ("critical", "high", "medium") and "older than" in x["title"].lower()
                   for x in archive_findings), [x["title"] for x in archive_findings]

    # A blocked page cannot turn into phantom engagement defects.
    blocked = engagement_analyzer.compile_findings([
        {"url": "https://shop.example/", "status_code": 403,
         "content_type": "text/html", "html": "<title>Just a moment...</title>"}
    ])
    assert blocked == [], blocked


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
