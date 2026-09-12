#!/usr/bin/env python3
"""engagement_extractor.py — Extract on-site engagement, navigation, orientation, and performance metrics.

Part of the engagement-audit skill in the Brand AI Readiness Audit marketplace.
Fetches pages, extracts navigation structures, orientation cues, load performance red flags,
mobile viewport configuration, call-to-action characteristics, site search elements, and context retention features.

Usage:
    python engagement_extractor.py --url https://example.com
    python engagement_extractor.py --url https://example.com --pages /,/pricing,/about --max-pages 10
    python engagement_extractor.py --url https://example.com --output /tmp/engagement-raw.json

Output: JSON to stdout or specified file.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import re
import sys
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print(
        json.dumps({
            "error": "Missing dependencies: requests or beautifulsoup4. "
                     "Install with: pip install -r requirements.txt"
        }),
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Reference file loader — resolves paths relative to this script's location
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

_USER_AGENT = _CONFIG.get("extraction", {}).get(
    "user_agent",
    "BrandAIReadinessAudit/1.0 (+https://github.com/brand-ai-readiness-audit)",
)
_DEFAULT_TIMEOUT = _CONFIG.get("extraction", {}).get("default_timeout_seconds", 15)
_DEFAULT_MAX_PAGES = _CONFIG.get("extraction", {}).get("default_max_pages", 10)


def extract_navigation_data(soup: BeautifulSoup, page_url: str) -> dict:
    """Analyze navigation clarity, key destinations, and header/footer structure."""
    nav_cfg = _CONFIG.get("navigation", {})
    key_destinations = nav_cfg.get("key_destinations", [
        "about", "pricing", "contact", "products", "services", "solutions", "help", "support", "docs"
    ])

    # Find primary nav elements
    nav_elements = soup.find_all(["nav", "div", "header"], role="navigation")
    if not nav_elements:
        nav_elements = soup.find_all("nav")
    if not nav_elements:
        nav_elements = soup.find_all(class_=re.compile(r"\b(nav|navbar|menu|header-nav|main-nav)\b", re.I))

    header_el = soup.find("header")
    footer_el = soup.find("footer")

    # Extract all links and categorize by location
    all_links = []
    primary_nav_links = []
    footer_links = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        text = a.get_text(separator=" ", strip=True)
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full_url = urljoin(page_url, href)
        link_record = {"text": text, "href": href, "full_url": full_url}
        all_links.append(link_record)

        # Check if inside primary nav
        is_primary = any(nav in a.parents for nav in nav_elements) or (header_el and header_el in a.parents)
        if is_primary:
            primary_nav_links.append(link_record)

        # Check if inside footer
        is_footer = footer_el and footer_el in a.parents
        if is_footer:
            footer_links.append(link_record)

    # Detect which key destinations are covered
    destinations_found = {}
    for dest in key_destinations:
        dest_pattern = re.compile(rf"\b{dest}\b", re.IGNORECASE)
        found_in_primary = any(dest_pattern.search(l["text"]) or dest_pattern.search(l["href"]) for l in primary_nav_links)
        found_in_footer = any(dest_pattern.search(l["text"]) or dest_pattern.search(l["href"]) for l in footer_links)
        found_anywhere = any(dest_pattern.search(l["text"]) or dest_pattern.search(l["href"]) for l in all_links)

        destinations_found[dest] = {
            "in_primary_nav": found_in_primary,
            "in_footer": found_in_footer,
            "found_anywhere": found_anywhere,
        }

    return {
        "has_primary_nav": len(nav_elements) > 0,
        "primary_nav_link_count": len(primary_nav_links),
        "total_internal_links": len(all_links),
        "key_destinations": destinations_found,
    }


def extract_orientation_data(soup: BeautifulSoup, page_url: str) -> dict:
    """Analyze page orientation cues: h1, above-the-fold value proposition, and breadcrumbs."""
    parsed = urlparse(page_url)
    path_segments = [seg for seg in parsed.path.strip("/").split("/") if seg]
    path_depth = len(path_segments)

    # 1. H1 Headings
    h1_tags = soup.find_all("h1")
    h1_list = [h.get_text(separator=" ", strip=True) for h in h1_tags if h.get_text(separator=" ", strip=True)]

    # 2. Hero copy / Above-the-fold value proposition
    hero_text = ""
    # Try finding hero container or main intro
    hero_container = soup.find(class_=re.compile(r"\b(hero|intro|banner|header-content|lead|value-prop)\b", re.I))
    if hero_container:
        p_tag = hero_container.find(["p", "h2", "div"])
        if p_tag:
            hero_text = p_tag.get_text(separator=" ", strip=True)
    if not hero_text:
        # Fallback: look for the first non-empty paragraph in body
        for p in soup.find_all("p"):
            t = p.get_text(separator=" ", strip=True)
            if len(t) >= 20:
                hero_text = t
                break

    # 3. Breadcrumb navigation
    breadcrumb_cfg = _CONFIG.get("orientation", {})
    has_visible_breadcrumbs = False
    breadcrumb_items = []

    # Check for visible breadcrumb UI
    bc_selectors = [
        ".breadcrumb", ".breadcrumbs", "[aria-label='breadcrumb']", "[aria-label='Breadcrumb']",
        "nav.breadcrumb", "ol.breadcrumb", "ul.breadcrumb"
    ]
    for sel in bc_selectors:
        bc_elem = soup.select_one(sel)
        if bc_elem:
            has_visible_breadcrumbs = True
            for li in bc_elem.find_all(["li", "a"]):
                txt = li.get_text(separator=" ", strip=True)
                if txt and txt not in breadcrumb_items and txt not in ("/", ">", "»"):
                    breadcrumb_items.append(txt)
            break

    # Also check JSON-LD BreadcrumbList
    has_schema_breadcrumbs = False
    for script in soup.find_all("script", type="application/ld+json"):
        content = script.string or script.get_text() or ""
        if "BreadcrumbList" in content:
            has_schema_breadcrumbs = True
            break

    return {
        "url_path_depth": path_depth,
        "h1_count": len(h1_list),
        "h1_elements": h1_list,
        "primary_h1": h1_list[0] if h1_list else None,
        "hero_value_prop": hero_text[:250] if hero_text else None,
        "hero_value_prop_chars": len(hero_text),
        "has_breadcrumbs": has_visible_breadcrumbs or has_schema_breadcrumbs,
        "has_visible_breadcrumbs": has_visible_breadcrumbs,
        "has_schema_breadcrumbs": has_schema_breadcrumbs,
        "breadcrumb_items": breadcrumb_items,
    }


def extract_performance_red_flags(soup: BeautifulSoup, page_url: str) -> dict:
    """Detect DOM and resource architecture red flags (render-blocking scripts, images, domains)."""
    parsed_page = urlparse(page_url)
    page_domain = parsed_page.netloc.lower().replace("www.", "")

    head = soup.find("head") or soup

    # 1. Render-blocking scripts in <head>
    render_blocking_scripts = []
    total_head_scripts = 0
    for s in head.find_all("script"):
        src = s.get("src", "").strip()
        stype = (s.get("type") or "").strip().lower()
        if stype in ("application/ld+json", "application/json"):
            continue
        total_head_scripts += 1
        if src:
            is_async = s.has_attr("async")
            is_defer = s.has_attr("defer")
            is_module = stype == "module"
            if not (is_async or is_defer or is_module):
                render_blocking_scripts.append(src)

    # 2. External stylesheets in <head>
    stylesheets = []
    for link in head.find_all("link", rel=True):
        rel = link.get("rel")
        rels = [r.lower() for r in (rel if isinstance(rel, list) else [rel])]
        if "stylesheet" in rels:
            href = link.get("href", "").strip()
            if href:
                stylesheets.append(href)

    # 3. Image dimensions & lazy loading
    all_imgs = soup.find_all("img")
    missing_dimensions = 0
    missing_lazy_loading = 0

    for idx, img in enumerate(all_imgs):
        has_width = img.has_attr("width")
        has_height = img.has_attr("height")
        if not (has_width and has_height):
            missing_dimensions += 1

        # Images after the first 2 should ideally be lazy loaded
        if idx >= 2:
            loading = (img.get("loading") or "").strip().lower()
            if loading != "lazy":
                missing_lazy_loading += 1

    # 4. Third-party domains sprawl
    third_party_domains = set()
    for tag in soup.find_all(["script", "link", "iframe", "img"]):
        src = tag.get("src") or tag.get("href") or ""
        if src.startswith("http"):
            parsed_src = urlparse(src)
            src_domain = parsed_src.netloc.lower().replace("www.", "")
            if src_domain and src_domain != page_domain and not src_domain.endswith("." + page_domain):
                third_party_domains.add(src_domain)

    return {
        "total_head_scripts": total_head_scripts,
        "render_blocking_scripts_count": len(render_blocking_scripts),
        "render_blocking_scripts": render_blocking_scripts[:5],
        "external_stylesheets_count": len(stylesheets),
        "external_stylesheets": stylesheets[:5],
        "total_images": len(all_imgs),
        "images_missing_dimensions": missing_dimensions,
        "images_missing_lazy_loading": missing_lazy_loading,
        "third_party_domains_count": len(third_party_domains),
        "third_party_domains_sample": sorted(list(third_party_domains))[:8],
    }


def extract_mobile_readiness(soup: BeautifulSoup) -> dict:
    """Analyze mobile viewport configuration and responsive viewport restrictions."""
    viewport_meta = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    viewport_content = viewport_meta.get("content", "").strip() if viewport_meta else ""

    has_viewport = bool(viewport_content)
    has_width_device = "width=device-width" in viewport_content.lower()
    has_initial_scale = "initial-scale" in viewport_content.lower()

    # Check for forbidden accessibility anti-patterns
    disables_zoom = any(pat in viewport_content.lower() for pat in [
        "user-scalable=no", "user-scalable=0", "maximum-scale=1.0", "maximum-scale=1"
    ])

    return {
        "has_viewport_meta": has_viewport,
        "viewport_content": viewport_content,
        "has_width_device": has_width_device,
        "has_initial_scale": has_initial_scale,
        "disables_zoom": disables_zoom,
    }


def extract_calls_to_action(soup: BeautifulSoup) -> dict:
    """Extract and categorize CTA buttons and prominent action links."""
    cta_cfg = _CONFIG.get("calls_to_action", {})
    actionable_keywords = cta_cfg.get("actionable_cta_keywords", [
        "start free trial", "get started", "request demo", "buy now", "subscribe", "download",
        "sign up", "book a demo", "try free", "join now", "create account", "contact sales", "order now"
    ])
    generic_phrases = cta_cfg.get("generic_cta_phrases", [
        "learn more", "click here", "read more", "more", "submit", "go", "continue", "view more"
    ])

    cta_elements = []

    # Find buttons and styled CTA links
    for elem in soup.find_all(["button", "a", "input"]):
        is_cta = False
        text = ""

        if elem.name == "button" or elem.get("role") == "button":
            is_cta = True
            text = elem.get_text(separator=" ", strip=True)
        elif elem.name == "input" and elem.get("type") in ("submit", "button"):
            is_cta = True
            text = elem.get("value", "").strip()
        elif elem.name == "a":
            classes = " ".join(elem.get("class", [])) if isinstance(elem.get("class"), list) else str(elem.get("class") or "")
            text_cand = elem.get_text(separator=" ", strip=True)
            text_cand_lower = text_cand.lower()
            if (
                re.search(r"\b(btn|button|cta|action-link)\b", classes, re.I)
                or elem.get("role") == "button"
                or any(kw in text_cand_lower for kw in actionable_keywords)
                or any(gp == text_cand_lower or text_cand_lower.startswith(gp) for gp in generic_phrases)
            ):
                is_cta = True
                text = text_cand

        if is_cta and text and len(text) <= 60:
            cta_elements.append(text)

    # Classify CTAs
    actionable_ctas = []
    generic_ctas = []
    other_ctas = []

    for text in cta_elements:
        text_lower = text.lower()
        if any(kw in text_lower for kw in actionable_keywords):
            actionable_ctas.append(text)
        elif any(gp == text_lower or text_lower.startswith(gp) for gp in generic_phrases):
            generic_ctas.append(text)
        else:
            other_ctas.append(text)

    return {
        "total_ctas": len(cta_elements),
        "actionable_ctas_count": len(actionable_ctas),
        "generic_ctas_count": len(generic_ctas),
        "actionable_ctas_sample": actionable_ctas[:5],
        "generic_ctas_sample": generic_ctas[:5],
        "all_ctas_sample": cta_elements[:8],
    }


def extract_site_search(soup: BeautifulSoup) -> dict:
    """Detect site search inputs, forms, and search trigger components."""
    search_inputs = []
    for inp in soup.find_all("input"):
        itype = (inp.get("type") or "").lower()
        iname = (inp.get("name") or "").lower()
        iplace = (inp.get("placeholder") or "").lower()

        if itype == "search" or "search" in iname or iname == "q" or "search" in iplace:
            search_inputs.append({
                "type": itype,
                "name": iname,
                "placeholder": inp.get("placeholder", ""),
                "has_aria_label": bool(inp.get("aria-label") or inp.get("title")),
            })

    search_forms = soup.find_all("form", attrs={"action": re.compile(r"search", re.I)})
    search_roles = soup.find_all(attrs={"role": "search"})

    # Check for search icon / button or search aria-labels
    search_icons = soup.find_all(class_=re.compile(r"\b(search-icon|search-btn|fa-search|icon-search)\b", re.I))
    search_buttons = soup.find_all(["button", "a"], attrs={"aria-label": re.compile(r"search", re.I)})

    has_search = bool(search_inputs or search_forms or search_roles or search_icons or search_buttons)

    return {
        "has_search": has_search,
        "search_inputs_count": len(search_inputs),
        "search_forms_count": len(search_forms),
        "has_search_role": len(search_roles) > 0,
        "search_inputs_sample": search_inputs[:2],
    }


def extract_context_retention(soup: BeautifulSoup) -> dict:
    """Detect returning-visitor features: recently viewed, saved items, account anchors, and client storage."""
    retention_cfg = _CONFIG.get("context_retention", {})
    patterns = retention_cfg.get("retention_feature_patterns", [
        "recently-viewed", "history", "wishlist", "saved", "favorites", "recommended-for-you", "for-you", "my-account", "profile"
    ])

    features_detected = []
    page_html_lower = str(soup).lower()

    for pat in patterns:
        if pat in page_html_lower:
            features_detected.append(pat)

    # Check for account / profile links
    has_account_link = False
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").lower()
        text = a.get_text(separator=" ", strip=True).lower()
        if any(term in href or term in text for term in ["account", "profile", "dashboard", "sign in", "login"]):
            has_account_link = True
            break

    # Check for client-side storage hooks in script text
    has_client_storage_hooks = any(storage in page_html_lower for storage in ["localstorage", "sessionstorage", "indexeddb"])

    return {
        "has_context_retention": bool(features_detected or has_account_link),
        "features_detected": features_detected[:6],
        "has_account_link": has_account_link,
        "has_client_storage_hooks": has_client_storage_hooks,
    }


def analyze_page(url: str, session: requests.Session) -> dict:
    """Fetch and extract engagement metrics from a single page."""
    try:
        resp = session.get(url, timeout=_DEFAULT_TIMEOUT, allow_redirects=True)
    except Exception as e:
        return {
            "url": url,
            "status_code": 0,
            "error": f"Failed to fetch {url}: {e}",
            "navigation": {},
            "orientation": {},
            "performance": {},
            "mobile": {},
            "ctas": {},
            "search": {},
            "context_retention": {},
        }

    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    nav_data = extract_navigation_data(soup, resp.url)
    orientation_data = extract_orientation_data(soup, resp.url)
    perf_data = extract_performance_red_flags(soup, resp.url)
    mobile_data = extract_mobile_readiness(soup)
    cta_data = extract_calls_to_action(soup)
    search_data = extract_site_search(soup)
    context_data = extract_context_retention(soup)

    return {
        "url": url,
        "final_url": resp.url,
        "status_code": resp.status_code,
        "content_type": resp.headers.get("Content-Type", ""),
        "navigation": nav_data,
        "orientation": orientation_data,
        "performance": perf_data,
        "mobile": mobile_data,
        "ctas": cta_data,
        "search": search_data,
        "context_retention": context_data,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract on-site engagement, navigation, and UX metrics.")
    parser.add_argument("--url", required=True, help="Site root URL (e.g. https://example.com)")
    parser.add_argument("--pages", default="", help="Comma-separated paths or URLs to audit")
    parser.add_argument("--max-pages", type=int, default=_DEFAULT_MAX_PAGES, help="Max pages to inspect")
    parser.add_argument("--output", default="", help="Optional file path to write JSON output")
    args = parser.parse_args()

    base_url = args.url.strip()
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url

    urls_to_check = [base_url]
    if args.pages:
        for p in args.pages.split(","):
            p = p.strip()
            if not p:
                continue
            full = urljoin(base_url, p)
            if full not in urls_to_check:
                urls_to_check.append(full)

    urls_to_check = urls_to_check[:args.max_pages]

    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})

    pages_output = []
    for u in urls_to_check:
        page_res = analyze_page(u, session)
        pages_output.append(page_res)

    final_report = {
        "site": base_url,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "total_pages_audited": len(pages_output),
        "pages": pages_output,
    }

    output_json = json.dumps(final_report, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
