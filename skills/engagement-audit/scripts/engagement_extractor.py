#!/usr/bin/env python3
"""Extract measurable engagement signals from fetched HTML pages.

Public integration:
    build_raw(base_url, paths, session, max_pages) -> raw engagement dict

The caller may pass a shared/cached requests-compatible session. Failed, blocked,
empty, and non-HTML responses are retained as coverage evidence but are never
parsed as pages suitable for engagement validation.
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
    print(json.dumps({"error": "Missing dependencies: requests or beautifulsoup4. Install with: pip install -r requirements.txt"}), file=sys.stderr)
    sys.exit(1)

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REFS_DIR = os.path.normpath(os.path.join(_SCRIPTS_DIR, "..", "references"))


def _load_json(filename: str) -> dict:
    try:
        with open(os.path.join(_REFS_DIR, filename), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


_CONFIG = _load_json("engagement-config.json")
_EXTRACTION = _CONFIG.get("extraction", {})
_USER_AGENT = _EXTRACTION.get("user_agent", "BrandAIReadinessAudit/1.0")
_DEFAULT_TIMEOUT = _EXTRACTION.get("default_timeout_seconds", 15)
_DEFAULT_MAX_PAGES = _EXTRACTION.get("default_max_pages", 10)
_BLOCKED_STATUS = {401, 403, 407, 423, 429, 451, 503}
_BLOCK_MARKERS = (
    "cf-chl-", "cloudflare ray id", "checking your browser", "just a moment...",
    "access denied", "verify you are human", "captcha", "request blocked",
)


def _normalize_base(url: str) -> str:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _is_same_site(candidate: str, page_url: str) -> bool:
    target = urlparse(candidate)
    page = urlparse(page_url)
    if target.scheme not in ("http", "https"):
        return False
    return target.netloc.lower().removeprefix("www.") == page.netloc.lower().removeprefix("www.")


def _unique_links(elements, page_url: str) -> list:
    links, seen = [], set()
    for a in elements:
        href = (a.get("href") or "").strip()
        if not href or href.lower().startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full = urljoin(page_url, href)
        key = full.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        links.append({
            "text": a.get_text(" ", strip=True)[:120],
            "href": href,
            "full_url": full,
            "internal": _is_same_site(full, page_url),
        })
    return links


def _page_profile(soup: BeautifulSoup, page_url: str) -> dict:
    path = urlparse(page_url).path.lower()
    body_classes = " ".join(soup.body.get("class", [])) if soup.body else ""
    hints = (path + " " + body_classes + " " + (soup.title.get_text(" ", strip=True).lower() if soup.title else ""))
    commercial_terms = ("pricing", "plans", "product", "shop", "store", "cart", "checkout", "buy")
    docs_terms = ("docs", "documentation", "developer", "reference", "guide", "api")
    editorial_terms = ("news", "article", "blog", "story", "press", "journal", "magazine")
    article = bool(soup.find("article"))
    scores = {
        "commercial": sum(term in hints for term in commercial_terms),
        "documentation": sum(term in hints for term in docs_terms),
        "editorial": sum(term in hints for term in editorial_terms) + int(article),
    }
    highest = max(scores.values())
    # Prefer editorial/docs on ties so a product-news article is not treated as a
    # conversion page merely because its title contains "product".
    kind = "general"
    for candidate in ("editorial", "documentation", "commercial"):
        if highest and scores[candidate] == highest:
            kind = candidate
            break
    return {"kind": kind, "signals": scores, "has_article_element": article}


def extract_navigation_data(soup: BeautifulSoup, page_url: str) -> dict:
    semantic = soup.select("nav, [role='navigation']")
    inferred = soup.select("header .nav, header .navbar, header .menu, .main-navigation, .primary-navigation")
    containers = semantic or inferred
    header = soup.find("header")
    footer = soup.find("footer")
    primary_elements = []
    for container in containers:
        if footer and (container is footer or footer in container.parents):
            continue
        primary_elements.extend(container.find_all("a", href=True))
    if not primary_elements and header:
        primary_elements = header.find_all("a", href=True)

    all_links = _unique_links(soup.find_all("a", href=True), page_url)
    primary_links = _unique_links(primary_elements, page_url)
    footer_links = _unique_links(footer.find_all("a", href=True), page_url) if footer else []
    terms = _CONFIG.get("navigation", {}).get("key_destinations", [])
    destinations = {}
    for term in terms:
        pattern = re.compile(r"(?:^|[\W_-])" + re.escape(term) + r"(?:$|[\W_-])", re.I)
        match = lambda link: bool(pattern.search(link["text"]) or pattern.search(link["href"]))
        destinations[term] = {
            "in_primary_nav": any(match(link) for link in primary_links),
            "in_footer": any(match(link) for link in footer_links),
            "found_anywhere": any(match(link) for link in all_links),
        }
    labels = sorted({link["text"].strip().lower() for link in primary_links if link["text"].strip()})
    return {
        "has_primary_nav": bool(containers or len(primary_links) >= 2),
        "has_navigation_landmark": bool(semantic),
        "primary_nav_link_count": len(primary_links),
        "internal_link_count": sum(link["internal"] for link in all_links),
        "total_links": len(all_links),
        "primary_nav_labels": labels[:40],
        "primary_nav_links_sample": primary_links[:12],
        "key_destinations": destinations,
    }


def _has_breadcrumb_schema(soup: BeautifulSoup) -> bool:
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        if "breadcrumblist" in script.get_text(" ", strip=True).lower():
            return True
    return bool(soup.find(attrs={"itemtype": re.compile(r"BreadcrumbList", re.I)}))


def extract_orientation_data(soup: BeautifulSoup, page_url: str) -> dict:
    depth = len([part for part in urlparse(page_url).path.split("/") if part])
    h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1") if h.get_text(" ", strip=True)]
    breadcrumb = soup.select_one(".breadcrumb, .breadcrumbs, [aria-label*='breadcrumb' i], nav[aria-label*='breadcrumb' i]")
    breadcrumb_items = []
    if breadcrumb:
        breadcrumb_items = [text for text in (el.get_text(" ", strip=True) for el in breadcrumb.find_all(["a", "li"])) if text][:12]

    main = soup.find("main") or soup.body or soup
    intro = ""
    hero = main.find(class_=re.compile(r"(?:^|[-_ ])(hero|intro|lead|banner|value[-_ ]?prop)(?:$|[-_ ])", re.I))
    candidates = hero.find_all(["p", "h2"], limit=3) if hero else main.find_all(["p", "h2"], limit=5)
    for candidate in candidates:
        text = candidate.get_text(" ", strip=True)
        if len(text) >= 20:
            intro = text
            break
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return {
        "url_path_depth": depth,
        "document_title": title[:200],
        "h1_count": len(h1s),
        "h1_elements": h1s[:8],
        "primary_h1": h1s[0] if h1s else None,
        "hero_value_prop": intro[:300] or None,
        "hero_value_prop_chars": len(intro),
        "has_breadcrumbs": bool(breadcrumb) or _has_breadcrumb_schema(soup),
        "has_visible_breadcrumbs": bool(breadcrumb),
        "has_schema_breadcrumbs": _has_breadcrumb_schema(soup),
        "breadcrumb_items": breadcrumb_items,
    }


def extract_performance_red_flags(soup: BeautifulSoup, page_url: str) -> dict:
    head = soup.head or soup
    blocking = []
    for script in head.find_all("script", src=True):
        stype = (script.get("type") or "").lower()
        if stype not in ("application/ld+json", "application/json") and not any(script.has_attr(a) for a in ("async", "defer")) and stype != "module":
            blocking.append(script.get("src"))
    stylesheets = []
    for link in head.find_all("link", href=True):
        rel = link.get("rel") or []
        rel_values = rel if isinstance(rel, list) else str(rel).split()
        if "stylesheet" in [str(value).lower() for value in rel_values]:
            stylesheets.append(link.get("href"))
    images = soup.find_all("img")
    missing_dimensions = [img.get("src") or img.get("alt") or "img" for img in images if not (img.get("width") and img.get("height"))]
    below_initial = images[2:]
    missing_lazy = [img.get("src") or img.get("alt") or "img" for img in below_initial if (img.get("loading") or "").lower() != "lazy"]
    page_host = urlparse(page_url).netloc.lower().removeprefix("www.")
    domains = set()
    for tag in soup.find_all(["script", "link", "iframe", "img", "source"]):
        ref = tag.get("src") or tag.get("href") or tag.get("srcset") or ""
        candidate = ref.split(",", 1)[0].strip().split(" ", 1)[0]
        host = urlparse(urljoin(page_url, candidate)).netloc.lower().removeprefix("www.")
        if host and host != page_host and not host.endswith("." + page_host):
            domains.add(host)
    count = len(images)
    return {
        "total_head_scripts": len(head.find_all("script")),
        "render_blocking_scripts_count": len(blocking),
        "render_blocking_scripts": blocking[:8],
        "external_stylesheets_count": len(stylesheets),
        "external_stylesheets": stylesheets[:8],
        "total_images": count,
        "images_missing_dimensions": len(missing_dimensions),
        "images_missing_dimensions_ratio": round(len(missing_dimensions) / count, 3) if count else 0,
        "images_missing_dimensions_sample": missing_dimensions[:5],
        "below_initial_images": len(below_initial),
        "images_missing_lazy_loading": len(missing_lazy),
        "images_missing_lazy_loading_sample": missing_lazy[:5],
        "third_party_domains_count": len(domains),
        "third_party_domains_sample": sorted(domains)[:10],
    }


def extract_mobile_readiness(soup: BeautifulSoup) -> dict:
    viewport = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    content = (viewport.get("content") or "").strip() if viewport else ""
    lower = re.sub(r"\s+", "", content.lower())
    user_scalable_no = bool(re.search(r"user-scalable=(?:no|0)(?:[,;]|$)", lower))
    maximum = re.search(r"maximum-scale=([0-9]+(?:\.[0-9]+)?)", lower)
    maximum_value = float(maximum.group(1)) if maximum else None
    return {
        "has_viewport_meta": bool(viewport and content),
        "viewport_content": content,
        "has_width_device": bool(re.search(r"(?:^|[,;])width=device-width(?:[,;]|$)", lower)),
        "has_initial_scale": bool(re.search(r"(?:^|[,;])initial-scale=1(?:\.0+)?(?:[,;]|$)", lower)),
        "maximum_scale": maximum_value,
        "disables_zoom": user_scalable_no or (maximum_value is not None and maximum_value < 2),
    }


def extract_calls_to_action(soup: BeautifulSoup) -> dict:
    cfg = _CONFIG.get("calls_to_action", {})
    actionable_words = cfg.get("actionable_cta_keywords", [])
    generic_words = cfg.get("generic_cta_phrases", [])
    records = []
    for elem in soup.find_all(["button", "a", "input"]):
        text = (elem.get("value") if elem.name == "input" else elem.get_text(" ", strip=True)) or ""
        text = re.sub(r"\s+", " ", text).strip()
        if not text or len(text) > 80:
            continue
        classes = " ".join(elem.get("class") or [])
        lower = text.lower()
        styled = bool(re.search(r"(?:^|[-_ ])(?:btn|button|cta|action)(?:$|[-_ ])", classes, re.I))
        control = elem.name == "button" or elem.get("role") == "button" or (elem.name == "input" and (elem.get("type") or "").lower() in ("submit", "button"))
        actionable = any(word in lower for word in actionable_words)
        generic = any(lower == word or lower.startswith(word + " ") for word in generic_words)
        if not (styled or control or actionable or generic):
            continue
        records.append({"text": text, "element": elem.name, "styled": styled, "actionable": actionable, "generic": generic})
    actionable = [r for r in records if r["actionable"]]
    generic = [r for r in records if r["generic"]]
    return {
        "total_ctas": len(records),
        "actionable_ctas_count": len(actionable),
        "actionable_ctas_unique_count": len({r["text"].lower() for r in actionable}),
        "generic_ctas_count": len(generic),
        "generic_cta_ratio": round(len(generic) / len(records), 3) if records else 0,
        "actionable_ctas_sample": [r["text"] for r in actionable[:6]],
        "generic_ctas_sample": [r["text"] for r in generic[:6]],
        "all_ctas_sample": [r["text"] for r in records[:10]],
        "cta_evidence": records[:12],
    }


def extract_site_search(soup: BeautifulSoup) -> dict:
    inputs = []
    for inp in soup.find_all("input"):
        attrs = " ".join(str(inp.get(name) or "") for name in ("type", "name", "placeholder", "aria-label", "title"))
        if re.search(r"\b(search|site search)\b", attrs, re.I) or (inp.get("name") or "").lower() == "q":
            inputs.append({"type": inp.get("type", ""), "name": inp.get("name", ""), "placeholder": inp.get("placeholder", ""), "accessible_name": inp.get("aria-label") or inp.get("title") or inp.get("placeholder") or ""})
    forms = soup.find_all("form", action=re.compile(r"search", re.I))
    roles = soup.find_all(attrs={"role": "search"})
    labelled_triggers = soup.find_all(["button", "a"], attrs={"aria-label": re.compile(r"search", re.I)})
    icon_triggers = soup.find_all(["button", "a"], class_=re.compile(r"(?:^|[-_ ])(?:search|search-icon|search-btn)(?:$|[-_ ])", re.I))
    inaccessible = [el for el in icon_triggers if not (el.get("aria-label") or el.get("title") or el.get_text(" ", strip=True))]
    return {
        "has_search": bool(inputs or forms or roles or labelled_triggers or icon_triggers),
        "search_inputs_count": len(inputs),
        "search_forms_count": len(forms),
        "search_landmarks_count": len(roles),
        "search_triggers_count": len({id(el) for el in labelled_triggers + icon_triggers}),
        "inaccessible_icon_triggers_count": len(inaccessible),
        "search_inputs_sample": inputs[:3],
    }


def extract_context_retention(soup: BeautifulSoup) -> dict:
    cfg = _CONFIG.get("context_retention", {})
    patterns = cfg.get("retention_feature_patterns", [])
    evidence = []
    for element in soup.find_all(["a", "button", "section", "div"], limit=2000):
        text = element.get_text(" ", strip=True)[:100] if element.name in ("a", "button") else ""
        haystack = " ".join(filter(None, [element.get("id"), " ".join(element.get("class") or []), element.get("href") if element.name == "a" else None, element.get("aria-label"), text])).lower()
        for pattern in patterns:
            normalized = pattern.replace("-", r"[-_ ]?")
            if re.search(r"(?:^|\b)" + normalized + r"(?:\b|$)", haystack, re.I):
                evidence.append(pattern)
    evidence = list(dict.fromkeys(evidence))
    script_text = " ".join(script.get_text(" ", strip=True) for script in soup.find_all("script"))[:500000].lower()
    storage = [item for item in cfg.get("storage_indicators", []) if item in script_text]
    account = any(item in evidence for item in ("my-account", "profile", "dashboard")) or bool(soup.find("a", href=re.compile(r"/(?:account|profile|login|signin)(?:/|$)", re.I)))
    return {
        "has_context_retention": bool(evidence or storage or account),
        "features_detected": evidence[:10],
        "has_account_link": account,
        "has_client_storage_hooks": bool(storage),
        "storage_indicators": storage,
    }


def _base_page(url: str, status_code=0, content_type="", final_url=None, error=None, fetch_status="fetch_error") -> dict:
    return {
        "url": url, "final_url": final_url or url, "status_code": status_code,
        "content_type": content_type or "", "fetch_status": fetch_status,
        "html_success": False, "error": error,
        "navigation": {}, "orientation": {}, "performance": {}, "mobile": {},
        "ctas": {}, "search": {}, "context_retention": {}, "page_profile": {},
    }


def analyze_html_page(url: str, html: str, status_code: int = 200, content_type: str = "text/html", final_url=None) -> dict:
    """Extract one already-fetched page; useful for cached orchestrator HTML."""
    page = _base_page(url, status_code, content_type, final_url, fetch_status="http_error")
    if status_code < 200 or status_code >= 300:
        page["fetch_status"] = "blocked" if status_code in _BLOCKED_STATUS else "http_error"
        page["error"] = f"HTTP {status_code}"
        return page
    ctype = (content_type or "").lower()
    if ctype and "html" not in ctype and "xhtml" not in ctype:
        page["fetch_status"] = "non_html"
        page["error"] = f"Non-HTML content type: {content_type}"
        return page
    if not html or not html.strip():
        page["fetch_status"] = "empty"
        page["error"] = "Empty HTML response"
        return page
    lower_sample = html[:200000].lower()
    marker = next((item for item in _BLOCK_MARKERS if item in lower_sample), None)
    if marker and len(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)) < 5000:
        page["fetch_status"] = "blocked"
        page["error"] = f"Probable interstitial/block page ({marker})"
        return page

    soup = BeautifulSoup(html, "html.parser")
    page.update({
        "fetch_status": "success", "html_success": True, "error": None,
        "html_bytes": len(html.encode("utf-8", errors="ignore")),
        "navigation": extract_navigation_data(soup, final_url or url),
        "orientation": extract_orientation_data(soup, final_url or url),
        "performance": extract_performance_red_flags(soup, final_url or url),
        "mobile": extract_mobile_readiness(soup),
        "ctas": extract_calls_to_action(soup),
        "search": extract_site_search(soup),
        "context_retention": extract_context_retention(soup),
        "page_profile": _page_profile(soup, final_url or url),
    })
    return page


def analyze_page(url: str, session: requests.Session) -> dict:
    try:
        response = session.get(url, timeout=_DEFAULT_TIMEOUT, allow_redirects=True)
    except Exception as exc:
        return _base_page(url, error=f"Fetch failed: {str(exc)[:300]}")
    return analyze_html_page(url, response.text, response.status_code, response.headers.get("Content-Type", ""), response.url)


def build_raw(base_url: str, paths, session: requests.Session, max_pages: int = _DEFAULT_MAX_PAGES) -> dict:
    """Fetch and extract a bounded page set for ``EngagementValidator``.

    ``paths`` accepts paths or absolute URLs. ``session`` is any requests-compatible
    session, including the orchestrator's cached SafeSession.
    """
    base_url = _normalize_base(base_url)
    try:
        limit = max(0, int(max_pages))
    except (TypeError, ValueError):
        limit = _DEFAULT_MAX_PAGES
    urls, seen = [], set()
    if isinstance(paths, str):
        paths = paths.split(",") if paths else []
    for candidate in [base_url] + list(paths or []):
        if candidate is None:
            continue
        candidate = str(candidate).strip()
        if not candidate:
            continue
        full = urljoin(base_url, candidate)
        canonical = full.split("#", 1)[0]
        if canonical not in seen:
            seen.add(canonical)
            urls.append(canonical)
    pages = [analyze_page(url, session) for url in urls[:limit]]
    successful = sum(page.get("html_success") is True for page in pages)
    return {
        "site": base_url,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "total_pages_audited": len(pages),
        "successful_html_pages": successful,
        "skipped_pages": len(pages) - successful,
        "pages": pages,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract engagement and UX signals from HTML pages.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--pages", default="", help="Comma-separated paths or URLs")
    parser.add_argument("--max-pages", type=int, default=_DEFAULT_MAX_PAGES)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})
    raw = build_raw(args.url, args.pages.split(",") if args.pages else [], session, args.max_pages)
    output = json.dumps(raw, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main()
