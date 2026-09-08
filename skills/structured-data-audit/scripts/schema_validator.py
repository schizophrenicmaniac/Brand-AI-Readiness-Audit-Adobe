#!/usr/bin/env python3
"""schema_validator.py — Validate Schema.org JSON-LD, Open Graph, Twitter cards, meta tags, and llms.txt.

Part of the structured-data-audit skill in the Brand AI Readiness Audit marketplace.
Analyzes raw extracted structured data and emits standardized findings (sd-001, sd-002, ...)
categorized by severity and check ID.

Usage:
    python schema_validator.py --raw-data /tmp/extracted_sd.json
    python schema_validator.py --url https://example.com [--pages /,/pricing]

Output: JSON findings report to stdout.
"""

import argparse
import json
import os
import re
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


_CONFIG = _load_json("structured-data-config.json")
_REGISTRY = _load_json("schema-type-registry.json")

_SCHEMA_TYPES = _REGISTRY.get("types", {})
_ANTI_PATTERNS = _REGISTRY.get("anti_patterns", [])

_PLACEHOLDER_REGEX = re.compile(
    r"\b(lorem\s+ipsum|placeholder|example\.com|acme\s+corp|your[-_\s]name|todo|test\s+company)\b",
    re.IGNORECASE,
)


class SchemaValidator:
    def __init__(self, raw_data: dict):
        self.raw_data = raw_data
        self.site_url = raw_data.get("site", "")
        self.pages = raw_data.get("pages", [])
        self.llms_txt_data = raw_data.get("llms_txt", {})
        self.findings = []
        self._finding_counter = 1

    def _add_finding(self, severity: str, title: str, detail: str, affected_urls: list, recommendation: str):
        finding_id = f"sd-{self._finding_counter:03d}"
        self._finding_counter += 1
        self.findings.append({
            "finding_id": finding_id,
            "skill": "structured-data-audit",
            "severity": severity,
            "title": title,
            "detail": detail,
            "affected_urls": affected_urls,
            "recommendation": recommendation,
        })

    def _is_homepage(self, url: str) -> bool:
        parsed = urlparse(url)
        path = parsed.path.strip()
        return path == "" or path == "/" or path == "/index.html" or path == "/index.htm"

    def _is_utility_page(self, url: str) -> bool:
        """Check if URL is a utility page where social preview tags should be suppressed."""
        parsed = urlparse(url)
        path = parsed.path.lower().rstrip("/")
        query = parsed.query.lower()

        patterns = _CONFIG.get("opengraph", {}).get("suppress_utility_patterns", [
            r"^/login", r"^/signin", r"^/signup", r"^/register", r"^/auth", r"^/logout",
            r"^/privacy", r"^/terms", r"^/tos", r"^/legal", r"^/cookie", r"^/search",
            r"^/cart", r"^/checkout", r"^/account", r"^/settings", r"^/admin", r"^/dashboard"
        ])

        if any(qp in query for qp in ["q=", "query=", "search=", "s="]):
            return True

        for pat in patterns:
            if re.search(pat, path):
                return True
        return False

    def _flatten_entities(self, data) -> list:
        """Recursively collect all Schema.org entities with an @type."""
        entities = []
        if isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                for item in data["@graph"]:
                    entities.extend(self._flatten_entities(item))
            elif "@type" in data:
                entities.append(data)
                for k, v in data.items():
                    if k not in ("@type", "@context"):
                        entities.extend(self._flatten_entities(v))
            else:
                for k, v in data.items():
                    entities.extend(self._flatten_entities(v))
        elif isinstance(data, list):
            for item in data:
                entities.extend(self._flatten_entities(item))
        return entities

    def _validate_context(self, context_val) -> bool:
        """Validate if @context matches allowed Schema.org patterns."""
        if not context_val:
            return False

        allowed = _CONFIG.get("jsonld", {}).get("allowed_context_patterns", [
            r"https?://schema\.org/?",
            r"https?://www\.schema\.org/?"
        ])

        def _is_valid_str(s: str) -> bool:
            clean = s.strip()
            return any(re.search(p, clean, re.IGNORECASE) for p in allowed)

        if isinstance(context_val, str):
            return _is_valid_str(context_val)
        elif isinstance(context_val, list):
            for item in context_val:
                if isinstance(item, str) and _is_valid_str(item):
                    return True
                elif isinstance(item, dict):
                    vocab = item.get("@vocab", "")
                    if isinstance(vocab, str) and _is_valid_str(vocab):
                        return True
            return False
        elif isinstance(context_val, dict):
            vocab = context_val.get("@vocab", "")
            if isinstance(vocab, str) and _is_valid_str(vocab):
                return True
            for v in context_val.values():
                if isinstance(v, str) and _is_valid_str(v):
                    return True
            return False
        return False

    def validate_sd01_presence(self):
        """SD-01: JSON-LD Presence by Page Context."""
        # Check domain-wide structured data presence across all audited pages
        total_site_entities = 0
        all_audited_urls = [p.get("url", "") for p in self.pages if p.get("url")]

        for p in self.pages:
            for b in p.get("json_ld", {}).get("blocks", []):
                if b.get("data"):
                    total_site_entities += len(self._flatten_entities(b["data"]))

        # Flag domain-wide critical absence if no structured data exists anywhere on site
        if total_site_entities == 0 and self.pages:
            self._add_finding(
                severity="critical",
                title="Zero JSON-LD structured data detected across website",
                detail=(
                    f"None of the {len(self.pages)} audited pages on {self.site_url} contain valid "
                    "JSON-LD Schema.org markup. AI search engines, citation agents, and answer bots "
                    "cannot extract structured brand identity, commercial offers, or content metadata."
                ),
                affected_urls=all_audited_urls,
                recommendation=(
                    "Implement baseline JSON-LD schema starting with Organization on the homepage, "
                    "and page-specific schema (Product, Article, FAQPage) across interior pages."
                ),
            )

        for page in self.pages:
            url = page.get("url", "")
            json_ld_blocks = page.get("json_ld", {}).get("blocks", [])
            valid_blocks = [b for b in json_ld_blocks if b.get("data")]
            all_entities = []
            for b in valid_blocks:
                all_entities.extend(self._flatten_entities(b["data"]))

            type_names = set()
            for ent in all_entities:
                t = ent.get("@type")
                if isinstance(t, list):
                    type_names.update(t)
                elif isinstance(t, str):
                    type_names.add(t)

            is_home = self._is_homepage(url)
            lower_url = url.lower()
            visible = page.get("visible_cues", {})

            # Homepage identity checks
            if is_home:
                expected_home = set(_CONFIG.get("jsonld", {}).get("homepage_expected_types", ["Organization", "WebSite", "LocalBusiness", "Corporation"]))
                found_identity = any(t in type_names for t in expected_home)
                if not found_identity:
                    # Promoted to critical if the site has zero structured data anywhere
                    sev = "critical" if total_site_entities == 0 else "high"
                    self._add_finding(
                        severity=sev,
                        title="Missing Organization or WebSite schema on homepage",
                        detail=(
                            f"The homepage ({url}) lacks Organization, WebSite, or LocalBusiness JSON-LD schema. "
                            "AI assistants cannot reliably ground entity identity, official links, or logo."
                        ),
                        affected_urls=[url],
                        recommendation=(
                            "Add a JSON-LD block with @type Organization or WebSite, including name, url, logo, "
                            "and sameAs properties."
                        ),
                    )
                else:
                    matched = sorted([t for t in expected_home if t in type_names])
                    self._add_finding(
                        severity="info",
                        title="Valid Organization or WebSite schema detected on homepage",
                        detail=f"The homepage ({url}) declares valid identity schema ({', '.join(matched)}).",
                        affected_urls=[url],
                        recommendation="Keep Organization schema up to date with official social channels and contact details.",
                    )

            # Product / E-commerce checks
            commercial_url_terms = ["/product", "/item", "/shop", "/pricing", "/store", "/services"]
            is_commercial_url = any(term in lower_url for term in commercial_url_terms)
            has_commerce_cues = bool(visible.get("detected_prices") and any(w in visible.get("text_sample", "").lower() for w in ["cart", "buy", "price", "checkout"]))
            if is_commercial_url or has_commerce_cues:
                if not any(t in type_names for t in ["Product", "Offer", "SoftwareApplication", "Service"]):
                    self._add_finding(
                        severity="high",
                        title="Missing Product or Offer schema on commercial page",
                        detail=(
                            f"The page ({url}) appears to be a commercial product/pricing page but lacks "
                            "Product, Offer, SoftwareApplication, or Service structured markup."
                        ),
                        affected_urls=[url],
                        recommendation="Add Product and Offer JSON-LD schema with name, price, priceCurrency, and availability properties.",
                    )

            # Article / Editorial checks
            editorial_terms = ["/blog", "/article", "/news", "/post", "/press-release"]
            if any(term in lower_url for term in editorial_terms):
                if not any(t in type_names for t in ["Article", "BlogPosting", "NewsArticle"]):
                    self._add_finding(
                        severity="medium",
                        title="Missing Article schema on editorial page",
                        detail=f"The page ({url}) appears to be an editorial article or post but has no Article, BlogPosting, or NewsArticle schema markup.",
                        affected_urls=[url],
                        recommendation="Add Article or BlogPosting JSON-LD schema including headline, author, datePublished, and dateModified.",
                    )

            # FAQ / Support checks
            faq_terms = ["/faq", "/frequently-asked", "/help", "/support", "/questions", "/qa"]
            is_faq_url = any(term in lower_url for term in faq_terms)
            has_faq_heading = visible.get("has_faq_content", False) or any(
                re.search(r"\b(faq|frequently\s+asked|questions?\s*(&|and)?\s*answers?)\b", h, re.IGNORECASE)
                for h in (visible.get("h1", []) + visible.get("h2_sample", []))
            )
            if is_faq_url or has_faq_heading:
                if "FAQPage" not in type_names:
                    self._add_finding(
                        severity="medium",
                        title="Missing FAQPage schema on FAQ/support page",
                        detail=(
                            f"The page ({url}) contains FAQ or support content but lacks FAQPage structured markup. "
                            "AI assistants cannot directly ingest structured question-and-answer pairs for zero-click answer synthesis."
                        ),
                        affected_urls=[url],
                        recommendation=(
                            "Add FAQPage JSON-LD schema with mainEntity array containing Question and acceptedAnswer entities."
                        ),
                    )
                else:
                    self._add_finding(
                        severity="info",
                        title="Rich FAQPage schema present",
                        detail=f"The page ({url}) declares FAQPage structured data for AI question answering.",
                        affected_urls=[url],
                        recommendation="Ensure all visible Q&A pairs are synchronized with the FAQPage JSON-LD block.",
                    )

            # Breadcrumb check for interior pages
            if not is_home:
                if "BreadcrumbList" not in type_names:
                    self._add_finding(
                        severity="low",
                        title="Missing BreadcrumbList schema on interior page",
                        detail=f"Interior page ({url}) does not declare BreadcrumbList schema, missing an opportunity to convey site hierarchy to AI crawler graphs.",
                        affected_urls=[url],
                        recommendation="Include BreadcrumbList JSON-LD markup declaring parent-child site navigation structure.",
                    )

    def validate_sd02_validity(self):
        """SD-02: JSON-LD Syntactic & Semantic Validity."""
        for page in self.pages:
            url = page.get("url", "")
            json_ld_blocks = page.get("json_ld", {}).get("blocks", [])

            # Check JSON parse errors
            for b in json_ld_blocks:
                if b.get("parse_error"):
                    err = b["parse_error"]
                    self._add_finding(
                        severity="high",
                        title="Malformed JSON in JSON-LD script block",
                        detail=f"JSON-LD block #{b.get('index', 0)} on {url} failed to parse: {err.get('message')} at line {err.get('line')}, col {err.get('col')}.",
                        affected_urls=[url],
                        recommendation="Fix the JSON syntax inside the <script type=\"application/ld+json\"> tag. Remove trailing commas or unescaped characters.",
                    )

            valid_blocks = [b for b in json_ld_blocks if b.get("data")]

            # Validate @context declaration and Schema.org compliance (Anti-Pattern AP-01)
            for b in valid_blocks:
                b_data = b.get("data")
                block_idx = b.get("index", 0)

                context_val = None
                has_context = False

                if isinstance(b_data, dict):
                    if "@context" in b_data:
                        has_context = True
                        context_val = b_data["@context"]
                    elif "@graph" in b_data and isinstance(b_data["@graph"], list):
                        for g_item in b_data["@graph"]:
                            if isinstance(g_item, dict) and "@context" in g_item:
                                has_context = True
                                context_val = g_item["@context"]
                                break
                elif isinstance(b_data, list):
                    for l_item in b_data:
                        if isinstance(l_item, dict) and "@context" in l_item:
                            has_context = True
                            context_val = l_item["@context"]
                            break

                if not has_context:
                    self._add_finding(
                        severity="high",
                        title="Missing @context in JSON-LD script block",
                        detail=(
                            f"JSON-LD block #{block_idx} on {url} lacks the required '@context': 'https://schema.org' "
                            "declaration (Anti-Pattern AP-01). Machines cannot disambiguate entity vocabulary."
                        ),
                        affected_urls=[url],
                        recommendation="Add '@context': 'https://schema.org' to the root of the JSON-LD object.",
                    )
                elif not self._validate_context(context_val):
                    self._add_finding(
                        severity="high",
                        title="Invalid or non-Schema.org @context in JSON-LD",
                        detail=(
                            f"JSON-LD block #{block_idx} on {url} declares @context '{context_val}', which does not "
                            "point to Schema.org (https://schema.org). AI search engines and crawler agents may ignore this markup."
                        ),
                        affected_urls=[url],
                        recommendation="Ensure '@context' points to 'https://schema.org'.",
                    )

            all_entities = []
            for b in valid_blocks:
                all_entities.extend(self._flatten_entities(b["data"]))

            for ent in all_entities:
                t = ent.get("@type")
                types_to_check = t if isinstance(t, list) else [t] if t else []

                if not types_to_check:
                    self._add_finding(
                        severity="high",
                        title="Schema entity missing @type",
                        detail=f"A JSON-LD entity on {url} lacks the required '@type' property: {json.dumps(ent)[:150]}.",
                        affected_urls=[url],
                        recommendation="Specify a valid Schema.org @type for all structured data entities.",
                    )
                    continue

                for type_name in types_to_check:
                    if type_name in _SCHEMA_TYPES:
                        spec = _SCHEMA_TYPES[type_name]
                        # Check required properties
                        missing_req = [p for p in spec.get("required_properties", []) if p not in ent or not ent[p]]
                        if missing_req:
                            self._add_finding(
                                severity="high",
                                title=f"Missing required properties in {type_name} schema",
                                detail=f"{type_name} schema on {url} is missing required properties: {', '.join(missing_req)}.",
                                affected_urls=[url],
                                recommendation=f"Add missing properties ({', '.join(missing_req)}) to the {type_name} markup.",
                            )

                        # Check recommended properties
                        missing_rec = [p for p in spec.get("recommended_properties", []) if p not in ent or not ent[p]]
                        if missing_rec and len(missing_rec) >= 2:
                            self._add_finding(
                                severity="medium",
                                title=f"Recommended properties missing in {type_name} schema",
                                detail=f"{type_name} schema on {url} is missing recommended properties for AI citation: {', '.join(missing_rec[:4])}.",
                                affected_urls=[url],
                                recommendation=f"Consider enhancing {type_name} with recommended properties: {', '.join(missing_rec[:4])}.",
                            )

                # Check placeholder anti-patterns in property strings
                for k, v in ent.items():
                    if isinstance(v, str) and _PLACEHOLDER_REGEX.search(v):
                        self._add_finding(
                            severity="high",
                            title=f"Placeholder dummy data detected in schema ({k})",
                            detail=f"Field '{k}' on {url} contains dummy or placeholder text: '{v[:100]}'.",
                            affected_urls=[url],
                            recommendation="Replace template or placeholder values with actual production brand data.",
                        )

    def validate_sd03_opengraph(self):
        """SD-03: Open Graph Protocol Tags."""
        req_og = _CONFIG.get("opengraph", {}).get("required_tags", ["og:title", "og:description", "og:image", "og:url", "og:type"])
        for page in self.pages:
            url = page.get("url", "")
            is_utility = self._is_utility_page(url)
            og = page.get("open_graph", {})

            # Suppress missing social preview tags on utility pages (false-positive suppression)
            if not is_utility:
                missing = [t for t in req_og if t not in og or not og[t]]
                if missing:
                    if len(missing) == len(req_og):
                        self._add_finding(
                            severity="medium",
                            title="Missing all essential Open Graph meta tags",
                            detail=f"The page ({url}) does not define any Open Graph tags. AI social and citation parsers cannot construct rich previews.",
                            affected_urls=[url],
                            recommendation=f"Add Open Graph meta tags to <head>: {', '.join(req_og)}.",
                        )
                    else:
                        self._add_finding(
                            severity="medium",
                            title=f"Incomplete Open Graph tags: missing {', '.join(missing)}",
                            detail=f"The page ({url}) is missing key Open Graph properties: {', '.join(missing)}.",
                            affected_urls=[url],
                            recommendation=f"Add the missing Open Graph tags: {', '.join(missing)}.",
                        )

            # Validate og:image URL if present (regardless of page type)
            if "og:image" in og and og["og:image"]:
                img_val = og["og:image"]
                img_url = img_val[0] if isinstance(img_val, list) else img_val
                if not img_url.startswith(("http://", "https://")):
                    self._add_finding(
                        severity="low",
                        title="Open Graph image is not an absolute URL",
                        detail=f"The og:image tag on {url} specifies a relative path '{img_url}', which breaks across external AI preview cards.",
                        affected_urls=[url],
                        recommendation="Specify a fully-qualified absolute HTTPS URL for og:image.",
                    )

    def validate_sd04_twitter(self):
        """SD-04: Twitter / X Card Tags."""
        req_tw = _CONFIG.get("twitter", {}).get("required_tags", ["twitter:card", "twitter:title", "twitter:description"])
        for page in self.pages:
            url = page.get("url", "")
            if self._is_utility_page(url):
                continue
            tw = page.get("twitter_card", {})

            missing = [t for t in req_tw if t not in tw or not tw[t]]
            if missing:
                self._add_finding(
                    severity="low",
                    title=f"Missing Twitter Card tags ({', '.join(missing)})",
                    detail=f"The page ({url}) lacks Twitter Card directives: {', '.join(missing)}.",
                    affected_urls=[url],
                    recommendation="Add twitter:card (summary_large_image), twitter:title, and twitter:description.",
                )

    def validate_sd05_title_meta(self):
        """SD-05: Title, Meta Description & Favicon."""
        t_min = _CONFIG.get("meta", {}).get("title_min_length", 10)
        t_max = _CONFIG.get("meta", {}).get("title_max_length", 65)
        d_min = _CONFIG.get("meta", {}).get("description_min_length", 50)
        d_max = _CONFIG.get("meta", {}).get("description_max_length", 165)
        generic_patterns = [re.compile(p, re.IGNORECASE) for p in _CONFIG.get("meta", {}).get("generic_title_patterns", [])]

        for page in self.pages:
            url = page.get("url", "")
            meta = page.get("meta", {})
            title = meta.get("title", "").strip()
            desc = meta.get("description", "").strip()
            favicons = meta.get("favicons", [])

            # Title validation
            if not title:
                self._add_finding(
                    severity="high",
                    title="Missing <title> tag",
                    detail=f"The page ({url}) has no <title> tag, preventing AI search engines and citation bots from determining page context.",
                    affected_urls=[url],
                    recommendation="Add a descriptive <title> tag (10-60 characters) summarizing page intent and brand.",
                )
            else:
                if any(p.match(title) for p in generic_patterns):
                    self._add_finding(
                        severity="medium",
                        title=f"Generic boilerplate title tag ('{title}')",
                        detail=f"The page ({url}) uses a generic title '{title}' with no distinguishing brand or topic identifier.",
                        affected_urls=[url],
                        recommendation="Use a specific title format: [Page Topic / Action] — [Brand Name].",
                    )
                elif len(title) < t_min:
                    self._add_finding(
                        severity="low",
                        title=f"Title tag is unusually short ({len(title)} chars)",
                        detail=f"The title '{title}' on {url} is only {len(title)} characters; it may lack sufficient context for AI answer grounding.",
                        affected_urls=[url],
                        recommendation="Expand the title tag to 10–60 characters with relevant brand context.",
                    )
                elif len(title) > t_max:
                    self._add_finding(
                        severity="low",
                        title=f"Title tag exceeds recommended length ({len(title)} chars)",
                        detail=f"The title on {url} is {len(title)} characters and may be truncated in search snippets.",
                        affected_urls=[url],
                        recommendation="Keep title tags under 65 characters to prevent truncation.",
                    )

            # Meta Description validation
            if not desc:
                self._add_finding(
                    severity="medium",
                    title="Missing meta description tag",
                    detail=f"The page ({url}) lacks a <meta name=\"description\"> tag.",
                    affected_urls=[url],
                    recommendation="Add a concise meta description (50–160 characters) summarizing the page.",
                )
            elif len(desc) < d_min:
                self._add_finding(
                    severity="low",
                    title="Meta description is too short",
                    detail=f"Meta description on {url} is only {len(desc)} characters (recommended: {d_min}–{d_max} characters).",
                    affected_urls=[url],
                    recommendation=f"Expand meta description to at least {d_min} characters of substantive summary.",
                )
            elif len(desc) > d_max:
                self._add_finding(
                    severity="low",
                    title="Meta description exceeds recommended length",
                    detail=f"Meta description on {url} is {len(desc)} characters (recommended: {d_min}–{d_max} characters) and may be truncated in search snippets.",
                    affected_urls=[url],
                    recommendation=f"Keep meta descriptions under {d_max} characters to avoid truncation.",
                )

            # Favicon
            if not favicons:
                self._add_finding(
                    severity="low",
                    title="Missing favicon link declaration",
                    detail=f"The page ({url}) does not declare a <link rel=\"icon\"> or apple-touch-icon tag.",
                    affected_urls=[url],
                    recommendation="Add <link rel=\"icon\" href=\"/favicon.ico\"> to the <head>.",
                )

    def validate_sd06_llms_txt(self):
        """SD-06: llms.txt / Machine-facing summary at site root."""
        root_llms = self.llms_txt_data.get("/llms.txt", {})
        if not root_llms.get("present"):
            self._add_finding(
                severity="low",
                title="Missing /llms.txt machine-facing summary file",
                detail=f"No /llms.txt file was found at {self.site_url}/llms.txt. Emerging AI web agents use llms.txt to quickly parse site purpose, structure, and documentation.",
                affected_urls=[f"{self.site_url.rstrip('/')}/llms.txt"],
                recommendation="Create an /llms.txt markdown file at the domain root with an H1 brand title, a blockquote summary, and links to core docs.",
            )
        else:
            length = root_llms.get("length_chars", 0)
            if length < 50:
                self._add_finding(
                    severity="low",
                    title="/llms.txt file is too brief or empty",
                    detail=f"An /llms.txt file was found at {self.site_url}/llms.txt, but it only contains {length} characters.",
                    affected_urls=[f"{self.site_url.rstrip('/')}/llms.txt"],
                    recommendation="Expand /llms.txt with a project summary, key product offerings, and curated links.",
                )

    def validate_cross_references(self):
        """SD-07: Cross-reference structured data properties against visible page content."""
        def _extract_numbers(val):
            return re.findall(r"\d+(?:\.\d{2})?", str(val).replace(",", ""))

        for page in self.pages:
            url = page.get("url", "")
            visible = page.get("visible_cues", {})
            h1s = visible.get("h1", [])
            body_text = visible.get("text_sample", "").lower()
            detected_prices = visible.get("detected_prices", [])
            detected_dates = visible.get("detected_dates", [])
            meta_title = page.get("meta", {}).get("title", "")

            json_ld_blocks = [b for b in page.get("json_ld", {}).get("blocks", []) if b.get("data")]
            all_entities = []
            for b in json_ld_blocks:
                all_entities.extend(self._flatten_entities(b["data"]))

            # 1. Name / Headline vs. visible H1 / Title cross-reference (AP-05)
            for ent in all_entities:
                t = ent.get("@type")
                name = ent.get("name") or ent.get("headline")
                if t in ("Product", "Organization", "Article", "SoftwareApplication", "Service") and isinstance(name, str) and (h1s or meta_title):
                    name_words = set(re.findall(r"\w+", name.lower()))
                    target_text = " ".join(h1s).lower() if h1s else meta_title.lower()
                    target_words = set(re.findall(r"\w+", target_text))

                    if len(name_words) > 1 and target_words:
                        overlap = len(name_words.intersection(target_words)) / len(name_words)
                        if overlap < 0.35:
                            display_target = h1s[0] if h1s else meta_title
                            self._add_finding(
                                severity="high",
                                title=f"Content discrepancy between Schema.org {t} name and visible DOM",
                                detail=(
                                    f"Schema {t} declares name/headline '{name}', but visible heading displays '{display_target}'. "
                                    f"Low token match ({int(overlap * 100)}%) creates conflicting entity signals for AI models (Anti-Pattern AP-05)."
                                ),
                                affected_urls=[url],
                                recommendation=f"Align Schema.org {t} name with the primary visible heading on the page.",
                            )

            # 2. Pricing Discrepancies between Schema Offer and DOM text (AP-05)
            visible_price_numbers = set()
            for dp in detected_prices:
                for num in _extract_numbers(dp):
                    try:
                        visible_price_numbers.add(float(num))
                    except ValueError:
                        pass

            checked_price_values = set()
            for ent in all_entities:
                t = ent.get("@type")
                if t == "Offer" or (t == "Product" and isinstance(ent.get("offers"), dict) and ent.get("offers").get("@type") != "Offer"):
                    offer_ent = ent if t == "Offer" else ent.get("offers")
                    if isinstance(offer_ent, dict):
                        raw_price = offer_ent.get("price")
                        currency = offer_ent.get("priceCurrency", "")
                        if raw_price is not None and str(raw_price) not in checked_price_values:
                            checked_price_values.add(str(raw_price))
                            schema_nums = _extract_numbers(raw_price)
                            if schema_nums:
                                try:
                                    s_price = float(schema_nums[0])
                                    # If visible prices exist on page and none match schema price
                                    if visible_price_numbers and not any(abs(s_price - vp) < 0.01 for vp in visible_price_numbers):
                                        # Also verify if numeric representation appears anywhere in sample text
                                        str_num = f"{s_price:.2f}"
                                        str_int = f"{int(s_price)}"
                                        if str_num not in body_text and str_int not in body_text:
                                            self._add_finding(
                                                severity="high",
                                                title="Pricing discrepancy between Schema Offer and visible DOM text",
                                                detail=(
                                                    f"Schema Offer declares price '{raw_price}' {currency}, but visible page prices are "
                                                    f"{', '.join(detected_prices[:4])}. Conflicting prices damage AI confidence and cause inaccurate citations."
                                                ),
                                                affected_urls=[url],
                                                recommendation="Ensure Schema.org Offer price exactly matches the visible price displayed to visitors.",
                                            )
                                except ValueError:
                                    pass

            # 3. Publication Date Conflicts
            for ent in all_entities:
                t = ent.get("@type")
                if t in ("Article", "BlogPosting", "NewsArticle"):
                    date_pub = ent.get("datePublished")
                    if date_pub and isinstance(date_pub, str) and detected_dates:
                        schema_year_match = re.search(r"\b(19\d\d|20\d\d)\b", date_pub)
                        if schema_year_match:
                            schema_year = schema_year_match.group(1)
                            visible_years = re.findall(r"\b(19\d\d|20\d\d)\b", " ".join(detected_dates))
                            if visible_years and schema_year not in visible_years:
                                self._add_finding(
                                    severity="medium",
                                    title="Publication date conflict between Schema and visible page text",
                                    detail=(
                                        f"Schema {t} specifies datePublished '{date_pub}' (year {schema_year}), but visible date text "
                                        f"references {', '.join(detected_dates)}. AI engines rely on synchronized timestamps for freshness scoring."
                                    ),
                                    affected_urls=[url],
                                    recommendation="Synchronize Schema datePublished and dateModified with visible article timestamps.",
                                )

    def run_all(self) -> dict:
        self.validate_sd01_presence()
        self.validate_sd02_validity()
        self.validate_sd03_opengraph()
        self.validate_sd04_twitter()
        self.validate_sd05_title_meta()
        self.validate_sd06_llms_txt()
        self.validate_cross_references()

        # Sort findings by priority: critical -> high -> medium -> low -> info
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        self.findings.sort(key=lambda x: priority_order.get(x.get("severity", "info"), 99))

        # Re-number finding IDs sequentially in priority order (sd-001, sd-002, ...)
        for idx, f in enumerate(self.findings, 1):
            f["finding_id"] = f"sd-{idx:03d}"

        # Severity summary
        by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in self.findings:
            sev = f.get("severity", "info")
            if sev in by_severity:
                by_severity[sev] += 1

        return {
            "site": self.site_url,
            "skill": "structured-data-audit",
            "total_findings": len(self.findings),
            "by_severity": by_severity,
            "findings": self.findings,
        }


def main():
    parser = argparse.ArgumentParser(description="Validate structured data extraction results.")
    parser.add_argument("--raw-data", help="Path to JSON output from structured_data_extractor.py")
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
        import subprocess
        extractor_script = os.path.join(_SCRIPTS_DIR, "structured_data_extractor.py")
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

    validator = SchemaValidator(raw_data)
    report = validator.run_all()

    output_str = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_str)
    else:
        print(output_str)


if __name__ == "__main__":
    main()
