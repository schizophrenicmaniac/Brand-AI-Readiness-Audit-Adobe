#!/usr/bin/env python3
"""Validate extracted engagement evidence and emit shared-contract findings.

Integration:
    EngagementValidator(raw).run_all() -> list[dict]

Only successfully fetched HTML pages are eligible for checks. This prevents a
403, bot challenge, timeout, or non-HTML response from becoming a phantom UX
finding.
"""

import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REFS_DIR = os.path.normpath(os.path.join(_SCRIPTS_DIR, "..", "references"))
_LIB_DIR = os.path.normpath(os.path.join(_SCRIPTS_DIR, "..", "..", "..", "lib"))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from report import make_finding  # noqa: E402


def _load_json(filename):
    try:
        with open(os.path.join(_REFS_DIR, filename), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


_CONFIG = _load_json("engagement-config.json")
SKILL = "engagement-audit"
_PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


class EngagementValidator:
    def __init__(self, raw_data: dict):
        self.raw_data = raw_data if isinstance(raw_data, dict) else {}
        self.site_url = self.raw_data.get("site", "")
        self.pages = self.raw_data.get("pages", []) if isinstance(self.raw_data.get("pages", []), list) else []
        self.findings = []

    def _content_pages(self):
        pages = []
        for page in self.pages:
            if not isinstance(page, dict):
                continue
            status = page.get("status_code")
            content_type = (page.get("content_type") or "").lower()
            explicit = page.get("html_success")
            if explicit is False or not isinstance(status, int) or not 200 <= status < 300:
                continue
            if content_type and "html" not in content_type and "xhtml" not in content_type:
                continue
            if page.get("fetch_status") in ("blocked", "non_html", "empty", "http_error", "fetch_error"):
                continue
            # Backward-compatible raw input may not have html_success/fetch_status. It
            # is eligible only when at least one extraction section has actual data.
            if explicit is not True and not any(page.get(key) for key in ("navigation", "orientation", "performance", "mobile", "ctas", "search", "context_retention")):
                continue
            pages.append(page)
        return pages

    def _add(self, check_id, severity, title, detail, urls, recommendation, locator, expected=None):
        urls = [url for url in (urls or []) if url]
        url = urls[0] if urls else self.site_url
        if len(urls) > 1:
            detail += f" Affected sample: {', '.join(urls[:5])}{' …' if len(urls) > 5 else ''}."
        self.findings.append(make_finding(
            skill=SKILL, check_id=check_id, severity=severity, title=title,
            action_summary=recommendation, evidence_url=url, evidence_source="html",
            evidence_observed=detail, evidence_locator=locator,
            evidence_expected=expected,
        ))

    @staticmethod
    def _url(page):
        return page.get("final_url") or page.get("url") or ""

    @staticmethod
    def _kind(page):
        return (page.get("page_profile") or {}).get("kind", "general")

    @staticmethod
    def _is_utility(url):
        path = urlparse(url).path.lower()
        patterns = _CONFIG.get("suppression", {}).get("utility_page_patterns", [])
        return any(re.search(pattern, path) for pattern in patterns)

    @staticmethod
    def _is_editorial_permalink(page):
        url = EngagementValidator._url(page)
        path = urlparse(url).path.lower()
        return EngagementValidator._kind(page) == "editorial" and bool(
            re.search(r"/(?:19|20)\d{2}/(?:0?[1-9]|1[0-2])(?:/|$)", path)
            or len([part for part in path.split("/") if part]) >= 2
        )

    def validate_eg01_navigation(self, pages):
        eligible = [p for p in pages if not self._is_utility(self._url(p))]
        missing = [self._url(p) for p in eligible if not (p.get("navigation") or {}).get("has_primary_nav")]
        if missing:
            self._add(
                "EG-01-nav-structure", "medium", "Primary navigation structure not detected",
                f"{len(missing)} of {len(eligible)} eligible HTML page(s) expose neither a navigation container nor header navigation links.",
                missing, "Provide a consistent primary navigation region with descriptive internal links.",
                "nav, [role=navigation], header navigation", "A primary navigation structure on each standard content page",
            )

        no_landmark = [self._url(p) for p in eligible if (p.get("navigation") or {}).get("has_primary_nav") and not (p.get("navigation") or {}).get("has_navigation_landmark")]
        if no_landmark:
            self._add(
                "EG-01-nav-landmark", "low", "Primary navigation lacks a semantic landmark",
                f"{len(no_landmark)} page(s) have inferred header/menu navigation but no <nav> or role=navigation landmark.",
                no_landmark, "Wrap primary navigation in <nav> or assign role=navigation and an accessible label.",
                "nav, [role=navigation]",
            )

        nav_pages = [p for p in eligible if len((p.get("navigation") or {}).get("primary_nav_labels", [])) >= 2]
        if len(nav_pages) >= 3:
            baseline = set((nav_pages[0].get("navigation") or {}).get("primary_nav_labels", []))
            inconsistent = []
            for page in nav_pages[1:]:
                labels = set((page.get("navigation") or {}).get("primary_nav_labels", []))
                union = baseline | labels
                similarity = len(baseline & labels) / len(union) if union else 1
                if similarity < 0.35:
                    inconsistent.append((self._url(page), round(similarity, 2)))
            if len(inconsistent) >= 2:
                details = ", ".join(f"{url} ({score:.0%} label overlap)" for url, score in inconsistent[:4])
                self._add(
                    "EG-01-nav-consistency", "medium", "Primary navigation changes substantially across pages",
                    f"Compared with {self._url(nav_pages[0])}, {len(inconsistent)} page(s) have under 35% navigation-label overlap: {details}.",
                    [url for url, _ in inconsistent], "Keep core navigation labels and destinations consistent across standard page templates.",
                    "primary navigation link text", ">=35% label overlap with the sampled baseline navigation",
                )

        commercial = [p for p in eligible if self._kind(p) == "commercial"]
        if commercial:
            aliases = _CONFIG.get("navigation", {}).get("destination_categories", {})
            missing_path = []
            for page in commercial:
                destinations = (page.get("navigation") or {}).get("key_destinations", {})
                offering = aliases.get("products_services", [])
                support = aliases.get("contact_support", [])
                if not any(destinations.get(a, {}).get("found_anywhere") for a in offering + support):
                    missing_path.append(self._url(page))
            if missing_path:
                self._add(
                    "EG-01-commercial-path", "medium", "Commercial pages lack an offering or support navigation path",
                    f"{len(missing_path)} commercial-profile page(s) contain no measured link matching product/service or contact/support aliases. Pricing and company links are not required by this check.",
                    missing_path, "Add a relevant route to offerings, documentation, help, or contact; use labels appropriate to the site.",
                    "a[href] text/href in page navigation", "At least one context-appropriate offering or support destination",
                )

    def validate_eg02_orientation(self, pages):
        eligible = [p for p in pages if not self._is_utility(self._url(p))]
        missing_h1 = [self._url(p) for p in eligible if (p.get("orientation") or {}).get("h1_count", 0) == 0]
        if missing_h1:
            self._add(
                "EG-02-h1-missing", "medium", "Pages lack a primary H1 orientation cue",
                f"{len(missing_h1)} of {len(eligible)} eligible HTML page(s) have zero non-empty <h1> elements.",
                missing_h1, "Add a descriptive H1 that states the page topic or purpose.", "h1", "At least one non-empty H1",
            )
        multiple = [(self._url(p), (p.get("orientation") or {}).get("h1_count", 0)) for p in eligible if (p.get("orientation") or {}).get("h1_count", 0) > 2]
        if multiple:
            self._add(
                "EG-02-h1-hierarchy", "low", "Page heading hierarchy has several primary headings",
                f"{len(multiple)} page(s) have more than two H1s; highest measured count is {max(count for _, count in multiple)}.",
                [url for url, _ in multiple], "Review the heading outline and reserve H1 for primary page topics where practical.", "h1", "A concise primary heading hierarchy",
            )

        intro_missing = []
        for page in eligible:
            orient = page.get("orientation") or {}
            path = urlparse(self._url(page)).path.strip("/")
            landing = not path or self._kind(page) == "commercial"
            if landing and orient.get("h1_count", 0) and orient.get("hero_value_prop_chars", 0) < 20:
                intro_missing.append(self._url(page))
        if intro_missing:
            self._add(
                "EG-02-intro-copy", "medium", "Landing pages lack measurable introductory copy",
                f"{len(intro_missing)} homepage/commercial landing page(s) have an H1 but no introductory paragraph or H2 of at least 20 characters near the start of <main>.",
                intro_missing, "Add concise introductory copy near the primary heading explaining the page's purpose or offering.",
                "main p, main h2, hero/intro container", "At least 20 characters of introductory text near the page start",
            )

        breadcrumb_missing = []
        for page in eligible:
            orient = page.get("orientation") or {}
            depth = orient.get("url_path_depth", 0)
            hierarchy_expected = self._kind(page) in ("documentation", "commercial") or depth >= 3
            if depth >= 2 and hierarchy_expected and not self._is_editorial_permalink(page) and not orient.get("has_breadcrumbs"):
                breadcrumb_missing.append(self._url(page))
        if breadcrumb_missing:
            self._add(
                "EG-02-breadcrumb", "low", "Hierarchical interior pages lack breadcrumb cues",
                f"{len(breadcrumb_missing)} deep documentation/commercial or 3+ segment page(s) expose neither visible nor structured breadcrumbs; editorial permalinks are excluded.",
                breadcrumb_missing, "Add visible breadcrumbs on genuinely hierarchical templates and optionally mirror them with BreadcrumbList data.",
                ".breadcrumb, [aria-label*=breadcrumb], BreadcrumbList", "Breadcrumbs on deep hierarchical pages",
            )

    def validate_eg03_performance(self, pages):
        cfg = _CONFIG.get("performance_red_flags", {})
        rules = [
            ("render_blocking_scripts_count", cfg.get("max_render_blocking_scripts", 3), "EG-03-blocking-scripts", "Synchronous head scripts create structural render delay risk", "script[src] in head without async/defer", "Defer or make non-critical scripts asynchronous."),
            ("external_stylesheets_count", cfg.get("max_external_stylesheets", 4), "EG-03-stylesheets", "Many render-blocking stylesheets are requested from head", "head link[rel=stylesheet]", "Consolidate stylesheets or inline only measured critical CSS where appropriate."),
            ("third_party_domains_count", cfg.get("max_third_party_domains", 10), "EG-03-third-parties", "Many third-party resource domains are referenced", "external script/link/iframe/img/source hosts", "Audit and consolidate non-essential third-party resources."),
        ]
        for field, threshold, check_id, title, locator, action in rules:
            affected = [(self._url(p), (p.get("performance") or {}).get(field, 0)) for p in pages if (p.get("performance") or {}).get(field, 0) > threshold]
            if affected:
                evidence = ", ".join(f"{url}: {count}" for url, count in affected[:4])
                self._add(check_id, "medium", title, f"Measured threshold >{threshold}; observed {evidence}.", [u for u, _ in affected], action, locator, f"At most {threshold}")

        dimensions = []
        for page in pages:
            perf = page.get("performance") or {}
            if perf.get("total_images", 0) >= 2 and perf.get("images_missing_dimensions", 0) >= 2 and perf.get("images_missing_dimensions_ratio", 0) >= 0.5:
                dimensions.append((self._url(page), perf.get("images_missing_dimensions"), perf.get("total_images")))
        if dimensions:
            summary = ", ".join(f"{url}: {missing}/{total}" for url, missing, total in dimensions[:4])
            self._add(
                "EG-03-image-dimensions", "medium", "Most images lack intrinsic dimensions",
                f"At least 50% and at least two images lack both width and height on {len(dimensions)} page(s): {summary}.",
                [u for u, _, _ in dimensions], "Provide intrinsic width/height or an equivalent reserved aspect ratio to reduce layout-shift risk.",
                "img:not([width][height])", "Dimensions on at least half of sampled images",
            )

        lazy = [(self._url(p), (p.get("performance") or {}).get("images_missing_lazy_loading", 0), (p.get("performance") or {}).get("below_initial_images", 0)) for p in pages if (p.get("performance") or {}).get("below_initial_images", 0) >= 3 and (p.get("performance") or {}).get("images_missing_lazy_loading", 0) >= 3]
        if lazy:
            summary = ", ".join(f"{url}: {missing}/{total}" for url, missing, total in lazy[:4])
            self._add(
                "EG-03-image-loading", "low", "Later document images load eagerly",
                f"Using document order as a static proxy for below-initial content, at least three later images omit loading=lazy: {summary}.",
                [u for u, _, _ in lazy], "Consider native lazy loading for non-critical images after verifying actual viewport placement.",
                "img after the first two document images", "loading=lazy on non-critical later images",
            )

    def validate_eg04_mobile(self, pages):
        missing = [self._url(p) for p in pages if not (p.get("mobile") or {}).get("has_viewport_meta")]
        if missing:
            self._add(
                "EG-04-viewport-missing", "critical", "Mobile viewport metadata is missing",
                f"{len(missing)} of {len(pages)} successful HTML page(s) have no non-empty viewport meta tag.",
                missing, "Add <meta name=viewport content='width=device-width, initial-scale=1'> in head.",
                "meta[name=viewport]", "width=device-width viewport metadata",
            )
        wrong_width = [self._url(p) for p in pages if (p.get("mobile") or {}).get("has_viewport_meta") and not (p.get("mobile") or {}).get("has_width_device")]
        if wrong_width:
            self._add(
                "EG-04-viewport-width", "high", "Viewport does not use device width",
                f"{len(wrong_width)} page(s) declare viewport metadata without an exact width=device-width directive.",
                wrong_width, "Set width=device-width while preserving any justified scale directives.",
                "meta[name=viewport] content", "width=device-width",
            )
        zoom = [(self._url(p), (p.get("mobile") or {}).get("viewport_content", "")) for p in pages if (p.get("mobile") or {}).get("disables_zoom")]
        if zoom:
            observed = "; ".join(f"{url}: {content}" for url, content in zoom[:3])
            self._add(
                "EG-04-zoom", "high", "Viewport configuration restricts user zoom",
                f"{len(zoom)} page(s) use user-scalable=no/0 or maximum-scale below 2: {observed}.",
                [u for u, _ in zoom], "Remove user-scalable restrictions and allow at least 200% zoom.",
                "meta[name=viewport] content", "No user-scalable=no and maximum-scale >= 2 when specified",
            )

    def validate_eg05_ctas(self, pages):
        eligible = [p for p in pages if not self._is_utility(self._url(p))]
        missing = []
        for page in eligible:
            cta = page.get("ctas") or {}
            if self._kind(page) == "commercial" and cta.get("total_ctas", 0) == 0:
                missing.append(self._url(page))
        if missing:
            self._add(
                "EG-05-commercial-cta", "high", "Commercial pages expose no measurable action control",
                f"{len(missing)} commercial-profile page(s) have zero buttons, styled action links, or recognized action labels.",
                missing, "Provide a context-specific next action such as purchasing, evaluation, contact, or documentation.",
                "button, [role=button], styled/action-labelled links", "At least one context-appropriate action control",
            )

        generic = []
        for page in eligible:
            cta = page.get("ctas") or {}
            if self._kind(page) != "editorial" and cta.get("generic_ctas_count", 0) >= 3 and cta.get("generic_cta_ratio", 0) >= 0.6:
                generic.append((self._url(page), cta.get("generic_ctas_count"), cta.get("generic_cta_ratio"), cta.get("generic_ctas_sample", [])))
        if generic:
            worst = max(generic, key=lambda item: item[1])
            self._add(
                "EG-05-generic-cta", "low", "Calls to action are dominated by generic labels",
                f"{len(generic)} non-editorial page(s) have at least three generic CTAs comprising >=60% of measured controls; worst page has {worst[1]} ({worst[2]:.0%}), samples: {', '.join(worst[3][:4])}.",
                [u for u, _, _, _ in generic], "Replace repeated generic labels with destination-specific action text.",
                "button/link control text", "Fewer than 3 generic labels or under 60% of measured controls",
            )

        maximum = _CONFIG.get("calls_to_action", {}).get("max_competing_primary_ctas_per_page", 3)
        overload = [(self._url(p), (p.get("ctas") or {}).get("actionable_ctas_unique_count", (p.get("ctas") or {}).get("actionable_ctas_count", 0))) for p in eligible if (p.get("ctas") or {}).get("actionable_ctas_unique_count", (p.get("ctas") or {}).get("actionable_ctas_count", 0)) > maximum]
        if overload:
            evidence = ", ".join(f"{u}: {n}" for u, n in overload[:4])
            self._add(
                "EG-05-cta-density", "low", "Many distinct high-intent actions compete on a page",
                f"Measured actionable CTA count exceeds {maximum}: {evidence}.",
                [u for u, _ in overload], "Prioritize one primary action and present secondary actions with lower visual emphasis.",
                "recognized actionable button/link labels", f"At most {maximum} distinct actionable CTA labels",
            )

    def validate_eg06_search(self, pages):
        eligible = [p for p in pages if not self._is_utility(self._url(p))]
        found = [p for p in eligible if (p.get("search") or {}).get("has_search")]
        inaccessible = [(self._url(p), (p.get("search") or {}).get("inaccessible_icon_triggers_count", 0)) for p in found if (p.get("search") or {}).get("inaccessible_icon_triggers_count", 0)]
        if inaccessible:
            self._add(
                "EG-06-search-label", "low", "Search icon triggers lack an accessible name",
                f"{len(inaccessible)} page(s) contain icon-class search triggers with no text, aria-label, or title; highest count is {max(n for _, n in inaccessible)}.",
                [u for u, _ in inaccessible], "Give icon-only search triggers an aria-label such as 'Search'.", "search trigger accessible name", "Text, aria-label, or title",
            )
        content_rich = sum(self._kind(p) in ("documentation", "editorial") for p in eligible)
        enough_evidence = len(eligible) >= 5 or (len(eligible) >= 3 and content_rich >= 3)
        if enough_evidence and not found:
            self._add(
                "EG-06-search-absent", "medium", "No site-search control found in a content-rich page sample",
                f"Search inputs/forms/roles/triggers were absent from {len(eligible)} eligible successful pages; {content_rich} were classified as documentation/editorial. Small sites and utility pages are suppressed.",
                [self._url(p) for p in eligible], "Add discoverable site search when the full content inventory warrants it.",
                "input/form/[role=search]/labelled search trigger", "A search control in a >=5-page or content-rich >=3-page sample",
            )

    def validate_eg07_context(self, pages):
        eligible = [p for p in pages if not self._is_utility(self._url(p))]
        found = [p for p in eligible if (p.get("context_retention") or {}).get("has_context_retention")]
        commercial_count = sum(self._kind(p) == "commercial" for p in eligible)
        if not found and len(eligible) >= 3 and commercial_count >= 2:
            self._add(
                "EG-07-continuity-absent", "low", "No continuity feature found across a commercial page sample",
                f"Across {len(eligible)} successful pages ({commercial_count} commercial-profile), no account link, saved/favorite/history feature, or client-storage hook was detected. General, news, and documentation sites are not required to provide these features.",
                [self._url(p) for p in eligible], "Consider saved items, recently viewed content, or account continuity only when useful to the visitor journey.",
                "account/save/history controls or storage hooks", "A relevant continuity mechanism on stateful commercial journeys",
            )

    def run_all(self) -> list[dict]:
        """Run all checks and return only shared-contract findings with stable ``id``."""
        self.findings = []
        pages = self._content_pages()
        if not pages:
            return []
        self.validate_eg01_navigation(pages)
        self.validate_eg02_orientation(pages)
        self.validate_eg03_performance(pages)
        self.validate_eg04_mobile(pages)
        self.validate_eg05_ctas(pages)
        self.validate_eg06_search(pages)
        self.validate_eg07_context(pages)
        self.findings.sort(key=lambda f: (_PRIORITY.get(f.get("severity"), 99), f.get("id", "")))
        return self.findings


def main():
    parser = argparse.ArgumentParser(description="Validate engagement extractor JSON.")
    parser.add_argument("--raw-data")
    parser.add_argument("--url")
    parser.add_argument("--pages", default="")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.raw_data:
        try:
            with open(args.raw_data, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"cannot read raw data: {exc}")
    elif args.url:
        import requests
        from engagement_extractor import build_raw
        session = requests.Session()
        session.headers.update({"User-Agent": _CONFIG.get("extraction", {}).get("user_agent", "BrandAIReadinessAudit/1.0")})
        raw = build_raw(args.url, args.pages.split(",") if args.pages else [], session, args.max_pages)
    else:
        parser.error("supply --raw-data or --url")
    findings = EngagementValidator(raw).run_all()
    report = {"site": raw.get("site", ""), "skill": SKILL, "total_findings": len(findings), "findings": findings}
    output = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main()
