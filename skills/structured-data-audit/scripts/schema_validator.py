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
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
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
_LIB_DIR = os.path.normpath(os.path.join(_SCRIPTS_DIR, "..", "..", "..", "lib"))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from report import make_finding  # noqa: E402

SKILL = "structured-data-audit"


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

    def _add_finding(self, severity, title, detail, affected_urls, recommendation,
                     *, check_id=None, source="html", locator="", expected=None):
        """Append one contract-shaped finding. Keeps the original keyword interface;
        `detail` becomes evidence.observed and `recommendation` the action summary."""
        affected_urls = affected_urls or []
        url = affected_urls[0] if affected_urls else (self.site_url or "")
        observed = detail
        if len(affected_urls) > 1:
            shown = ", ".join(affected_urls[:6])
            observed = f"{detail} (affected: {shown}{' …' if len(affected_urls) > 6 else ''})"
        self.findings.append(make_finding(
            skill=SKILL, check_id=check_id or title, severity=severity, title=title,
            action_summary=recommendation, evidence_url=url, evidence_source=source,
            evidence_observed=observed, evidence_locator=locator, evidence_expected=expected))

    def _content_pages(self):
        """Only successfully-fetched HTML pages are eligible for content checks; a failed
        or non-HTML fetch must not produce phantom 'missing X' findings."""
        out = []
        for p in self.pages:
            if p.get("status_code") != 200:
                continue
            ctype = (p.get("content_type") or "").lower()
            if ctype and "html" not in ctype:
                continue
            out.append(p)
        return out

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

    @staticmethod
    def _entity_types(entity: dict) -> list:
        value = entity.get("@type")
        return value if isinstance(value, list) else ([value] if isinstance(value, str) else [])

    @staticmethod
    def _is_minimal_organization(entity: dict) -> bool:
        """Return true for lightweight nested publisher/brand references.

        A name/@id-only Organization is a valid nested reference and should not be
        graded as though it were the site's complete primary identity declaration.
        """
        meaningful = {k for k, v in entity.items() if k not in ("@type", "@context") and v not in (None, "", [], {})}
        return meaningful.issubset({"@id", "name", "url"}) and "sameAs" not in entity

    @staticmethod
    def _parse_decimal(value):
        try:
            parsed = Decimal(str(value).replace(",", "").strip())
            return parsed if parsed.is_finite() else None
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def _parse_iso_date(value):
        if not isinstance(value, str) or not value.strip():
            return None
        clean = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(clean).date()
        except ValueError:
            try:
                return date.fromisoformat(clean[:10])
            except ValueError:
                return None

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
        pages = self._content_pages()
        total_site_entities = 0
        all_audited_urls = [p.get("url", "") for p in pages if p.get("url")]

        for p in pages:
            for b in p.get("json_ld", {}).get("blocks", []):
                if b.get("data"):
                    total_site_entities += len(self._flatten_entities(b["data"]))

        # Flag domain-wide critical absence if no structured data exists anywhere on site
        if total_site_entities == 0 and pages:
            self._add_finding(
                check_id="SD-01-zero",
                severity="critical",
                title="Zero JSON-LD structured data detected across website",
                detail=(
                    f"None of the {len(pages)} successfully-fetched pages on {self.site_url} contain "
                    "valid JSON-LD Schema.org markup. AI search engines, citation agents, and answer "
                    "bots cannot extract structured brand identity, commercial offers, or content metadata."
                ),
                affected_urls=all_audited_urls,
                locator="script[type=application/ld+json]",
                recommendation=(
                    "Implement baseline JSON-LD schema starting with Organization on the homepage, "
                    "and page-specific schema (Product, Article, FAQPage) across interior pages."
                ),
            )

        missing_breadcrumb = []

        for page in pages:
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
                expected_home = set(_CONFIG.get("jsonld", {}).get("homepage_expected_types", ["Organization", "WebSite", "LocalBusiness", "Corporation", "NewsMediaOrganization"]))
                specific_identity_types = {"NewsMediaOrganization", "Corporation", "LocalBusiness"}
                has_specific_identity = any(
                    set(self._entity_types(ent)).intersection(specific_identity_types)
                    for ent in all_entities
                )
                matched_identity_types = set()
                for ent in all_entities:
                    ent_types = set(self._entity_types(ent)).intersection(expected_home)
                    if "Organization" in ent_types and has_specific_identity and self._is_minimal_organization(ent):
                        ent_types.remove("Organization")
                    matched_identity_types.update(ent_types)
                found_identity = bool(matched_identity_types)
                if not found_identity:
                    # Promoted to critical if the site has zero structured data anywhere
                    sev = "critical" if total_site_entities == 0 else "high"
                    self._add_finding(
                        check_id="SD-01-home-identity",
                        severity=sev,
                        title="Missing organization identity or WebSite schema on homepage",
                        detail=(
                            f"The homepage ({url}) lacks Organization, NewsMediaOrganization, WebSite, or LocalBusiness JSON-LD schema. "
                            "AI assistants cannot reliably ground entity identity, official links, or logo."
                        ),
                        affected_urls=[url],
                        locator="script[type=application/ld+json] @type",
                        expected="Organization | NewsMediaOrganization | WebSite | LocalBusiness",
                        recommendation=(
                            "Add a JSON-LD block with an appropriate Organization subtype or WebSite, including name, url, logo, "
                            "and sameAs properties."
                        ),
                    )
                else:
                    matched = sorted(matched_identity_types)
                    self._add_finding(
                        check_id="SD-01-home-identity",
                        severity="info",
                        title="Valid organization identity or WebSite schema detected on homepage",
                        detail=f"The homepage ({url}) declares valid identity schema ({', '.join(matched)}).",
                        affected_urls=[url],
                        locator="script[type=application/ld+json] @type",
                        recommendation="Keep Organization schema up to date with official social channels and contact details.",
                    )

            # Product / E-commerce checks. NOTE: "/services" is deliberately NOT here —
            # a consulting/services page is not e-commerce, and demanding Product/Offer
            # schema on it (at HIGH) was a false positive. Commercial intent is confirmed
            # by an explicit shop/pricing path OR real on-page commerce cues.
            commercial_url_terms = ["/product", "/item", "/shop", "/pricing", "/store"]
            is_commercial_url = any(term in lower_url for term in commercial_url_terms)
            has_commerce_cues = bool(visible.get("detected_prices") and any(w in visible.get("text_sample", "").lower() for w in ["cart", "buy", "price", "checkout"]))
            if is_commercial_url or has_commerce_cues:
                if not any(t in type_names for t in ["Product", "Offer", "SoftwareApplication", "Service"]):
                    # HIGH only when the page shows real commerce cues (prices + cart/buy);
                    # a bare /product(s) overview with no prices is a weaker signal -> medium.
                    self._add_finding(
                        check_id="SD-01-product",
                        severity="high" if has_commerce_cues else "medium",
                        title="Missing Product or Offer schema on commercial page",
                        detail=(
                            f"The page ({url}) appears to be a commercial product/pricing page but lacks "
                            "Product, Offer, SoftwareApplication, or Service structured markup."
                        ),
                        affected_urls=[url],
                        locator="script[type=application/ld+json] @type",
                        expected="Product | Offer | SoftwareApplication | Service",
                        recommendation="Add Product and Offer JSON-LD schema with name, price, priceCurrency, and availability properties.",
                    )

            # Article / Editorial checks
            editorial_terms = ["/blog", "/article", "/news", "/post", "/press-release"]
            if any(term in lower_url for term in editorial_terms):
                if not any(t in type_names for t in ["Article", "BlogPosting", "NewsArticle"]):
                    self._add_finding(
                        check_id="SD-01-article",
                        severity="medium",
                        title="Missing Article schema on editorial page",
                        detail=f"The page ({url}) appears to be an editorial article or post but has no Article, BlogPosting, or NewsArticle schema markup.",
                        affected_urls=[url],
                        locator="script[type=application/ld+json] @type",
                        recommendation="Add Article or BlogPosting JSON-LD schema including headline, author, datePublished, and dateModified.",
                    )

            # FAQ / Support checks
            faq_terms = ["/faq", "/frequently-asked", "/help", "/support", "/questions", "/qa"]
            is_faq_url = any(term in lower_url for term in faq_terms)
            has_faq_heading = visible.get("has_faq_content", False) or any(
                re.search(r"\b(faq|frequently\s+asked|questions?\s*(&|and)?\s*answers?)\b", h, re.IGNORECASE)
                for h in (visible.get("h1", []) + visible.get("h2_sample", []))
            )
            # Require an explicit FAQ signal in the URL or a real FAQ heading — a page with
            # two <details> elements alone is not enough to demand FAQPage markup.
            if is_faq_url or has_faq_heading:
                if "FAQPage" not in type_names:
                    self._add_finding(
                        check_id="SD-01-faq",
                        severity="medium",
                        title="Missing FAQPage schema on FAQ/support page",
                        detail=(
                            f"The page ({url}) contains FAQ or support content but lacks FAQPage structured markup. "
                            "AI assistants cannot directly ingest structured question-and-answer pairs for zero-click answer synthesis."
                        ),
                        affected_urls=[url],
                        locator="script[type=application/ld+json] @type",
                        recommendation=(
                            "Add FAQPage JSON-LD schema with mainEntity array containing Question and acceptedAnswer entities."
                        ),
                    )

            # Breadcrumb: collect interior pages missing it; report once (not per page).
            if not is_home and "BreadcrumbList" not in type_names:
                missing_breadcrumb.append(url)

        if missing_breadcrumb:
            self._add_finding(
                check_id="SD-01-breadcrumb",
                severity="low",
                title=f"BreadcrumbList schema missing on {len(missing_breadcrumb)} interior page(s)",
                detail=(
                    "Interior pages do not declare BreadcrumbList schema, missing an opportunity to "
                    "convey site hierarchy to AI crawler graphs."
                ),
                affected_urls=missing_breadcrumb,
                locator="script[type=application/ld+json] @type=BreadcrumbList",
                recommendation="Include BreadcrumbList JSON-LD markup declaring parent-child navigation on interior pages.",
            )


    def validate_sd02_validity(self):
        """SD-02: JSON-LD Syntactic & Semantic Validity."""
        for page in self._content_pages():
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

            has_specific_org = any(
                set(self._entity_types(ent)).intersection({"NewsMediaOrganization", "Corporation", "LocalBusiness"})
                for ent in all_entities
            )

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
                    # A lightweight nested Organization (commonly NewsArticle.publisher)
                    # is not a second, incomplete primary org when a more specific
                    # organization is declared on the page.
                    if type_name == "Organization" and has_specific_org and self._is_minimal_organization(ent):
                        continue
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

                        # Check one-of required properties used by types such as
                        # SpeakableSpecification and AggregateRating.
                        required_any = spec.get("required_any_properties", [])
                        if required_any and not any(ent.get(p) for p in required_any):
                            self._add_finding(
                                check_id=f"SD-02-{type_name.lower()}-required-any",
                                severity="high",
                                title=f"Missing alternative required property in {type_name} schema",
                                detail=f"{type_name} schema on {url} requires at least one of: {', '.join(required_any)}.",
                                affected_urls=[url],
                                recommendation=f"Add one of ({', '.join(required_any)}) to the {type_name} markup.",
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

            self._validate_news_and_commerce(url, page, all_entities)

    def _validate_news_and_commerce(self, url: str, page: dict, entities: list):
        """Deterministic semantic validation beyond registry presence checks."""
        valid_availability = set(_CONFIG.get("commerce", {}).get("valid_availability_values", []))
        today = date.today()

        for ent in entities:
            types = self._entity_types(ent)

            if "NewsArticle" in types:
                required = ("headline", "datePublished", "author", "publisher")
                date_pub = self._parse_iso_date(ent.get("datePublished"))
                date_mod = self._parse_iso_date(ent.get("dateModified")) if ent.get("dateModified") else None
                author_ok = isinstance(ent.get("author"), str) or (
                    isinstance(ent.get("author"), dict) and bool(ent["author"].get("name") or ent["author"].get("@id"))
                ) or (isinstance(ent.get("author"), list) and bool(ent.get("author")))
                publisher = ent.get("publisher")
                publisher_ok = isinstance(publisher, dict) and bool(publisher.get("name") or publisher.get("@id"))
                errors = []
                if ent.get("datePublished") and not date_pub:
                    errors.append("datePublished is not a valid ISO-8601 date")
                if ent.get("dateModified") and not date_mod:
                    errors.append("dateModified is not a valid ISO-8601 date")
                if date_pub and date_mod and date_mod < date_pub:
                    errors.append("dateModified precedes datePublished")
                if ent.get("author") and not author_ok:
                    errors.append("author lacks a usable name or @id")
                if publisher and not publisher_ok:
                    errors.append("publisher lacks a usable name or @id")
                if errors:
                    self._add_finding(
                        check_id="SD-02-newsarticle-semantic",
                        severity="high",
                        title="Invalid NewsArticle publication metadata",
                        detail=f"NewsArticle on {url}: {'; '.join(errors)}.",
                        affected_urls=[url],
                        locator="NewsArticle",
                        recommendation="Use ISO-8601 publication dates and identify author and publisher with names or stable @id values.",
                    )
                elif all(ent.get(p) for p in required) and date_pub and author_ok and publisher_ok:
                    self._add_finding(
                        check_id="SD-02-newsarticle-valid",
                        severity="info",
                        title="Valid NewsArticle publication metadata detected",
                        detail="NewsArticle has a headline, valid publication date, identified author, and identified publisher.",
                        affected_urls=[url],
                        locator="NewsArticle",
                        recommendation="Keep dateModified synchronized with substantive editorial updates.",
                    )

            if "SpeakableSpecification" in types:
                selectors = []
                for prop in ("cssSelector", "xpath"):
                    value = ent.get(prop)
                    selectors.extend(value if isinstance(value, list) else ([value] if isinstance(value, str) else []))
                selectors = [s.strip() for s in selectors if isinstance(s, str) and s.strip()]
                if selectors:
                    self._add_finding(
                        check_id="SD-02-speakable-valid",
                        severity="info",
                        title="Valid SpeakableSpecification selectors detected",
                        detail=f"SpeakableSpecification provides {len(selectors)} non-empty CSS/XPath selector(s).",
                        affected_urls=[url],
                        locator="SpeakableSpecification.cssSelector|xpath",
                        recommendation="Keep selectors limited to concise text that remains visible on the page.",
                    )
                elif ent.get("cssSelector") or ent.get("xpath"):
                    self._add_finding(
                        check_id="SD-02-speakable-invalid",
                        severity="high",
                        title="Invalid SpeakableSpecification selector values",
                        detail="Speakable selectors must be a non-empty string or list of non-empty strings.",
                        affected_urls=[url],
                        locator="SpeakableSpecification.cssSelector|xpath",
                        recommendation="Provide one or more non-empty cssSelector or xpath strings.",
                    )

            if "Product" in types:
                offers = ent.get("offers")
                offer_items = offers if isinstance(offers, list) else ([offers] if isinstance(offers, dict) else [])
                if offer_items and not any("Offer" in self._entity_types(o) or o.get("price") is not None for o in offer_items):
                    self._add_finding(
                        check_id="SD-02-product-offers",
                        severity="high",
                        title="Product offers do not contain a usable Offer",
                        detail=f"Product '{ent.get('name', '')}' has offers markup without an Offer type or price.",
                        affected_urls=[url],
                        locator="Product.offers",
                        recommendation="Nest a valid Offer with price, priceCurrency, availability, and URL under Product.offers.",
                    )

            if "Offer" in types:
                errors = []
                price = self._parse_decimal(ent.get("price"))
                if ent.get("price") is not None and (price is None or price < 0):
                    errors.append("price must be a non-negative number")
                currency = ent.get("priceCurrency")
                if currency and not re.fullmatch(r"[A-Z]{3}", str(currency)):
                    errors.append("priceCurrency must be an uppercase three-letter ISO 4217 code")
                availability = str(ent.get("availability", "")).rstrip("/").split("/")[-1]
                if availability and availability not in valid_availability:
                    errors.append(f"availability '{availability}' is not a recognized ItemAvailability value")
                valid_until = self._parse_iso_date(ent.get("priceValidUntil")) if ent.get("priceValidUntil") else None
                if ent.get("priceValidUntil") and not valid_until:
                    errors.append("priceValidUntil is not a valid ISO-8601 date")
                elif valid_until and valid_until < today:
                    errors.append(f"priceValidUntil expired on {valid_until.isoformat()}")
                if errors:
                    self._add_finding(
                        check_id="SD-02-offer-semantic",
                        severity="high",
                        title="Invalid or stale Offer pricing metadata",
                        detail=f"Offer on {url}: {'; '.join(errors)}.",
                        affected_urls=[url],
                        locator="Offer",
                        recommendation="Publish a numeric current price, ISO currency, valid ItemAvailability URL/value, and a non-expired priceValidUntil.",
                    )

            if "AggregateRating" in types:
                errors = []
                rating = self._parse_decimal(ent.get("ratingValue"))
                best = self._parse_decimal(ent.get("bestRating", 5))
                worst = self._parse_decimal(ent.get("worstRating", 1))
                count_raw = ent.get("ratingCount", ent.get("reviewCount"))
                count = self._parse_decimal(count_raw)
                if ent.get("ratingValue") is not None and rating is None:
                    errors.append("ratingValue must be numeric")
                if ent.get("bestRating") is not None and best is None:
                    errors.append("bestRating must be numeric")
                if ent.get("worstRating") is not None and worst is None:
                    errors.append("worstRating must be numeric")
                if count_raw is not None and count is None:
                    errors.append("ratingCount/reviewCount must be numeric")
                if rating is not None and best is not None and worst is not None and not (worst <= rating <= best):
                    errors.append(f"ratingValue {rating} is outside the declared {worst}-{best} range")
                if count is not None and (count <= 0 or count != count.to_integral_value()):
                    errors.append("ratingCount/reviewCount must be a positive integer")
                if errors:
                    self._add_finding(
                        check_id="SD-02-aggregate-rating-semantic",
                        severity="high",
                        title="Invalid AggregateRating values",
                        detail=f"AggregateRating on {url}: {'; '.join(errors)}.",
                        affected_urls=[url],
                        locator="AggregateRating",
                        recommendation="Keep ratingValue within bestRating/worstRating and provide a positive integer ratingCount or reviewCount.",
                    )

    def validate_sd03_opengraph(self):
        """SD-03: Open Graph Protocol Tags."""
        req_og = _CONFIG.get("opengraph", {}).get("required_tags", ["og:title", "og:description", "og:image", "og:url", "og:type"])
        for page in self._content_pages():
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
        for page in self._content_pages():
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

        for page in self._content_pages():
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

            # Favicon — a site-wide asset; flag once, on the homepage, not per page.
            if not favicons and self._is_homepage(url):
                self._add_finding(
                    check_id="SD-05-favicon",
                    severity="low",
                    title="Missing favicon link declaration",
                    detail=f"The homepage ({url}) does not declare a <link rel=\"icon\"> or apple-touch-icon tag.",
                    affected_urls=[url],
                    locator='link[rel=icon]',
                    recommendation="Add <link rel=\"icon\" href=\"/favicon.ico\"> to the <head>.",
                )

    def validate_sd06_llms_txt(self):
        """SD-06: llms.txt / Machine-facing summary at site root."""
        root_llms = self.llms_txt_data.get("/llms.txt", {})
        if not root_llms.get("present"):
            self._add_finding(
                check_id="SD-06-llms",
                severity="low",
                title="Missing /llms.txt machine-facing summary file",
                detail=f"No /llms.txt file was found at {self.site_url.rstrip('/')}/llms.txt. Emerging AI web agents use llms.txt to quickly parse site purpose, structure, and documentation.",
                affected_urls=[f"{self.site_url.rstrip('/')}/llms.txt"],
                source="llms_txt",
                recommendation="Create an /llms.txt markdown file at the domain root with an H1 brand title, a blockquote summary, and links to core docs.",
            )
        else:
            length = root_llms.get("length_chars", 0)
            if length < 50:
                self._add_finding(
                    check_id="SD-06-llms-thin",
                    severity="low",
                    title="/llms.txt file is too brief or empty",
                    detail=f"An /llms.txt file was found at {self.site_url}/llms.txt, but it only contains {length} characters.",
                    affected_urls=[f"{self.site_url.rstrip('/')}/llms.txt"],
                    source="llms_txt",
                    recommendation="Expand /llms.txt with a project summary, key product offerings, and curated links.",
                )
            else:
                self._add_finding(
                    check_id="SD-06-llms-valid",
                    severity="info",
                    title="Valid /llms.txt machine-facing summary detected",
                    detail=f"Valid /llms.txt found at {root_llms.get('url', self.site_url.rstrip('/') + '/llms.txt')} ({length} characters). Provides structured context for AI agents.",
                    affected_urls=[root_llms.get("url", f"{self.site_url.rstrip('/')}/llms.txt")],
                    source="llms_txt",
                    recommendation="Keep /llms.txt updated with current brand, product, and canonical doc references.",
                )

    def validate_cross_references(self):
        """SD-07: Cross-reference structured data properties against visible page content."""
        def _extract_numbers(val):
            return re.findall(r"\d+(?:\.\d{2})?", str(val).replace(",", ""))

        for page in self._content_pages():
            url = page.get("url", "")
            visible = page.get("visible_cues", {})
            h1s = visible.get("h1", [])
            body_text = visible.get("text_sample", "").lower()
            detected_prices = visible.get("detected_prices", [])
            detected_currencies = {str(c).upper() for c in visible.get("detected_currencies", [])}
            detected_availability = {str(a).lower() for a in visible.get("detected_availability", [])}
            detected_ratings = {
                value for value in (self._parse_decimal(r) for r in visible.get("detected_ratings", [])) if value is not None
            }
            detected_dates = visible.get("detected_dates", [])
            meta_title = page.get("meta", {}).get("title", "")

            json_ld_blocks = [b for b in page.get("json_ld", {}).get("blocks", []) if b.get("data")]
            all_entities = []
            for b in json_ld_blocks:
                all_entities.extend(self._flatten_entities(b["data"]))

            # 1. Name / Headline vs. visible H1 / Title cross-reference (AP-05).
            # NOTE: Organization/WebSite are intentionally excluded — a brand `name`
            # ("Acme Corporation") legitimately differs from a homepage <h1> tagline
            # ("Ship software faster"), so comparing them produced HIGH false positives.
            for ent in all_entities:
                t = ent.get("@type")
                t_list = t if isinstance(t, list) else ([t] if t else [])
                checked = {"Product", "Article", "BlogPosting", "NewsArticle", "SoftwareApplication", "Service"}
                match_t = next((x for x in t_list if x in checked), None)
                name = ent.get("name") or ent.get("headline")
                if match_t and isinstance(name, str) and (h1s or meta_title):
                    name_words = set(re.findall(r"\w+", name.lower()))
                    # Compare against BOTH the H1(s) and the <title>, so a name that matches
                    # either surface is not flagged.
                    target_text = (" ".join(h1s) + " " + (meta_title or "")).lower()
                    target_words = set(re.findall(r"\w+", target_text))

                    if len(name_words) > 1 and target_words:
                        overlap = len(name_words.intersection(target_words)) / len(name_words)
                        if overlap < 0.35:
                            display_target = h1s[0] if h1s else meta_title
                            self._add_finding(
                                severity="high",
                                title=f"Content discrepancy between Schema.org {match_t} name and visible DOM",
                                detail=(
                                    f"Schema {match_t} declares name/headline '{name}', but visible heading/title shows '{display_target}'. "
                                    f"Low token match ({int(overlap * 100)}%) creates conflicting entity signals for AI models (Anti-Pattern AP-05)."
                                ),
                                affected_urls=[url],
                                recommendation=f"Align Schema.org {match_t} name with the primary visible heading or title on the page.",
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
                t_list = t if isinstance(t, list) else ([t] if t else [])
                is_offer = "Offer" in t_list
                is_product = "Product" in t_list
                if is_offer or (is_product and isinstance(ent.get("offers"), dict) and ent.get("offers").get("@type") != "Offer"):
                    offer_ent = ent if is_offer else ent.get("offers")
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
                                                check_id="SD-07-offer-price-consistency",
                                                severity="high",
                                                title="Pricing discrepancy between Schema Offer and visible DOM text",
                                                detail=(
                                                    f"Schema Offer declares price '{raw_price}' {currency}, but visible page prices are "
                                                    f"{', '.join(detected_prices[:4])}. Conflicting prices damage AI confidence and cause inaccurate citations."
                                                ),
                                                affected_urls=[url],
                                                recommendation="Ensure Schema.org Offer price exactly matches the visible price displayed to visitors.",
                                            )
                                    if currency and detected_currencies and str(currency).upper() not in detected_currencies:
                                        self._add_finding(
                                            check_id="SD-07-offer-currency-consistency",
                                            severity="high",
                                            title="Currency discrepancy between Schema Offer and visible content",
                                            detail=(
                                                f"Schema Offer declares {currency}, while visible pricing uses "
                                                f"{', '.join(sorted(detected_currencies))}."
                                            ),
                                            affected_urls=[url],
                                            recommendation="Align Offer.priceCurrency with the currency visibly attached to the advertised price.",
                                        )
                                except ValueError:
                                    pass

                    availability = str(offer_ent.get("availability", "")).rstrip("/").split("/")[-1].lower()
                    availability_groups = {
                        "instock": {"in stock", "available online"},
                        "outofstock": {"out of stock", "unavailable"},
                        "soldout": {"sold out"},
                        "preorder": {"pre-order", "preorder"},
                        "backorder": {"back-order", "backorder"},
                    }
                    if availability and detected_availability:
                        expected_visible = availability_groups.get(availability, set())
                        visible_states = {
                            state for state, cues in availability_groups.items()
                            if not cues.isdisjoint(detected_availability)
                        }
                        # Multiple visible states usually mean a multi-variant/product
                        # page; without DOM-to-Offer association a contradiction would
                        # be speculative, so only compare one unambiguous state.
                        contradictory = len(visible_states) == 1 and availability not in visible_states
                        if expected_visible and contradictory:
                            self._add_finding(
                                check_id="SD-07-offer-availability-consistency",
                                severity="high",
                                title="Availability discrepancy between Schema Offer and visible content",
                                detail=(
                                    f"Schema Offer declares '{offer_ent.get('availability')}', while visible content says "
                                    f"{', '.join(sorted(detected_availability))}."
                                ),
                                affected_urls=[url],
                                recommendation="Update Offer.availability whenever the visible stock state changes.",
                            )

            # 3. Aggregate rating consistency with visible rating text.
            for ent in all_entities:
                if "AggregateRating" not in self._entity_types(ent):
                    continue
                schema_rating = self._parse_decimal(ent.get("ratingValue"))
                if schema_rating is not None and detected_ratings and all(abs(schema_rating - r) > Decimal("0.05") for r in detected_ratings):
                    self._add_finding(
                        check_id="SD-07-rating-consistency",
                        severity="high",
                        title="AggregateRating discrepancy between schema and visible content",
                        detail=(
                            f"Schema declares ratingValue '{ent.get('ratingValue')}', while visible rating text contains "
                            f"{', '.join(str(r) for r in sorted(detected_ratings))}."
                        ),
                        affected_urls=[url],
                        recommendation="Keep AggregateRating.ratingValue and rating/review counts synchronized with the visible rating summary.",
                    )

            # 4. Publication Date Conflicts
            for ent in all_entities:
                t = ent.get("@type")
                t_list = t if isinstance(t, list) else ([t] if t else [])
                article_t = next((x for x in t_list if x in ("Article", "BlogPosting", "NewsArticle")), None)
                if article_t:
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
                                        f"Schema {article_t} specifies datePublished '{date_pub}' (year {schema_year}), but visible date text "
                                        f"references {', '.join(detected_dates)}. AI engines rely on synchronized timestamps for freshness scoring."
                                    ),
                                    affected_urls=[url],
                                    recommendation="Synchronize Schema datePublished and dateModified with visible article timestamps.",
                                )

    def run_all(self) -> list:
        self.validate_sd01_presence()
        self.validate_sd02_validity()
        self.validate_sd03_opengraph()
        self.validate_sd04_twitter()
        self.validate_sd05_title_meta()
        self.validate_sd06_llms_txt()
        self.validate_cross_references()

        # Order by severity; ids are content-derived (stable), so no renumbering.
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        self.findings.sort(key=lambda x: priority_order.get(x.get("severity", "info"), 99))
        return self.findings


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
    findings = validator.run_all()
    report = {
        "skill": SKILL,
        "site": validator.site_url,
        "total_findings": len(findings),
        "findings": findings,
    }

    output_str = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_str)
    else:
        print(output_str)


if __name__ == "__main__":
    main()
