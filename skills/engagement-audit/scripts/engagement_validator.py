#!/usr/bin/env python3
"""engagement_validator.py — Validate engagement, navigation, orientation, mobile, performance, and CTA metrics.

Part of the engagement-audit skill in the Brand AI Readiness Audit marketplace.
Analyzes raw extracted engagement metrics and emits standardized findings (eg-001, eg-002, ...)
categorized by severity and check ID.

Usage:
    python engagement_validator.py --raw-data /tmp/engagement-raw.json
    python engagement_validator.py --url https://example.com [--pages /,/pricing]

Output: JSON findings report to stdout or specified file.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Reference file loader
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REFS_DIR = os.path.normpath(os.path.join(_SCRIPTS_DIR, "..", "references"))


def _load_json(filename: str) -> dict:
    path = os.path.join(_REFS_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON in {path}: {e}"}), file=sys.stderr)
        return {}


_CONFIG = _load_json("engagement-config.json")


class EngagementValidator:
    def __init__(self, raw_data: dict):
        self.raw_data = raw_data
        self.site_url = raw_data.get("site", "")
        self.pages = raw_data.get("pages", [])
        self.findings = []
        self._finding_counter = 1

    def _add_finding(self, severity: str, title: str, detail: str, affected_urls: list, recommendation: str):
        finding_id = f"eg-{self._finding_counter:03d}"
        self._finding_counter += 1
        self.findings.append({
            "finding_id": finding_id,
            "skill": "engagement-audit",
            "severity": severity,
            "title": title,
            "detail": detail,
            "affected_urls": affected_urls,
            "recommendation": recommendation,
        })

    def _is_utility_path(self, url: str) -> bool:
        """Check if URL is a utility page where standard nav/CTA expectations should be suppressed."""
        parsed = urlparse(url)
        path = parsed.path.lower()
        patterns = _CONFIG.get("suppression", {}).get("utility_page_patterns", [
            r"^/login", r"^/signin", r"^/signup", r"^/register", r"^/auth", r"^/logout",
            r"^/privacy", r"^/terms", r"^/tos", r"^/legal", r"^/cookie", r"^/cart", r"^/checkout"
        ])
        return any(re.search(pat, path) for pat in patterns)

    def _is_commercial_path(self, url: str) -> bool:
        """Check if URL represents a commercial product or pricing page."""
        lower = url.lower()
        return any(term in lower for term in ["/pricing", "/price", "/product", "/plans", "/store", "/shop"])

    def validate_eg01_navigation(self):
        """EG-01: Navigational clarity and key destinations reachability."""
        nav_cfg = _CONFIG.get("navigation", {})
        categories = nav_cfg.get("destination_categories", {
            "about": ["about", "company", "who-we-are", "mission"],
            "pricing": ["pricing", "plans", "rates"],
            "products_services": ["products", "services", "solutions", "features", "catalog"],
            "contact_support": ["contact", "support", "help", "docs", "documentation"],
        })
        category_display_names = {
            "about": "about",
            "pricing": "pricing",
            "products_services": "products/services",
            "contact_support": "contact/support",
        }

        homepage_nav = None
        for p in self.pages:
            parsed = urlparse(p.get("url", ""))
            if parsed.path in ("", "/", "/index.html"):
                homepage_nav = p.get("navigation", {})
                break

        if not homepage_nav and self.pages:
            homepage_nav = self.pages[0].get("navigation", {})

        if homepage_nav:
            destinations = homepage_nav.get("key_destinations", {})
            completely_missing = []
            footer_only = []

            for cat, aliases in categories.items():
                cat_display = category_display_names.get(cat, cat)
                found_in_primary = any(destinations.get(a, {}).get("in_primary_nav", False) for a in aliases)
                found_in_footer = any(destinations.get(a, {}).get("in_footer", False) for a in aliases)
                found_anywhere = any(destinations.get(a, {}).get("found_anywhere", False) for a in aliases)

                if not found_anywhere:
                    completely_missing.append(cat_display)
                elif found_in_footer and not found_in_primary:
                    footer_only.append(cat_display)

            if len(completely_missing) >= 2:
                self._add_finding(
                    severity="critical",
                    title="Key commercial destinations missing from site navigation",
                    detail=(
                        f"Core destinations ({', '.join(completely_missing)}) are absent from navigation. "
                        "Visitors referred by AI assistants cannot locate pricing, product specs, or contact details."
                    ),
                    affected_urls=[self.site_url],
                    recommendation="Add direct navigation links to pricing, products, about, and contact in the primary header menu.",
                )
            elif completely_missing:
                self._add_finding(
                    severity="high",
                    title=f"Navigation lacks direct link to {', '.join(completely_missing)}",
                    detail=(
                        f"The primary navigation does not provide links to {', '.join(completely_missing)}. "
                        "Referred traffic seeking these specific facts will encounter navigation friction."
                    ),
                    affected_urls=[self.site_url],
                    recommendation=f"Introduce links to {', '.join(completely_missing)} within the main header menu.",
                )

            if footer_only:
                self._add_finding(
                    severity="medium",
                    title=f"Key destinations buried only in page footer ({', '.join(footer_only)})",
                    detail=(
                        f"Links for {', '.join(footer_only)} appear only in the footer. "
                        "Visitors arriving on mobile or scanning above-the-fold miss these critical conversion pathways."
                    ),
                    affected_urls=[self.site_url],
                    recommendation="Promote important pathways from the footer into a clean header dropdown or nav bar.",
                )

            if not completely_missing and not footer_only:
                self._add_finding(
                    severity="info",
                    title="Clear and accessible primary navigation structure detected",
                    detail="Primary navigation provides direct links to key destinations (about, pricing, products/services, contact/support).",
                    affected_urls=[self.site_url],
                    recommendation="Maintain consistent navbar labeling and verify mobile menu toggle operates smoothly.",
                )

    def validate_eg02_orientation(self):
        """EG-02: Orientation cues (H1 headings, hero value proposition, and breadcrumbs)."""
        pages_missing_h1 = []
        pages_multiple_h1 = []
        pages_missing_value_prop = []
        deep_pages_missing_breadcrumbs = []
        pages_with_good_orientation = []

        for p in self.pages:
            url = p.get("url", "")
            if self._is_utility_path(url):
                continue

            orient = p.get("orientation", {})
            h1_count = orient.get("h1_count", 0)
            hero_chars = orient.get("hero_value_prop_chars", 0)
            has_bc = orient.get("has_breadcrumbs", False)
            path_depth = orient.get("url_path_depth", 0)

            # H1 checks
            if h1_count == 0:
                pages_missing_h1.append(url)
            elif h1_count > 1:
                pages_multiple_h1.append(url)

            # Hero value prop on landing / commercial pages
            is_landing = path_depth <= 1
            if is_landing and hero_chars < 30:
                pages_missing_value_prop.append(url)

            # Breadcrumbs on deep interior pages
            if path_depth >= 2 and not has_bc:
                deep_pages_missing_breadcrumbs.append(url)

            if h1_count == 1 and hero_chars >= 30:
                pages_with_good_orientation.append(url)

        if pages_missing_h1:
            self._add_finding(
                severity="high",
                title="Page lacks primary <h1> heading for orientation",
                detail=(
                    f"{len(pages_missing_h1)} page(s) lack an <h1> heading: {', '.join(pages_missing_h1[:3])}. "
                    "Visitors arriving from AI citations lack immediate heading orientation on landing."
                ),
                affected_urls=pages_missing_h1,
                recommendation="Add a descriptive <h1> summarizing the core topic or offering of the page.",
            )

        if pages_multiple_h1:
            self._add_finding(
                severity="medium",
                title="Multiple conflicting <h1> headings detected on page",
                detail=(
                    f"{len(pages_multiple_h1)} page(s) declare multiple <h1> elements: {', '.join(pages_multiple_h1[:3])}. "
                    "Confuses visual and assistive hierarchy regarding the primary page topic."
                ),
                affected_urls=pages_multiple_h1,
                recommendation="Ensure exactly one primary <h1> per document; downgrade section titles to <h2>.",
            )

        if pages_missing_value_prop:
            self._add_finding(
                severity="high",
                title="No clear value proposition or introductory copy above-the-fold",
                detail=(
                    f"Landing page(s) lack clear explanatory hero copy: {', '.join(pages_missing_value_prop[:3])}. "
                    "Referred visitors must scroll or guess what service or product is being offered."
                ),
                affected_urls=pages_missing_value_prop,
                recommendation="Add a concise 1–2 sentence value proposition directly below or beside the main hero title.",
            )

        if deep_pages_missing_breadcrumbs:
            self._add_finding(
                severity="medium",
                title="Deep interior pages lack breadcrumb navigation",
                detail=(
                    f"{len(deep_pages_missing_breadcrumbs)} deep page(s) lack breadcrumb UI or BreadcrumbList schema: "
                    f"{', '.join(deep_pages_missing_breadcrumbs[:3])}. Visitors referred directly to deep links cannot discern catalog hierarchy."
                ),
                affected_urls=deep_pages_missing_breadcrumbs,
                recommendation="Implement visible breadcrumbs and JSON-LD BreadcrumbList markup on catalog and documentation pages.",
            )

        if pages_with_good_orientation and not pages_missing_h1 and not pages_missing_value_prop:
            self._add_finding(
                severity="info",
                title="Strong orientation hierarchy verified on key pages",
                detail="Key landing pages feature clear single <h1> headings and descriptive above-the-fold introductory text.",
                affected_urls=pages_with_good_orientation[:4],
                recommendation="Keep introductory copy aligned with search queries that drive visitor referrals.",
            )

    def validate_eg03_performance(self):
        """EG-03: Load performance red flags (render-blocking scripts, stylesheets, image attributes)."""
        pages_heavy_scripts = []
        pages_heavy_css = []
        pages_cls_images = []
        pages_no_lazy = []
        pages_domain_sprawl = []

        for p in self.pages:
            url = p.get("url", "")
            perf = p.get("performance", {})

            rb_scripts = perf.get("render_blocking_scripts_count", 0)
            css_count = perf.get("external_stylesheets_count", 0)
            missing_dims = perf.get("images_missing_dimensions", 0)
            missing_lazy = perf.get("images_missing_lazy_loading", 0)
            domains_count = perf.get("third_party_domains_count", 0)

            if rb_scripts > 3:
                pages_heavy_scripts.append({"url": url, "count": rb_scripts})
            if css_count > 4:
                pages_heavy_css.append({"url": url, "count": css_count})
            if missing_dims > 0:
                pages_cls_images.append({"url": url, "count": missing_dims})
            if missing_lazy >= 3:
                pages_no_lazy.append({"url": url, "count": missing_lazy})
            if domains_count > 10:
                pages_domain_sprawl.append({"url": url, "count": domains_count})

        if pages_heavy_scripts:
            urls = [p["url"] for p in pages_heavy_scripts]
            script_summaries = [f"{p['url']} ({p['count']} scripts)" for p in pages_heavy_scripts[:3]]
            self._add_finding(
                severity="medium",
                title="Multiple render-blocking scripts detected in <head>",
                detail=(
                    f"Page(s) contain render-blocking script tags: {', '.join(script_summaries)}. "
                    "Synchronous scripts block HTML parsing and directly inflate First Contentful Paint (FCP)."
                ),
                affected_urls=urls,
                recommendation="Add async or defer attributes to non-critical scripts, or migrate scripts to footer / tag manager.",
            )

        if pages_heavy_css:
            urls = [p["url"] for p in pages_heavy_css]
            self._add_finding(
                severity="medium",
                title="Excessive external stylesheet requests in <head>",
                detail=(
                    f"{len(pages_heavy_css)} page(s) load > 4 external CSS files: {', '.join(urls[:3])}. "
                    "Increases network round-trips before initial paint."
                ),
                affected_urls=urls,
                recommendation="Bundle and minify CSS files or inline critical CSS to accelerate above-the-fold rendering.",
            )

        if pages_cls_images:
            urls = [p["url"] for p in pages_cls_images]
            self._add_finding(
                severity="medium",
                title="Images missing explicit width/height dimensions (CLS risk)",
                detail=(
                    f"{len(pages_cls_images)} page(s) feature images without width/height attributes: {', '.join(urls[:3])}. "
                    "Causes Cumulative Layout Shift (CLS) as images load, jarring arriving readers."
                ),
                affected_urls=urls,
                recommendation="Specify width and height attributes on all <img> elements, using CSS aspect-ratio for responsiveness.",
            )

        if pages_domain_sprawl:
            urls = [p["url"] for p in pages_domain_sprawl]
            self._add_finding(
                severity="medium",
                title="Third-party domain sprawl exceeds 10 hostnames",
                detail=(
                    f"Pages connect to high volumes of third-party domains: {', '.join(urls[:3])}. "
                    "Each external domain incurs separate DNS resolution, TLS handshake, and connection overhead."
                ),
                affected_urls=urls,
                recommendation="Consolidate tracking pixels and audit third-party widget scripts to reduce connection latency.",
            )

        if pages_no_lazy:
            urls = [p["url"] for p in pages_no_lazy]
            self._add_finding(
                severity="low",
                title="Offscreen images lack native loading='lazy' attribute",
                detail=f"{len(pages_no_lazy)} page(s) load offscreen images eagerly: {', '.join(urls[:3])}.",
                affected_urls=urls,
                recommendation="Add loading='lazy' to images below the initial fold to conserve visitor mobile bandwidth.",
            )

    def validate_eg04_mobile(self):
        """EG-04: Mobile responsiveness and viewport accessibility."""
        missing_viewport = []
        disables_zoom = []
        valid_viewport = []

        for p in self.pages:
            url = p.get("url", "")
            mob = p.get("mobile", {})

            if not mob.get("has_viewport_meta", False):
                missing_viewport.append(url)
            else:
                if mob.get("disables_zoom", False):
                    disables_zoom.append(url)
                if mob.get("has_width_device", False):
                    valid_viewport.append(url)

        if missing_viewport:
            self._add_finding(
                severity="critical",
                title="Missing <meta name='viewport'> tag breaks mobile responsiveness",
                detail=(
                    f"Page(s) lack a viewport meta tag: {', '.join(missing_viewport[:3])}. "
                    "Mobile browsers render at standard desktop resolution (980px), forcing microscopic text and instant visitor bounce."
                ),
                affected_urls=missing_viewport,
                recommendation="Add <meta name='viewport' content='width=device-width, initial-scale=1.0'> in <head>.",
            )

        if disables_zoom:
            self._add_finding(
                severity="high",
                title="Viewport tag restricts pinch-to-zoom (accessibility violation)",
                detail=(
                    f"Page(s) configure user-scalable=no or maximum-scale=1.0: {', '.join(disables_zoom[:3])}. "
                    "Prevents visually impaired visitors from zooming, violating WCAG 1.4.4."
                ),
                affected_urls=disables_zoom,
                recommendation="Remove user-scalable=no and maximum-scale constraints from viewport configuration.",
            )

        if valid_viewport and not missing_viewport:
            self._add_finding(
                severity="info",
                title="Mobile viewport properly configured for responsive rendering",
                detail="All audited pages declare responsive width=device-width viewport settings.",
                affected_urls=valid_viewport[:4],
                recommendation="Regularly test touch tap target spacing (>= 48x48 CSS px) on physical mobile devices.",
            )

    def validate_eg05_ctas(self):
        """EG-05: Calls-to-action clarity and actionability."""
        commercial_missing_cta = []
        generic_cta_dominated = []
        excessive_ctas = []
        clear_ctas = []

        for p in self.pages:
            url = p.get("url", "")
            if self._is_utility_path(url):
                continue

            cta = p.get("ctas", {})
            total_ctas = cta.get("total_ctas", 0)
            actionable = cta.get("actionable_ctas_count", 0)
            generic = cta.get("generic_ctas_count", 0)

            is_comm = self._is_commercial_path(url)

            if is_comm and (total_ctas == 0 or actionable == 0):
                commercial_missing_cta.append(url)

            if generic >= 3 and generic > actionable:
                generic_cta_dominated.append({"url": url, "count": generic, "samples": cta.get("generic_ctas_sample", [])})

            if total_ctas > 8:
                excessive_ctas.append(url)

            if actionable > 0 and generic == 0:
                clear_ctas.append(url)

        if commercial_missing_cta:
            self._add_finding(
                severity="high",
                title="No actionable call-to-action buttons on commercial/product page",
                detail=(
                    f"Commercial page(s) lack prominent actionable CTAs: {', '.join(commercial_missing_cta[:3])}. "
                    "Visitors arriving from AI shopping or product queries have no immediate conversion path."
                ),
                affected_urls=commercial_missing_cta,
                recommendation="Add distinct primary CTA buttons ('Start Free Trial', 'Request Demo', 'Buy Now') above the fold.",
            )

        if generic_cta_dominated:
            urls = [p["url"] for p in generic_cta_dominated]
            self._add_finding(
                severity="medium",
                title="Call-to-action buttons dominated by ambiguous generic copy ('Learn More')",
                detail=(
                    f"{len(generic_cta_dominated)} page(s) overuse generic CTA phrases: {', '.join(urls[:3])}. "
                    "Repeated 'Learn More' buttons fail to communicate value and reduce conversion clarity."
                ),
                affected_urls=urls,
                recommendation="Replace generic labels with specific verbs indicating the destination ('Read Pricing Guide', 'Explore Features').",
            )

        if excessive_ctas:
            self._add_finding(
                severity="low",
                title="High density of competing action buttons may induce decision fatigue",
                detail=f"{len(excessive_ctas)} page(s) contain > 8 distinct button triggers: {', '.join(excessive_ctas[:3])}.",
                affected_urls=excessive_ctas,
                recommendation="Establish a single prominent primary CTA per viewport, styling secondary actions subtly.",
            )

        if clear_ctas:
            self._add_finding(
                severity="info",
                title="Actionable, distinct conversion paths detected",
                detail="Pages feature action-oriented call-to-action wording with clear intent.",
                affected_urls=clear_ctas[:4],
                recommendation="Perform A/B testing on button colors and placement to continually refine conversion rates.",
            )

    def validate_eg06_search(self):
        """EG-06: On-site search availability and discoverability."""
        has_search_anywhere = any(p.get("search", {}).get("has_search", False) for p in self.pages)

        # Only audit search if site has multiple pages (not a micro-page)
        if len(self.pages) >= 3 and not has_search_anywhere:
            self._add_finding(
                severity="medium",
                title="No discoverable on-site search functionality detected",
                detail=(
                    f"None of the {len(self.pages)} audited pages on {self.site_url} contain a search form or input. "
                    "Visitors arriving via AI referrals who wish to explore related queries cannot search your catalog."
                ),
                affected_urls=[self.site_url],
                recommendation="Embed a global search input in the header or navigation bar with autocomplete support.",
            )
        elif has_search_anywhere:
            self._add_finding(
                severity="info",
                title="Discoverable on-site search input verified",
                detail="Site search forms or search input controls are present in page navigation.",
                affected_urls=[self.site_url],
                recommendation="Ensure on-site search results provide snippet summaries and facet filters for content-heavy catalogs.",
            )

    def validate_eg07_context_retention(self):
        """EG-07: Context retention and returning-visitor features."""
        has_retention_anywhere = any(p.get("context_retention", {}).get("has_context_retention", False) for p in self.pages)

        if not has_retention_anywhere:
            self._add_finding(
                severity="low",
                title="No returning-visitor context retention or personalization features detected",
                detail=(
                    f"Site ({self.site_url}) shows no stateful continuity features (recently viewed items, wishlist/saved items, "
                    "or account personalization). Returning visitors arriving from AI assistants find no prior browsing anchor."
                ),
                affected_urls=[self.site_url],
                recommendation="Introduce client-side browsing history widgets ('Recently Viewed') or favoriting to anchor returning visitors.",
            )
        else:
            self._add_finding(
                severity="info",
                title="Returning-visitor continuity anchors detected",
                detail="Detected account/profile controls or client-side retention state hooks for returning visitors.",
                affected_urls=[self.site_url],
                recommendation="Leverage cookie/session context to greet returning visitors referred back by AI search assistants.",
            )

    def run_all(self) -> dict:
        """Execute all checks, prioritize findings, and format report."""
        self.validate_eg01_navigation()
        self.validate_eg02_orientation()
        self.validate_eg03_performance()
        self.validate_eg04_mobile()
        self.validate_eg05_ctas()
        self.validate_eg06_search()
        self.validate_eg07_context_retention()

        # Sort findings by priority: critical -> high -> medium -> low -> info
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        self.findings.sort(key=lambda x: priority_order.get(x.get("severity", "info"), 99))

        # Re-number finding IDs sequentially in priority order (eg-001, eg-002, ...)
        for idx, f in enumerate(self.findings, 1):
            f["finding_id"] = f"eg-{idx:03d}"

        # Severity summary
        by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in self.findings:
            sev = f.get("severity", "info")
            if sev in by_severity:
                by_severity[sev] += 1

        return {
            "site": self.site_url,
            "skill": "engagement-audit",
            "total_findings": len(self.findings),
            "by_severity": by_severity,
            "findings": self.findings,
        }


def main():
    parser = argparse.ArgumentParser(description="Validate on-site engagement, navigation, and UX metrics.")
    parser.add_argument("--raw-data", help="Path to JSON output from engagement_extractor.py")
    parser.add_argument("--url", help="Site URL (if running extraction inline)")
    parser.add_argument("--pages", help="Comma-separated paths to audit if running inline")
    parser.add_argument("--output", help="Optional output JSON file path")
    args = parser.parse_args()

    raw_data = None
    if args.raw_data:
        try:
            with open(args.raw_data, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception as e:
            print(json.dumps({"error": f"Failed to read --raw-data file {args.raw_data}: {e}"}), file=sys.stderr)
            sys.exit(1)
    elif args.url:
        extractor_script = os.path.join(_SCRIPTS_DIR, "engagement_extractor.py")
        cmd = [sys.executable, extractor_script, "--url", args.url]
        if args.pages:
            cmd.extend(["--pages", args.pages])
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(json.dumps({"error": f"Extractor failed: {proc.stderr}"}), file=sys.stderr)
            sys.exit(1)
        raw_data = json.loads(proc.stdout)
    else:
        print(json.dumps({"error": "Must supply either --raw-data or --url"}), file=sys.stderr)
        sys.exit(1)

    validator = EngagementValidator(raw_data)
    report = validator.run_all()

    output_str = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_str)
    else:
        print(output_str)


if __name__ == "__main__":
    main()
