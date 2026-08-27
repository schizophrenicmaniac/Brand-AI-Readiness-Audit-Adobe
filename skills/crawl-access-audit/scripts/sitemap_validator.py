#!/usr/bin/env python3
"""sitemap_validator.py — Fetch, parse, and validate XML sitemaps.

Part of the crawl-access-audit skill in the Brand AI Readiness Audit marketplace.
Discovers sitemaps via robots.txt Sitemap: directives or standard paths, validates
XML structure, checks URL freshness, samples URLs for HTTP status, and detects
parameter proliferation.

Usage:
    python3 sitemap_validator.py --url https://example.com
    python3 sitemap_validator.py --url https://example.com --sitemap-urls https://example.com/sitemap.xml
    python3 sitemap_validator.py --url https://example.com --sample-size 20

Output: JSON to stdout.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, urljoin
from xml.etree import ElementTree as ET
from url_utils import normalize_url
from robots_analyzer import parse_robots_txt, evaluate_bot

try:
    import requests
except ImportError:
    print(json.dumps({"error": "Missing dependency: requests. Install with: pip install requests"}))
    sys.exit(1)

# ---------------------------------------------------------------------------
# Reference file loader — resolves paths relative to this script's location.
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REFS_DIR = os.path.join(_SCRIPTS_DIR, "..", "references")


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


# ---------------------------------------------------------------------------
# Load data from SSOT reference file — edit references/crawl-config.json,
# not this script, to change thresholds or tracking parameter lists.
# ---------------------------------------------------------------------------
_config = _load_json("crawl-config.json")["sitemap"]

MAX_SITEMAPS       = _config["max_sitemaps_to_process"]
MAX_URLS_TO_PARSE  = _config["max_urls_to_parse"]
DEFAULT_SAMPLE_SIZE = _config["default_sample_size"]
STALE_THRESHOLD_DAYS = _config["stale_lastmod_threshold_days"]
TRACKING_PARAMS    = set(_config["tracking_params"])
_MAX_URLS_PER_FILE = _config["max_urls_per_file"]
_MIN_PROLIFERATION_VARIANTS = _config["parameter_proliferation_min_variants"]

# Sitemap XML namespaces
SITEMAP_NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "image": "http://www.google.com/schemas/sitemap-image/1.1",
    "video": "http://www.google.com/schemas/sitemap-video/1.1",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}


def fetch_sitemap(url: str, timeout: int = 15) -> dict:
    """Fetch a single sitemap URL and return status + content."""
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True,
                            headers={"User-Agent": "BrandAIReadinessAudit/1.0"})
        return {
            "url": url,
            "status": resp.status_code,
            "content": resp.text if resp.status_code == 200 else None,
            "content_length": len(resp.content) if resp.status_code == 200 else 0,
            "error": None,
        }
    except requests.exceptions.Timeout:
        return {"url": url, "status": None, "content": None, "content_length": 0,
                "error": "Timeout (>15s)"}
    except requests.exceptions.ConnectionError as e:
        return {"url": url, "status": None, "content": None, "content_length": 0,
                "error": f"Connection error: {str(e)[:200]}"}
    except Exception as e:
        return {"url": url, "status": None, "content": None, "content_length": 0,
                "error": f"Error: {str(e)[:200]}"}


def parse_sitemap_xml(content: str, url: str) -> dict:
    """Parse a sitemap XML document and extract URLs or sub-sitemap references.

    Returns:
        {
            "type": "urlset" | "sitemapindex" | "error",
            "urls": [{"loc": ..., "lastmod": ...}],
            "sub_sitemaps": [...],
            "xml_errors": [...]
        }
    """
    result = {
        "type": "error",
        "urls": [],
        "sub_sitemaps": [],
        "xml_errors": [],
    }

    # Strip BOM and whitespace
    content = content.strip().lstrip("\ufeff")

    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        result["xml_errors"].append(f"XML parse error: {str(e)}")
        return result

    # Detect root element type
    tag = root.tag.lower()

    # Handle sitemapindex
    if "sitemapindex" in tag:
        result["type"] = "sitemapindex"
        for sitemap_el in root:
            if "sitemap" in sitemap_el.tag.lower():
                loc_el = None
                for child in sitemap_el:
                    if "loc" in child.tag.lower():
                        loc_el = child
                        break
                if loc_el is not None and loc_el.text:
                    result["sub_sitemaps"].append(loc_el.text.strip())
        return result

    # Handle urlset
    if "urlset" in tag:
        result["type"] = "urlset"
        for url_el in root:
            if "url" not in url_el.tag.lower():
                continue
            entry = {"loc": None, "lastmod": None}
            for child in url_el:
                child_tag = child.tag.lower()
                if "loc" in child_tag and child.text:
                    entry["loc"] = child.text.strip()
                elif "lastmod" in child_tag and child.text:
                    entry["lastmod"] = child.text.strip()
            if entry["loc"]:
                result["urls"].append(entry)
        return result

    result["xml_errors"].append(f"Unrecognized root element: {root.tag}")
    return result


def check_lastmod_freshness(urls: list) -> dict:
    """Analyze lastmod dates across all URLs."""
    now = datetime.now(timezone.utc)
    total_with_lastmod = 0
    stale_count = 0
    oldest_lastmod = None
    newest_lastmod = None

    for entry in urls:
        if not entry.get("lastmod"):
            continue
        total_with_lastmod += 1
        try:
            # Parse various lastmod formats
            lm_str = entry["lastmod"]
            dt = None
            for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m"):
                try:
                    dt = datetime.strptime(lm_str[:len("2024-01-01T00:00:00+00:00")], fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            if dt is None:
                continue

            age_days = (now - dt).days
            if age_days > STALE_THRESHOLD_DAYS:
                stale_count += 1

            lm_iso = dt.strftime("%Y-%m-%d")
            if oldest_lastmod is None or dt < datetime.strptime(oldest_lastmod, "%Y-%m-%d").replace(tzinfo=timezone.utc):
                oldest_lastmod = lm_iso
            if newest_lastmod is None or dt > datetime.strptime(newest_lastmod, "%Y-%m-%d").replace(tzinfo=timezone.utc):
                newest_lastmod = lm_iso
        except Exception:
            continue

    return {
        "total_with_lastmod": total_with_lastmod,
        "total_without_lastmod": len(urls) - total_with_lastmod,
        "stale_count": stale_count,
        "stale_threshold_days": STALE_THRESHOLD_DAYS,
        "oldest_lastmod": oldest_lastmod,
        "newest_lastmod": newest_lastmod,
    }


def sample_check_urls(urls: list, sample_size: int, robots_groups: list | None = None) -> list:
    """Deterministically sample URLs; fall back to GET when HEAD is unsupported."""
    if not urls:
        return []

    locs = sorted({u["loc"] for u in urls if u.get("loc")})
    if len(locs) > sample_size:
        # Evenly-spaced sampling is repeatable and covers the sitemap range.
        sampled = [locs[(index * (len(locs) - 1)) // (sample_size - 1)] for index in range(sample_size)] if sample_size > 1 else [locs[0]]
    else:
        sampled = locs

    results = []
    for loc in sampled:
        if robots_groups is not None and evaluate_bot(robots_groups, "BrandAIReadinessAudit", urlparse(loc).path or "/")["blocked"]:
            results.append({"url": loc, "skipped": True, "skip_reason": "Disallowed by robots.txt for BrandAIReadinessAudit"})
            continue
        try:
            resp = requests.head(loc, timeout=10, allow_redirects=True,
                                 headers={"User-Agent": "BrandAIReadinessAudit/1.0"})
            method = "HEAD"
            if resp.status_code in (403, 405, 501):
                resp = requests.get(loc, timeout=10, allow_redirects=True, stream=True,
                                    headers={"User-Agent": "BrandAIReadinessAudit/1.0"})
                method = "GET-fallback"
            results.append({"url": loc, "status": resp.status_code, "method": method, "final_url": resp.url})
        except requests.exceptions.Timeout:
            results.append({"url": loc, "status": None, "error": "timeout"})
        except Exception as e:
            results.append({"url": loc, "status": None, "error": str(e)[:100]})

    return results


def detect_parameter_proliferation(urls: list) -> dict:
    """Detect URLs that differ only by query parameters (crawl trap risk)."""
    path_groups = defaultdict(list)
    for entry in urls:
        loc = entry.get("loc", "")
        if not loc:
            continue
        parsed = urlparse(loc)
        path_groups[parsed.path].append(loc)

    # Find paths with many parameter variants
    proliferation_paths = []
    total_affected = 0
    pattern_examples = set()

    for path, group_urls in path_groups.items():
        if len(group_urls) < _MIN_PROLIFERATION_VARIANTS:
            continue
        # Check if they differ by query params
        params_seen = set()
        for u in group_urls:
            parsed = urlparse(u)
            qs = parse_qs(parsed.query)
            for param in qs:
                if param.lower() not in TRACKING_PARAMS:
                    params_seen.add(param)
        if params_seen:
            proliferation_paths.append(path)
            total_affected += len(group_urls)
            for p in list(params_seen)[:3]:
                pattern_examples.add(f"?{p}=...")

    return {
        "detected": len(proliferation_paths) > 0,
        "affected_paths": proliferation_paths[:10],
        "pattern_examples": list(pattern_examples)[:5],
        "affected_url_count": total_affected,
    }


def detect_duplicate_urls(urls: list) -> dict:
    """Detect duplicate URLs in the sitemap."""
    locs = [normalize_url(u["loc"]) for u in urls if u.get("loc")]
    counter = Counter(locs)
    duplicates = {url: count for url, count in counter.items() if count > 1}
    return {
        "duplicate_count": len(duplicates),
        "duplicate_examples": list(duplicates.keys())[:5],
    }


def discover_sitemaps(base_url: str, provided_urls: list = None) -> list:
    """Discover sitemap URLs from robots.txt or standard paths."""
    discovered = []

    if provided_urls:
        discovered.extend(provided_urls)
        return discovered

    # Try robots.txt first
    robots_url = urljoin(base_url, "/robots.txt")
    try:
        resp = requests.get(robots_url, timeout=10,
                            headers={"User-Agent": "BrandAIReadinessAudit/1.0"})
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                line_clean = line.split("#")[0].strip()
                match = re.match(r"^sitemap\s*:\s*(.+)$", line_clean, re.IGNORECASE)
                if match:
                    discovered.append(match.group(1).strip())
    except Exception:
        pass

    # Try standard paths as fallback
    if not discovered:
        for path in ["/sitemap.xml", "/sitemap_index.xml"]:
            url = urljoin(base_url, path)
            try:
                resp = requests.head(url, timeout=10, allow_redirects=True,
                                     headers={"User-Agent": "BrandAIReadinessAudit/1.0"})
                if resp.status_code == 200:
                    discovered.append(url)
            except Exception:
                pass

    return discovered


def analyze(url: str, sitemap_urls: list = None, sample_size: int = DEFAULT_SAMPLE_SIZE) -> dict:
    """Main analysis: discover, fetch, parse, and validate sitemaps."""
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    robots_groups = None
    try:
        robots_response = requests.get(urljoin(base_url, "/robots.txt"), timeout=10,
                                        headers={"User-Agent": "BrandAIReadinessAudit/1.0"})
        if robots_response.status_code == 200:
            robots_groups = parse_robots_txt(robots_response.text)
    except requests.RequestException:
        pass

    result = {
        "sitemaps_found": [],
        "declared_in_robots_txt": False,
        "total_urls_across_sitemaps": 0,
        "duplicates": {"duplicate_count": 0, "duplicate_examples": []},
        "parameter_proliferation": {"detected": False},
        "errors": [],
        "page_urls": [],
        "robots_policy_loaded": robots_groups is not None,
    }

    # Discover sitemaps
    if sitemap_urls:
        discovered = sitemap_urls
    else:
        discovered = discover_sitemaps(base_url)
        # Check if any were from robots.txt
        robots_url = urljoin(base_url, "/robots.txt")
        try:
            resp = requests.get(robots_url, timeout=10,
                                headers={"User-Agent": "BrandAIReadinessAudit/1.0"})
            if resp.status_code == 200:
                for line in resp.text.splitlines():
                    if re.match(r"^sitemap\s*:", line.strip(), re.IGNORECASE):
                        result["declared_in_robots_txt"] = True
                        break
        except Exception:
            pass

    if not discovered:
        result["errors"].append("No sitemap found via robots.txt or standard paths (/sitemap.xml, /sitemap_index.xml)")
        return result

    # Process sitemaps (with sub-sitemap expansion)
    all_urls = []
    queue = list(discovered)
    processed = set()
    sitemap_count = 0

    while queue and sitemap_count < MAX_SITEMAPS:
        sm_url = queue.pop(0)
        if sm_url in processed:
            continue
        processed.add(sm_url)
        sitemap_count += 1

        fetch_result = fetch_sitemap(sm_url)
        sitemap_entry = {
            "url": sm_url,
            "type": None,
            "status": fetch_result["status"],
            "xml_valid": True,
            "url_count": 0,
            "content_length_bytes": fetch_result["content_length"],
            "lastmod_analysis": None,
            "sample_checks": [],
            "errors": [],
        }

        if fetch_result["error"]:
            sitemap_entry["errors"].append(fetch_result["error"])
            sitemap_entry["xml_valid"] = False
            result["sitemaps_found"].append(sitemap_entry)
            continue

        if fetch_result["status"] != 200:
            sitemap_entry["errors"].append(f"HTTP {fetch_result['status']}")
            sitemap_entry["xml_valid"] = False
            result["sitemaps_found"].append(sitemap_entry)
            continue

        # Parse XML
        parsed_sm = parse_sitemap_xml(fetch_result["content"], sm_url)
        sitemap_entry["type"] = parsed_sm["type"]

        if parsed_sm["xml_errors"]:
            sitemap_entry["xml_valid"] = False
            sitemap_entry["errors"].extend(parsed_sm["xml_errors"])

        if parsed_sm["type"] == "sitemapindex":
            # Queue sub-sitemaps
            for sub_url in parsed_sm["sub_sitemaps"]:
                if sub_url not in processed:
                    queue.append(sub_url)
            sitemap_entry["url_count"] = len(parsed_sm["sub_sitemaps"])
        elif parsed_sm["type"] == "urlset":
            sitemap_entry["url_count"] = len(parsed_sm["urls"])
            all_urls.extend(parsed_sm["urls"])

            # Check if oversized
            if len(parsed_sm["urls"]) > _MAX_URLS_PER_FILE:
                sitemap_entry["errors"].append(
                    f"Sitemap contains {len(parsed_sm['urls'])} URLs — exceeds the {_MAX_URLS_PER_FILE:,} URL limit per sitemap file."
                )

            # Lastmod analysis
            sitemap_entry["lastmod_analysis"] = check_lastmod_freshness(parsed_sm["urls"])

            # Sample check
            sitemap_entry["sample_checks"] = sample_check_urls(parsed_sm["urls"], sample_size, robots_groups)

        result["sitemaps_found"].append(sitemap_entry)

        if len(all_urls) >= MAX_URLS_TO_PARSE:
            result["errors"].append(f"URL count exceeded {MAX_URLS_TO_PARSE}; stopped parsing additional sitemaps.")
            break

    # Aggregate
    result["total_urls_across_sitemaps"] = len(all_urls)
    result["page_urls"] = sorted({normalize_url(entry["loc"]) for entry in all_urls if entry.get("loc")})
    result["duplicates"] = detect_duplicate_urls(all_urls)
    result["parameter_proliferation"] = detect_parameter_proliferation(all_urls)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Fetch, parse, and validate XML sitemaps."
    )
    parser.add_argument("--url", required=True, help="Site root URL (e.g., https://example.com)")
    parser.add_argument("--sitemap-urls", default=None,
                        help="Comma-separated sitemap URLs (overrides auto-discovery)")
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE,
                        help=f"Number of URLs to sample-check for HTTP status (default: {DEFAULT_SAMPLE_SIZE})")
    args = parser.parse_args()

    sitemap_urls = None
    if args.sitemap_urls:
        sitemap_urls = [u.strip() for u in args.sitemap_urls.split(",") if u.strip()]

    result = analyze(args.url, sitemap_urls, args.sample_size)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
