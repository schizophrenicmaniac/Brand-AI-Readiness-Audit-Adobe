#!/usr/bin/env python3
"""freshness_validator.py — Validate freshness, temporal signals, staleness markers, corroboration, and disambiguation.

Part of the freshness-corroboration-audit skill in the Brand AI Readiness Audit marketplace.
Analyzes raw extracted freshness data and emits standardized findings (fc-001, fc-002, ...)
categorized by severity and check ID.

Usage:
    python freshness_validator.py --raw-data /tmp/freshness-raw.json
    python freshness_validator.py --url https://example.com [--pages /,/pricing]
    python freshness_validator.py --raw-data /tmp/freshness-raw.json --corroboration-data /tmp/corroboration.json

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
    path = os.path.normpath(os.path.join(_REFS_DIR, filename))
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(json.dumps({"error": f"Reference file not found: {path}. Ensure references/ is present."}), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON in {path}: {e}"}), file=sys.stderr)
        sys.exit(1)


_CONFIG = _load_json("freshness-config.json")


class FreshnessValidator:
    def __init__(self, raw_data: dict, corroboration_data: dict = None):
        self.raw_data = raw_data
        self.site_url = raw_data.get("site", "")
        self.pages = raw_data.get("pages", [])
        self.entity_data = raw_data.get("entity", {})
        self.grounding = raw_data.get("grounding", {})
        self.corroboration_templates = raw_data.get("corroboration_templates", [])
        self.corroboration_data = corroboration_data or {}
        self.findings = []
        self._finding_counter = 1

    def _add_finding(self, severity: str, title: str, detail: str, affected_urls: list, recommendation: str):
        finding_id = f"fc-{self._finding_counter:03d}"
        self._finding_counter += 1
        self.findings.append({
            "finding_id": finding_id,
            "skill": "freshness-corroboration-audit",
            "severity": severity,
            "title": title,
            "detail": detail,
            "affected_urls": affected_urls,
            "recommendation": recommendation,
        })

    def _is_evergreen_path(self, url: str) -> bool:
        """Check if URL path is an evergreen document where old/missing dates should be suppressed."""
        parsed = urlparse(url)
        path = parsed.path.lower().rstrip("/")
        patterns = _CONFIG.get("suppression", {}).get("evergreen_path_patterns", [
            r"^/about/?$", r"^/mission/?$", r"^/history/?$", r"^/values/?$",
            r"^/privacy-policy/?$", r"^/terms/?$", r"^/legal/?$"
        ])
        for pat in patterns:
            if re.search(pat, path):
                return True
        return False

    def _is_utility_path(self, url: str) -> bool:
        """Check if URL path is a utility page."""
        parsed = urlparse(url)
        path = parsed.path.lower()
        patterns = _CONFIG.get("suppression", {}).get("utility_paths", [
            r"^/login", r"^/signin", r"^/signup", r"^/auth", r"^/cart", r"^/checkout"
        ])
        for pat in patterns:
            if re.search(pat, path):
                return True
        return False

    def _is_commercial_path(self, url: str) -> bool:
        """Check if URL is a commercial / pricing / product page."""
        lower = url.lower()
        return any(term in lower for term in ["/pricing", "/price", "/product", "/plans", "/store", "/shop"])

    def validate_fc01_date_signals(self):
        """FC-01: Last-modified / published dates check."""
        stale_threshold_months = _CONFIG.get("date_detection", {}).get("stale_threshold_months", 12)
        pricing_critical_threshold_months = _CONFIG.get("date_detection", {}).get("critical_pricing_threshold_months", 24)
        now_ts = datetime.now().timestamp()

        pages_missing_dates = []
        pages_stale_dates = []
        pages_critical_pricing_dates = []
        pages_with_fresh_dates = []

        missing_last_modified_header = []

        for page in self.pages:
            url = page.get("url", "")
            if self._is_utility_path(url):
                continue

            date_signals = page.get("date_signals", {})
            has_date = date_signals.get("has_any_date_signal", False)
            freshest = date_signals.get("freshest_date")
            is_comm = self._is_commercial_path(url)
            is_evergreen = self._is_evergreen_path(url)

            # Check HTTP Last-Modified header presence
            headers = date_signals.get("http_headers", {})
            if "last-modified" not in headers:
                missing_last_modified_header.append(url)

            if not has_date:
                if not is_evergreen:
                    pages_missing_dates.append({"url": url, "is_commercial": is_comm})
            elif freshest and "timestamp" in freshest:
                age_seconds = now_ts - freshest["timestamp"]
                age_months = age_seconds / (30.44 * 86400)

                if is_comm and age_months >= pricing_critical_threshold_months:
                    pages_critical_pricing_dates.append({
                        "url": url,
                        "age_months": int(age_months),
                        "raw_date": freshest.get("raw"),
                    })
                elif age_months >= stale_threshold_months:
                    if not is_evergreen:
                        pages_stale_dates.append({
                            "url": url,
                            "age_months": int(age_months),
                            "raw_date": freshest.get("raw"),
                            "is_commercial": is_comm,
                        })
                elif age_months <= 1:
                    pages_with_fresh_dates.append(url)

        # Critical finding: Pricing page dates extremely stale (> 24 months)
        if pages_critical_pricing_dates:
            urls = [p["url"] for p in pages_critical_pricing_dates]
            ages = [f"{p['url']} ({p['raw_date']}, ~{p['age_months']} mos ago)" for p in pages_critical_pricing_dates]
            self._add_finding(
                severity="critical",
                title="Commercial pricing page dates exceed 24 months staleness threshold",
                detail=(
                    f"Pricing or commercial page(s) exhibit timestamps over {pricing_critical_threshold_months} months old: "
                    f"{'; '.join(ages)}. AI shopping assistants and citation engines treat outdated pricing as untrustworthy "
                    "or quote obsolete tier amounts."
                ),
                affected_urls=urls,
                recommendation=(
                    "Update visible 'last updated' text and add dateModified schema with current timestamp to confirm pricing accuracy."
                ),
            )

        # High/Medium finding: Key pages missing any date signals
        if pages_missing_dates:
            comm_missing = [p["url"] for p in pages_missing_dates if p["is_commercial"]]
            other_missing = [p["url"] for p in pages_missing_dates if not p["is_commercial"]]

            if comm_missing:
                self._add_finding(
                    severity="high",
                    title="Pricing/product pages lack published or modified date signals",
                    detail=(
                        f"The commercial page(s) {', '.join(comm_missing)} contain no Last-Modified HTTP header, "
                        "meta dates, Schema.org dateModified, or visible timestamp. AI models cannot evaluate whether pricing and terms are current."
                    ),
                    affected_urls=comm_missing,
                    recommendation="Add dateModified in JSON-LD and include a visible 'Last updated' stamp in page headers or footers.",
                )

            if other_missing:
                self._add_finding(
                    severity="medium",
                    title="Key content pages lack temporal metadata signals",
                    detail=(
                        f"{len(other_missing)} page(s) have no published or modified date signals. "
                        "AI search systems (e.g., Perplexity, Google SGE) require freshness cues for recency weighting."
                    ),
                    affected_urls=other_missing,
                    recommendation="Implement datePublished and dateModified schema across key editorial and documentation pages.",
                )

        # Medium/Low finding: Pages with stale dates (> 12 months)
        if pages_stale_dates:
            urls = [p["url"] for p in pages_stale_dates]
            stale_details = [f"{p['url']} (~{p['age_months']} mos)" for p in pages_stale_dates[:4]]
            sev = "high" if any(p["is_commercial"] for p in pages_stale_dates) else "medium"
            self._add_finding(
                severity=sev,
                title="Page timestamps indicate content older than 12 months",
                detail=(
                    f"Content on {len(pages_stale_dates)} page(s) was last modified more than {stale_threshold_months} months ago: "
                    f"{', '.join(stale_details)}. May lead AI assistants to classify facts as dated."
                ),
                affected_urls=urls,
                recommendation="Review and re-verify page claims, then refresh dateModified timestamps upon content review.",
            )

        # Low finding: Missing Last-Modified HTTP header
        if len(missing_last_modified_header) == len(self.pages) and self.pages:
            self._add_finding(
                severity="low",
                title="Server does not emit Last-Modified HTTP header",
                detail=(
                    "Web server does not provide Last-Modified HTTP headers on audited responses. "
                    "Prevents crawlers from utilizing conditional GET requests (If-Modified-Since) to detect updates efficiently."
                ),
                affected_urls=[self.site_url],
                recommendation="Configure web server or CDN (Cloudflare, Fastly, CloudFront) to include Last-Modified headers on HTML responses.",
            )

        # Info finding: Positive verification
        if pages_with_fresh_dates:
            self._add_finding(
                severity="info",
                title="Active temporal freshness signals verified on key pages",
                detail=f"Recent timestamps (<30 days) successfully detected on {len(pages_with_fresh_dates)} page(s).",
                affected_urls=pages_with_fresh_dates[:5],
                recommendation="Maintain regular update cadence and keep dateModified timestamps synchronized with changes.",
            )

    def validate_fc02_staleness(self):
        """FC-02: Content staleness signals check."""
        for page in self.pages:
            url = page.get("url", "")
            if self._is_utility_path(url):
                continue

            markers = page.get("staleness_markers", {})

            # Outdated pricing keywords (e.g. "2022 pricing")
            outdated_kw = markers.get("outdated_pricing_keywords", [])
            if outdated_kw:
                is_comm = self._is_commercial_path(url)
                sev = "critical" if is_comm else "high"
                self._add_finding(
                    severity=sev,
                    title="Outdated pricing year keywords detected in copy",
                    detail=(
                        f"Page copy on {url} contains historical pricing phrasing: {', '.join(outdated_kw)}. "
                        "AI models quoting this page will identify prices as dated or inaccurate."
                    ),
                    affected_urls=[url],
                    recommendation="Remove outdated year designations from pricing copy and affirm current pricing schedule.",
                )

            # Future-tense past years (e.g. "upcoming in 2023")
            future_past = markers.get("future_past_conflicts", [])
            if future_past:
                self._add_finding(
                    severity="high",
                    title="Past calendar events described with future-tense language",
                    detail=(
                        f"Page copy on {url} references historical years using future-tense phrasing: {', '.join(future_past)}. "
                        "Indicates unmaintained copy that degrades AI confidence scoring."
                    ),
                    affected_urls=[url],
                    recommendation="Update copy to past tense or replace with current upcoming roadmap milestones.",
                )

            # Stale copyright in footer
            stale_cr = markers.get("stale_copyright")
            if stale_cr:
                years_behind = stale_cr.get("years_behind", 1)
                sev = "medium" if years_behind >= 2 else "low"
                self._add_finding(
                    severity=sev,
                    title=f"Stale footer copyright year ({stale_cr.get('detected_year')})",
                    detail=(
                        f"Footer on {url} states copyright {stale_cr.get('detected_year')}, which is {years_behind} "
                        f"year(s) behind the current year ({stale_cr.get('current_year')}). Signals site maintenance neglect to crawlers."
                    ),
                    affected_urls=[url],
                    recommendation=f"Update copyright year notice to {stale_cr.get('current_year')} or utilize dynamic current-year templating.",
                )

            # Dead outbound links
            dead_links = markers.get("dead_outbound_links", [])
            if dead_links:
                sample_dead = [f"{d.get('url')} (status: {d.get('status_code') or d.get('error')})" for d in dead_links[:3]]
                sev = "medium" if len(dead_links) >= 3 else "low"
                self._add_finding(
                    severity=sev,
                    title=f"Broken outbound reference links detected ({len(dead_links)} links)",
                    detail=(
                        f"Page {url} contains dead outbound hyperlinks: {'; '.join(sample_dead)}. "
                        "AI models following citations detect dead-end references and reduce authority ratings."
                    ),
                    affected_urls=[url],
                    recommendation="Fix or remove dead outbound links, replacing them with updated authoritative destinations.",
                )

    def validate_fc03_corroboration(self):
        """FC-03: External corroboration of core claims."""
        claims = self.entity_data.get("aggregated_claims", {})
        brand_name = self.entity_data.get("detected_brand_name", "")

        # Check if external corroboration data was supplied
        if self.corroboration_data:
            verified_claims = self.corroboration_data.get("claims", [])
            isolated_claims = []
            inconsistent_claims = []

            for c in verified_claims:
                status = c.get("status", "").lower()
                if status == "isolated":
                    isolated_claims.append(c)
                elif status == "inconsistent":
                    inconsistent_claims.append(c)

            if isolated_claims:
                c_details = [f"{c.get('type')}: '{c.get('value')}'" for c in isolated_claims]
                self._add_finding(
                    severity="high",
                    title=f"Core brand claims exist in isolation with no independent corroboration ({len(isolated_claims)} claims)",
                    detail=(
                        f"The following brand claims appear only on {self.site_url} and could not be corroborated on third-party sources: "
                        f"{'; '.join(c_details)}. AI assistants hesitate to assert uncorroborated facts in zero-shot answers."
                    ),
                    affected_urls=[self.site_url],
                    recommendation="Publish press releases, establish directory profiles (Crunchbase, LinkedIn, G2), and seek media coverage.",
                )

            if inconsistent_claims:
                inc_details = [f"{c.get('type')}: site says '{c.get('value')}', external sources say '{c.get('external_value')}'" for c in inconsistent_claims]
                self._add_finding(
                    severity="medium",
                    title="External sources report conflicting information for core brand facts",
                    detail=f"Factual discrepancy detected between website claims and external citations: {'; '.join(inc_details)}.",
                    affected_urls=[self.site_url],
                    recommendation="Align company profiles across external directories, Wikipedia, and registries to eliminate conflicting signals.",
                )
        else:
            # If no manual corroboration file provided, analyze extracted claims and emit actionable verification finding
            extracted_claim_types = []
            if claims.get("founding_year"):
                extracted_claim_types.append(f"founding year ({claims['founding_year']})")
            if claims.get("hq_location"):
                extracted_claim_types.append(f"headquarters ({claims['hq_location']})")
            if claims.get("flagship_products"):
                extracted_claim_types.append(f"products ({', '.join(claims['flagship_products'][:2])})")
            if claims.get("leadership"):
                extracted_claim_types.append(f"leadership ({', '.join(claims['leadership'][:2])})")

            # Check if entity has zero external profile links to corroborate
            same_as = self.entity_data.get("sameAs_links", [])
            if not same_as and extracted_claim_types:
                self._add_finding(
                    severity="high",
                    title="Core claims lack third-party verification anchors (zero sameAs links)",
                    detail=(
                        f"Entity declares {len(extracted_claim_types)} key claims ({', '.join(extracted_claim_types)}) "
                        "but provides zero sameAs external profile links to enable automated AI cross-verification."
                    ),
                    affected_urls=[self.site_url],
                    recommendation=(
                        "Add sameAs links to JSON-LD Organization schema pointing to authoritative third-party profiles "
                        "(LinkedIn, Crunchbase, Wikipedia, official registries)."
                    ),
                )
            elif self.corroboration_templates:
                # Informational guidance showing queries generated for corroboration
                self._add_finding(
                    severity="info",
                    title="Corroboration search query templates generated for agent verification",
                    detail=(
                        f"Generated {len(self.corroboration_templates)} external corroboration queries for {brand_name}. "
                        "Run search_web tool against these queries to confirm multi-source consensus."
                    ),
                    affected_urls=[self.site_url],
                    recommendation="Agent can execute provided search_web queries to verify founding date, HQ, and flagship offerings.",
                )

    def validate_fc04_disambiguation(self):
        """FC-04: Entity disambiguation and NAP checks."""
        same_as = self.entity_data.get("sameAs_links", [])
        brand_name = self.entity_data.get("detected_brand_name", "")
        expected_domains = _CONFIG.get("disambiguation", {}).get("expected_sameas_domains", [])

        # Check sameAs completeness
        if not same_as:
            self._add_finding(
                severity="high",
                title="Missing sameAs schema links in Organization markup",
                detail=(
                    f"Brand '{brand_name}' does not declare any sameAs links in JSON-LD. "
                    "AI search engines cannot disambiguate the brand from identically named or similar entities."
                ),
                affected_urls=[self.site_url],
                recommendation=(
                    "Add sameAs array to Organization schema linking to official profiles: "
                    "Wikipedia, Wikidata, LinkedIn, Crunchbase, and verified social channels."
                ),
            )
        else:
            present_domains = set()
            for link in same_as:
                parsed = urlparse(link)
                netloc = parsed.netloc.lower().replace("www.", "")
                for exp in expected_domains:
                    if exp in netloc:
                        present_domains.add(exp)

            missing_authoritative = [d for d in ["linkedin.com", "crunchbase.com", "wikidata.org"] if d not in present_domains]
            if missing_authoritative:
                self._add_finding(
                    severity="medium",
                    title="sameAs markup missing authoritative professional/entity profiles",
                    detail=(
                        f"sameAs schema declares {len(same_as)} links but lacks key grounding profiles: "
                        f"{', '.join(missing_authoritative)}. These sources serve as primary entity resolution anchors."
                    ),
                    affected_urls=[self.site_url],
                    recommendation=f"Add links to your {', '.join(missing_authoritative)} profiles in the sameAs array.",
                )
            else:
                self._add_finding(
                    severity="info",
                    title="Robust entity disambiguation links detected in sameAs schema",
                    detail=f"Found {len(same_as)} disambiguation links covering major directories ({', '.join(sorted(present_domains))}).",
                    affected_urls=[self.site_url],
                    recommendation="Keep sameAs social and registry URLs updated as company expands footprint.",
                )

        # NAP validation for local business types
        for page in self.pages:
            ed = page.get("entity_data", {})
            nap = ed.get("nap", {})
            if nap.get("name") and (nap.get("telephone") or nap.get("address")):
                # Has partial NAP - check completeness
                missing_nap_parts = []
                if not nap.get("telephone"):
                    missing_nap_parts.append("telephone")
                if not nap.get("address"):
                    missing_nap_parts.append("address")

                if missing_nap_parts:
                    self._add_finding(
                        severity="medium",
                        title=f"Incomplete NAP (Name-Address-Phone) markup on {page.get('url')}",
                        detail=f"Page declares local entity data but lacks explicit {', '.join(missing_nap_parts)} properties in schema.",
                        affected_urls=[page.get("url")],
                        recommendation="Provide complete postalAddress and telephone fields in LocalBusiness schema.",
                    )

        # Brand name collision heuristic: Very short or common dictionary word brand
        if brand_name and len(brand_name) <= 3 and not any("wikidata.org" in s or "wikipedia.org" in s for s in same_as):
            self._add_finding(
                severity="high",
                title=f"High entity collision risk for short brand identifier ('{brand_name}')",
                detail=(
                    f"The brand identifier '{brand_name}' is 3 characters or fewer and lacks Wikipedia/Wikidata sameAs grounding. "
                    "AI systems face severe ambiguity distinguishing this acronym from common abbreviations."
                ),
                affected_urls=[self.site_url],
                recommendation="Establish a Wikidata item and link company Crunchbase and LinkedIn profiles to disambiguate the acronym.",
            )

    def validate_fc05_wikipedia(self):
        """FC-05: Wikipedia / Wikidata presence check."""
        wp = self.grounding.get("wikipedia", {})
        wd = self.grounding.get("wikidata", {})
        brand_name = self.entity_data.get("detected_brand_name", "")

        has_wp = wp.get("has_entry", False)
        has_wd = wd.get("has_entry", False)

        if has_wp and has_wd:
            self._add_finding(
                severity="info",
                title="Strong knowledge graph grounding verified on Wikipedia and Wikidata",
                detail=(
                    f"Entity '{brand_name}' is grounded in both Wikipedia ({wp.get('title')}) and "
                    f"Wikidata ({wd.get('qid')}: {wd.get('description') or 'Entity'}). Provides top-tier grounding for AI citations."
                ),
                affected_urls=[self.site_url],
                recommendation="Ensure Wikidata statements (official website, CEO, headquarters) remain in sync with site content.",
            )
        elif has_wd and not has_wp:
            self._add_finding(
                severity="low",
                title="Wikidata entity present but no dedicated Wikipedia article found",
                detail=(
                    f"Entity '{brand_name}' has an active Wikidata item ({wd.get('qid')}) providing machine grounding, "
                    "but lacks an English Wikipedia article."
                ),
                affected_urls=[self.site_url],
                recommendation="Maintain comprehensive Wikidata properties and seek notable independent coverage if seeking Wikipedia inclusion.",
            )
        elif not has_wd and not has_wp:
            skip_smb = _CONFIG.get("suppression", {}).get("skip_wikipedia_check_for_micro_businesses", True)
            sev = "low" if skip_smb else "medium"
            self._add_finding(
                severity=sev,
                title=f"No Wikipedia or Wikidata knowledge graph presence found for '{brand_name}'",
                detail=(
                    f"Could not locate an English Wikipedia article or Wikidata item for '{brand_name}'. "
                    "AI search models (Google SGE, Perplexity, ChatGPT Search) rely heavily on Wikidata for zero-shot entity grounding."
                ),
                affected_urls=[self.site_url],
                recommendation=(
                    "Create a Wikidata item for the organization (Wikidata has lower notability hurdles than Wikipedia), "
                    "and link it in JSON-LD sameAs."
                ),
            )

    def run_all(self) -> dict:
        """Execute all checks, prioritize findings, and format report."""
        self.validate_fc01_date_signals()
        self.validate_fc02_staleness()
        self.validate_fc03_corroboration()
        self.validate_fc04_disambiguation()
        self.validate_fc05_wikipedia()

        # Sort findings by priority: critical -> high -> medium -> low -> info
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        self.findings.sort(key=lambda x: priority_order.get(x.get("severity", "info"), 99))

        # Re-number finding IDs sequentially in priority order (fc-001, fc-002, ...)
        for idx, f in enumerate(self.findings, 1):
            f["finding_id"] = f"fc-{idx:03d}"

        # Severity summary
        by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in self.findings:
            sev = f.get("severity", "info")
            if sev in by_severity:
                by_severity[sev] += 1

        return {
            "site": self.site_url,
            "skill": "freshness-corroboration-audit",
            "total_findings": len(self.findings),
            "by_severity": by_severity,
            "findings": self.findings,
        }


def main():
    parser = argparse.ArgumentParser(description="Validate freshness, corroboration, and disambiguation data.")
    parser.add_argument("--raw-data", help="Path to JSON output from freshness_extractor.py")
    parser.add_argument("--corroboration-data", help="Optional path to external corroboration results JSON")
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
        extractor_script = os.path.join(_SCRIPTS_DIR, "freshness_extractor.py")
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

    corrob_data = None
    if args.corroboration_data:
        try:
            with open(args.corroboration_data, "r", encoding="utf-8") as f:
                corrob_data = json.load(f)
        except Exception as e:
            print(json.dumps({"warning": f"Failed to load corroboration data: {e}"}), file=sys.stderr)

    validator = FreshnessValidator(raw_data, corroboration_data=corrob_data)
    report = validator.run_all()

    output_str = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_str)
    else:
        print(output_str)


if __name__ == "__main__":
    main()
