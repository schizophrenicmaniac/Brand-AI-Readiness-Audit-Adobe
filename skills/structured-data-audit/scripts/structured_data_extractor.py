#!/usr/bin/env python3
"""structured_data_extractor.py — Extract JSON-LD, Open Graph, Twitter cards, meta tags, and llms.txt.

Part of the structured-data-audit skill in the Brand AI Readiness Audit marketplace.
Fetches given pages, extracts all machine-readable markup, and extracts visible content
cues for downstream semantic validation and cross-referencing.

Usage:
    python structured_data_extractor.py --url https://example.com
    python structured_data_extractor.py --url https://example.com --pages /,/about,/pricing --max-pages 10

Output: JSON to stdout.
"""

import argparse
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

_USER_AGENT = _CONFIG.get("extraction", {}).get(
    "user_agent",
    "BrandAIReadinessAudit/1.0 (+https://github.com/brand-ai-readiness-audit)",
)
_DEFAULT_TIMEOUT = _CONFIG.get("extraction", {}).get("default_timeout_seconds", 15)
_DEFAULT_MAX_PAGES = _CONFIG.get("extraction", {}).get("default_max_pages", 10)


def clean_json_string(raw_str: str) -> str:
    """Clean common invalid formatting in JSON-LD strings (HTML comments, CDATA, trailing commas)."""
    text = raw_str.strip()
    # Strip HTML comments
    if text.startswith("<!--"):
        text = re.sub(r"^<!--\s*", "", text)
        text = re.sub(r"\s*-->$", "", text)
    # Strip CDATA
    if text.startswith("<![CDATA["):
        text = re.sub(r"^<!\[CDATA\[\s*", "", text)
        text = re.sub(r"\s*\]\]>$", "", text)
    return text.strip()


def extract_json_ld_blocks(soup: BeautifulSoup) -> list:
    """Extract and parse all application/ld+json script blocks."""
    blocks = []
    scripts = soup.find_all("script", type="application/ld+json")
    for idx, script in enumerate(scripts):
        raw_content = script.string or script.get_text() or ""
        cleaned = clean_json_string(raw_content)
        if not cleaned:
            continue

        block_record = {
            "index": idx,
            "raw_length": len(raw_content),
            "parse_error": None,
            "raw_snippet": cleaned[:250],
            "data": None,
        }

        try:
            parsed = json.loads(cleaned)
            block_record["data"] = parsed
        except json.JSONDecodeError as err:
            block_record["parse_error"] = {
                "message": str(err),
                "line": err.lineno,
                "col": err.colno,
            }

        blocks.append(block_record)
    return blocks


def extract_open_graph_tags(soup: BeautifulSoup) -> dict:
    """Extract Open Graph meta tags."""
    og_data = {}
    for meta in soup.find_all("meta"):
        prop = meta.get("property") or meta.get("name") or ""
        prop = prop.strip().lower()
        if prop.startswith("og:"):
            content = meta.get("content", "").strip()
            if prop in og_data:
                # Handle multi-value tags like og:image
                if isinstance(og_data[prop], list):
                    og_data[prop].append(content)
                else:
                    og_data[prop] = [og_data[prop], content]
            else:
                og_data[prop] = content
    return og_data


def extract_twitter_card_tags(soup: BeautifulSoup) -> dict:
    """Extract Twitter / X card meta tags."""
    twitter_data = {}
    for meta in soup.find_all("meta"):
        name = meta.get("name") or meta.get("property") or ""
        name = name.strip().lower()
        if name.startswith("twitter:"):
            content = meta.get("content", "").strip()
            twitter_data[name] = content
    return twitter_data


def extract_meta_tags(soup: BeautifulSoup, base_url: str) -> dict:
    """Extract page title, description, favicon, canonical, and other metadata."""
    title_tag = soup.find("title")
    title = title_tag.get_text().strip() if title_tag else ""

    description = ""
    keywords = ""
    robots = ""
    author = ""

    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").strip().lower()
        content = meta.get("content", "").strip()
        if name == "description":
            description = content
        elif name == "keywords":
            keywords = content
        elif name == "robots":
            robots = content
        elif name == "author":
            author = content

    # Canonical
    canonical_tag = soup.find("link", rel=lambda r: r and "canonical" in r.lower())
    canonical = canonical_tag.get("href", "").strip() if canonical_tag else ""
    if canonical:
        canonical = urljoin(base_url, canonical)

    # Favicons
    favicons = []
    for link in soup.find_all("link", rel=True):
        rels = [r.lower() for r in (link.get("rel") if isinstance(link.get("rel"), list) else [link.get("rel")])]
        if any("icon" in r for r in rels):
            href = link.get("href", "").strip()
            if href:
                favicons.append({
                    "rel": " ".join(rels),
                    "href": urljoin(base_url, href),
                    "sizes": link.get("sizes", ""),
                    "type": link.get("type", "")
                })

    return {
        "title": title,
        "description": description,
        "keywords": keywords,
        "robots": robots,
        "author": author,
        "canonical": canonical,
        "favicons": favicons,
    }


def extract_visible_cues(soup: BeautifulSoup) -> dict:
    """Extract visible content cues to cross-reference against structured data."""
    # Headings
    h1s = [h.get_text().strip() for h in soup.find_all("h1") if h.get_text().strip()]
    h2s = [h.get_text().strip() for h in soup.find_all("h2") if h.get_text().strip()][:10]

    # Detect body text
    body = soup.find("body")
    text = body.get_text(separator=" ", strip=True) if body else ""

    # Potential price mentions
    prices = list(set(re.findall(r"[\$€£¥₹]\s*\d+(?:\.\d{2})?|\b\d+(?:\.\d{2})?\s*(?:USD|EUR|GBP|INR)\b", text)))[:10]

    # Potential date mentions
    dates = list(set(re.findall(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b|\b\d{4}-\d{2}-\d{2}\b", text)))[:5]

    # FAQ cues: headings, details elements, or distinct Q&A formatting
    faq_headings = [h for h in (h1s + h2s) if re.search(r"\b(faq|frequently\s+asked|questions?\s*(&|and)?\s*answers?)\b", h, re.IGNORECASE)]
    has_details = len(soup.find_all("details")) >= 2
    has_faq_content = bool(faq_headings or has_details)

    return {
        "h1": h1s,
        "h2_sample": h2s,
        "detected_prices": prices,
        "detected_dates": dates,
        "has_faq_content": has_faq_content,
        "faq_headings": faq_headings,
        "body_char_count": len(text),
        "text_sample": text[:350],
    }


def check_llms_txt(base_url: str, session: requests.Session) -> dict:
    """Check for llms.txt and llms-full.txt presence at site root."""
    parsed = urlparse(base_url)
    root_origin = f"{parsed.scheme}://{parsed.netloc}"

    targets = _CONFIG.get("llms_txt", {}).get("root_paths", ["/llms.txt", "/llms-full.txt", "/.well-known/llms.txt"])
    results = {}

    for path in targets:
        full_url = urljoin(root_origin, path)
        try:
            resp = session.get(full_url, timeout=8, allow_redirects=True)
            if resp.status_code == 200:
                content_type = resp.headers.get("Content-Type", "").lower()
                is_text = "text/plain" in content_type or "text/markdown" in content_type or "text/" in content_type
                body = resp.text
                results[path] = {
                    "present": True,
                    "url": full_url,
                    "status_code": resp.status_code,
                    "content_type": content_type,
                    "length_chars": len(body),
                    "has_title": bool(re.search(r"^#\s+", body, re.MULTILINE)),
                    "has_summary": bool(re.search(r"^>\s+", body, re.MULTILINE)),
                    "has_sections": bool(re.search(r"^##\s+", body, re.MULTILINE)),
                    "snippet": body[:300],
                }
            else:
                results[path] = {
                    "present": False,
                    "url": full_url,
                    "status_code": resp.status_code,
                }
        except Exception as e:
            results[path] = {
                "present": False,
                "url": full_url,
                "error": str(e),
            }

    return results


def analyze_page(url: str, session: requests.Session) -> dict:
    """Fetch and extract structured data from a single URL."""
    try:
        resp = session.get(url, timeout=_DEFAULT_TIMEOUT, allow_redirects=True)
    except Exception as e:
        return {
            "url": url,
            "error": f"Failed to fetch {url}: {e}",
            "status_code": 0,
        }

    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    json_ld_blocks = extract_json_ld_blocks(soup)
    og_tags = extract_open_graph_tags(soup)
    twitter_tags = extract_twitter_card_tags(soup)
    meta_tags = extract_meta_tags(soup, resp.url)
    visible_cues = extract_visible_cues(soup)

    return {
        "url": url,
        "final_url": resp.url,
        "status_code": resp.status_code,
        "content_type": resp.headers.get("Content-Type", ""),
        "json_ld": {
            "total_blocks": len(json_ld_blocks),
            "blocks": json_ld_blocks,
        },
        "open_graph": og_tags,
        "twitter_card": twitter_tags,
        "meta": meta_tags,
        "visible_cues": visible_cues,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract structured data from website pages.")
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
        res = analyze_page(u, session)
        pages_output.append(res)

    llms_txt_results = check_llms_txt(base_url, session)

    final_report = {
        "site": base_url,
        "total_pages_audited": len(pages_output),
        "llms_txt": llms_txt_results,
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
