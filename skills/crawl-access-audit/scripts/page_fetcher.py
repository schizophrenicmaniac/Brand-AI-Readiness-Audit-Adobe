#!/usr/bin/env python3
"""page_fetcher.py — Core fetch audit for crawl-access-audit.

Part of the crawl-access-audit skill in the Brand AI Readiness Audit marketplace.
Fetches a list of pages and records HTTP status, redirect chains, meta robots,
X-Robots-Tag, canonical tags, response latency, challenge-page detection, and
TLS certificate health. Also performs a lightweight BFS crawl for depth analysis.

Usage:
    python3 page_fetcher.py --url https://example.com
    python3 page_fetcher.py --url https://example.com --pages /products,/pricing,/about
    python3 page_fetcher.py --url https://example.com --max-pages 30 --max-depth 3

Output: JSON to stdout.
"""

import argparse
import hashlib
import json
import os
import re
import ssl
import socket
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin, urldefrag
from url_utils import normalize_url
from robots_analyzer import parse_robots_txt, evaluate_bot

try:
    import requests
except ImportError:
    print(json.dumps({"error": "Missing dependency: requests. Install with: pip install requests"}), file=sys.stderr)
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
# Load SSOT reference files:
# references/challenge-signatures.json → signatures & soft-404 patterns
# references/crawl-config.json         → page fetch & BFS limits
# ---------------------------------------------------------------------------
_sig_data = _load_json("challenge-signatures.json")
_config = _load_json("crawl-config.json")["page_fetch"]

DEFAULT_MAX_PAGES = _config["default_max_pages"]
DEFAULT_MAX_DEPTH = _config["default_max_depth"]
DEFAULT_TIMEOUT = _config["default_timeout_seconds"]
_TTFB_THRESHOLDS = _config.get("ttfb_thresholds", {"critical_seconds": 10, "high_seconds": 5, "medium_seconds": 3})
_CERT_EXPIRY_WARNING_DAYS = _config.get("cert_expiry_warning_days", 30)

CHALLENGE_SIGNATURES = _sig_data["challenge_signatures"]
SOFT_404_PATTERNS = _sig_data["soft_404_patterns"]
SOFT_404_TITLE_INDICATORS = _sig_data.get("soft_404_title_indicators", ["404", "not found", "error", "page missing"])
_SOFT_404_MIN_BODY_CHARS = _sig_data.get("soft_404_min_body_chars", 200)
_JS_REDIRECT_MAX_BODY_CHARS = _sig_data.get("js_redirect_max_body_chars", 1500)
_JS_REDIRECT_MIN_TEXT_CHARS = _sig_data.get("js_redirect_min_text_chars", 100)


# ---------------------------------------------------------------------------
# HTML Parser for extracting meta tags, canonical, title, and links
# ---------------------------------------------------------------------------
class PageMetaParser(HTMLParser):
    """Extract meta robots, canonical, title, and internal links from HTML."""

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.base_domain = urlparse(base_url).netloc.lower()
        self.meta_robots = None
        self.canonical = None
        self.canonical_count = 0
        self.title = None
        self.links = set()
        self._in_title = False
        self._title_parts = []
        self._in_body = False
        self._body_text_parts = []
        self._body_text_len = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = {k.lower(): v for k, v in attrs}

        if tag == "meta":
            name = attrs_dict.get("name", "").lower()
            content = attrs_dict.get("content", "")
            if name == "robots":
                self.meta_robots = content

        elif tag == "link":
            rel = attrs_dict.get("rel", "").lower()
            href = attrs_dict.get("href", "")
            if rel == "canonical" and href:
                self.canonical_count += 1
                self.canonical = urljoin(self.base_url, href)
            # Pagination and language alternates are crawl-discovery hints, not
            # content extraction. Include same-host targets in the frontier.
            if href and any(token in rel.split() for token in ("next", "prev", "alternate")):
                candidate = urljoin(self.base_url, href)
                if urlparse(candidate).netloc.lower() == self.base_domain:
                    self.links.add(urldefrag(candidate)[0])

        elif tag == "title":
            self._in_title = True

        elif tag == "a":
            href = attrs_dict.get("href", "")
            if href:
                abs_url = urljoin(self.base_url, href)
                abs_url, _ = urldefrag(abs_url)
                parsed = urlparse(abs_url)
                if parsed.netloc.lower() == self.base_domain and parsed.scheme in ("http", "https"):
                    self.links.add(abs_url)

        elif tag == "body":
            self._in_body = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
            self.title = "".join(self._title_parts).strip()

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)
        if self._in_body:
            stripped = data.strip()
            if stripped:
                self._body_text_len += len(stripped)

    def get_body_text_length(self) -> int:
        return self._body_text_len


# ---------------------------------------------------------------------------
# TLS Certificate Check
# ---------------------------------------------------------------------------
def check_tls(hostname: str, port: int = 443) -> dict:
    """Check TLS certificate validity for a hostname."""
    result = {
        "valid": False,
        "expires": None,
        "days_until_expiry": None,
        "hostname_match": False,
        "error": None,
    }
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                # Expiry
                not_after = cert.get("notAfter", "")
                if not_after:
                    expiry_dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                    expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
                    result["expires"] = expiry_dt.strftime("%Y-%m-%d")
                    result["days_until_expiry"] = (expiry_dt - datetime.now(timezone.utc)).days
                # Hostname match (if we got here without error, it matches)
                result["hostname_match"] = True
                result["valid"] = True
    except ssl.SSLCertVerificationError as e:
        result["error"] = f"Certificate verification failed: {str(e)[:200]}"
        if "hostname mismatch" in str(e).lower():
            result["hostname_match"] = False
    except ssl.SSLError as e:
        result["error"] = f"SSL error: {str(e)[:200]}"
    except socket.timeout:
        result["error"] = "TLS connection timeout"
    except Exception as e:
        result["error"] = f"TLS check error: {str(e)[:200]}"

    return result


# ---------------------------------------------------------------------------
# Challenge Page Detection
# ---------------------------------------------------------------------------
def detect_challenge_page(body: str, headers: dict, status_code: int) -> dict:
    """Detect if a response is a bot-challenge or CAPTCHA page."""
    body_lower = body.lower() if body else ""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", body_lower, re.DOTALL)
    title_text = title_match.group(1).strip() if title_match else ""
    infrastructure_signals = []

    for provider, sigs in CHALLENGE_SIGNATURES.items():
        # Check title patterns
        for pattern in sigs["title_patterns"]:
            if pattern in title_text:
                return {
                    "detected": True,
                    "provider": provider,
                    "signal": f"Title contains '{pattern}'",
                }
        # Check body patterns
        for pattern in sigs["body_patterns"]:
            if pattern in body_lower:
                return {
                    "detected": True,
                    "provider": provider,
                    "signal": f"Body contains '{pattern}'",
                }
        # Check header patterns
        for header_name, header_val in sigs["header_patterns"].items():
            actual = headers.get(header_name, headers.get(header_name.lower(), ""))
            if actual:
                if header_val is None:
                    infrastructure_signals.append(f"{provider}: header '{header_name}' present")
                elif header_val.lower() in actual.lower():
                    infrastructure_signals.append(f"{provider}: header '{header_name}' contains '{header_val}'")

    # JS-redirect-only check
    if body and len(body) < _JS_REDIRECT_MAX_BODY_CHARS:
        script_match = re.search(r"<script[^>]*>(.*?)</script>", body_lower, re.DOTALL)
        if script_match:
            script_content = script_match.group(1)
            if any(p in script_content for p in ["window.location", "document.location", "location.href"]):
                # Check if there's minimal non-script content
                text_only = re.sub(r"<script[^>]*>.*?</script>", "", body_lower, flags=re.DOTALL)
                text_only = re.sub(r"<[^>]+>", "", text_only).strip()
                if len(text_only) < _JS_REDIRECT_MIN_TEXT_CHARS:
                    return {
                        "detected": True,
                        "provider": "js_redirect",
                        "signal": "Small page with JavaScript-only redirect, no meaningful text content",
                    }

    # CDN/WAF delivery headers are common on healthy pages. They are useful
    # context, but only a challenge-specific title/body/redirect is a block.
    return {"detected": False, "provider": None,
            "signal": "; ".join(infrastructure_signals) if infrastructure_signals else None}


# ---------------------------------------------------------------------------
# Soft-404 Detection
# ---------------------------------------------------------------------------
def detect_soft_404(status_code: int, body: str, title: str) -> dict:
    """Detect soft-404 pages (200 status but functionally a 404)."""
    if status_code != 200:
        return {"detected": False, "rule": None}

    # Strip HTML tags for text length check
    text_only = re.sub(r"<[^>]+>", "", body or "").strip() if body else ""

    # Rule 1: Very short body
    if len(text_only) < _SOFT_404_MIN_BODY_CHARS:
        return {"detected": True, "rule": f"Body text < {_SOFT_404_MIN_BODY_CHARS} characters"}

    # Rule 2: Body contains 404-like patterns
    body_lower = (body or "").lower()
    for pattern in SOFT_404_PATTERNS:
        if pattern in body_lower:
            # Additional check: make sure "404" isn't just in a nav/footer link
            # by verifying it appears in a prominent position (title, h1, or main content)
            if pattern == "not found" and body_lower.count(pattern) == 1:
                # Could be a false positive in a single link — check title
                if title and pattern in title.lower():
                    return {"detected": True, "rule": f"Title contains '{pattern}'"}
                continue
            return {"detected": True, "rule": f"Body contains '{pattern}'"}

    # Rule 3: Title contains 404 patterns
    if title:
        title_lower = title.lower()
        for indicator in SOFT_404_TITLE_INDICATORS:
            if indicator in title_lower:
                return {"detected": True, "rule": f"Title contains '{indicator}'"}

    return {"detected": False, "rule": None}


# ---------------------------------------------------------------------------
# Single Page Fetch
# ---------------------------------------------------------------------------
def fetch_page(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Fetch a single page and extract all crawl-access signals."""
    result = {
        "url": url,
        "final_url": url,
        "status_code": None,
        "redirect_chain": [],
        "redirect_chain_length": 0,
        "ttfb_ms": None,
        "total_time_ms": None,
        "content_length": 0,
        "is_soft_404": False,
        "soft_404_rule": None,
        "meta_robots": None,
        "x_robots_tag": None,
        "canonical": {
            "href": None,
            "matches_request_url": None,
            "target_status": None,
            "count": 0,
        },
        "title": None,
        "challenge_page": {"detected": False, "provider": None, "signal": None},
        "hsts_header": None,
        "error": None,
    }

    try:
        # Use allow_redirects=False to capture the chain manually
        chain = []
        current_url = url
        seen_urls = set()
        start_time = time.time()
        ttfb = None
        final_resp = None

        for hop in range(10):
            if current_url in seen_urls:
                result["error"] = f"Redirect loop detected at {current_url}"
                result["redirect_chain"] = chain
                result["redirect_chain_length"] = len(chain)
                return result
            seen_urls.add(current_url)

            resp = requests.get(
                current_url, timeout=timeout, allow_redirects=False,
                headers={"User-Agent": "BrandAIReadinessAudit/1.0"},
                stream=True
            )

            if ttfb is None:
                ttfb = (time.time() - start_time) * 1000

            if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location", "")
                if location:
                    next_url = urljoin(current_url, location)
                    chain.append({
                        "from": current_url,
                        "to": next_url,
                        "status": resp.status_code,
                    })
                    current_url = next_url
                    continue
            # Read full body
            body = resp.text
            final_resp = resp
            break

        total_time = (time.time() - start_time) * 1000
        result["ttfb_ms"] = round(ttfb, 1)
        result["total_time_ms"] = round(total_time, 1)
        result["redirect_chain"] = chain
        result["redirect_chain_length"] = len(chain)
        result["final_url"] = current_url

        if final_resp is None:
            result["error"] = "Too many redirects (>10 hops)"
            return result

        result["status_code"] = final_resp.status_code
        result["content_length"] = len(final_resp.content)

        # X-Robots-Tag
        x_robots = final_resp.headers.get("X-Robots-Tag")
        result["x_robots_tag"] = x_robots

        # HSTS
        hsts = final_resp.headers.get("Strict-Transport-Security")
        result["hsts_header"] = hsts

        if final_resp.status_code == 200:
            body = final_resp.text

            # Parse HTML for meta, canonical, title, links
            parser = PageMetaParser(current_url)
            try:
                parser.feed(body)
            except Exception:
                pass

            result["meta_robots"] = parser.meta_robots
            result["title"] = parser.title
            result["canonical"]["count"] = parser.canonical_count

            # Canonical
            if parser.canonical:
                result["canonical"]["href"] = parser.canonical
                # Normalize for comparison
                req_normalized = urlparse(current_url)._replace(fragment="").geturl()
                can_normalized = urlparse(parser.canonical)._replace(fragment="").geturl()
                result["canonical"]["matches_request_url"] = (
                    req_normalized.rstrip("/") == can_normalized.rstrip("/")
                )
                # If canonical differs, check target
                if not result["canonical"]["matches_request_url"]:
                    try:
                        can_resp = requests.head(
                            parser.canonical, timeout=10, allow_redirects=True,
                            headers={"User-Agent": "BrandAIReadinessAudit/1.0"}
                        )
                        result["canonical"]["target_status"] = can_resp.status_code
                    except Exception:
                        result["canonical"]["target_status"] = None

            # Soft-404 detection
            soft_404 = detect_soft_404(final_resp.status_code, body, parser.title)
            result["is_soft_404"] = soft_404["detected"]
            result["soft_404_rule"] = soft_404.get("rule")

            # Challenge page detection
            result["challenge_page"] = detect_challenge_page(
                body, dict(final_resp.headers), final_resp.status_code
            )

    except requests.exceptions.Timeout:
        result["error"] = f"Timeout (>{timeout}s)"
        result["ttfb_ms"] = None
        result["total_time_ms"] = timeout * 1000
    except requests.exceptions.SSLError as e:
        result["error"] = f"SSL error: {str(e)[:200]}"
    except requests.exceptions.ConnectionError as e:
        result["error"] = f"Connection error: {str(e)[:200]}"
    except Exception as e:
        result["error"] = f"Error: {str(e)[:200]}"

    return result


# ---------------------------------------------------------------------------
# BFS Crawl for Depth Analysis
# ---------------------------------------------------------------------------
def load_audit_robots(base_url: str) -> list | None:
    """Load the audit client's policy once; unavailable policies are reported, not bypassed."""
    try:
        response = requests.get(urljoin(base_url, "/robots.txt"), timeout=10,
                                headers={"User-Agent": "BrandAIReadinessAudit/1.0"})
        return parse_robots_txt(response.text) if response.status_code == 200 else None
    except requests.RequestException:
        return None


def bfs_crawl(start_url: str, max_pages: int, max_depth: int, robots_groups: list | None = None) -> dict:
    """Breadth-first crawl from start URL to analyze link depth."""
    parsed_start = urlparse(start_url)
    domain = parsed_start.netloc.lower()
    base_url = f"{parsed_start.scheme}://{parsed_start.netloc}"

    visited = set()
    depth_map = {}  # url -> depth
    broken_internal_links = []
    robots_skipped = []
    queue = deque([(start_url, 0)])
    depth_distribution = defaultdict(int)

    while queue and len(visited) < max_pages:
        current_url, depth = queue.popleft()

        current_url = normalize_url(current_url)
        if current_url in visited:
            continue
        if depth > max_depth:
            continue

        visited.add(current_url)
        depth_map[current_url] = depth
        depth_distribution[depth] += 1

        if robots_groups is not None and evaluate_bot(robots_groups, "BrandAIReadinessAudit", urlparse(current_url).path or "/")["blocked"]:
            robots_skipped.append(current_url)
            continue

        try:
            resp = requests.get(
                current_url, timeout=10, allow_redirects=True,
                headers={"User-Agent": "BrandAIReadinessAudit/1.0"}
            )
            if resp.status_code >= 400:
                broken_internal_links.append({
                    "url": current_url,
                    "status": resp.status_code,
                    "depth": depth,
                })
                continue
            if resp.status_code != 200:
                continue

            # Parse links
            parser = PageMetaParser(current_url)
            try:
                parser.feed(resp.text)
            except Exception:
                pass

            for link in parser.links:
                link_parsed = urlparse(link)
                normalized_link = normalize_url(link)
                if link_parsed.netloc.lower() == domain and normalized_link not in visited:
                    queue.append((normalized_link, depth + 1))

        except Exception:
            continue

    # Find deep pages (depth > 3)
    deep_pages = [{"url": url, "depth": d} for url, d in depth_map.items() if d > 3]

    return {
        "pages_discovered": len(visited),
        "discovered_urls": sorted(visited),
        "max_depth_reached": max(depth_distribution.keys()) if depth_distribution else 0,
        "depth_distribution": dict(sorted(depth_distribution.items())),
        "deep_pages": deep_pages[:20],
        "broken_internal_links": broken_internal_links[:20],
        "robots_skipped_urls": robots_skipped[:20],
    }


# ---------------------------------------------------------------------------
# HTTP → HTTPS Check
# ---------------------------------------------------------------------------
def check_http_to_https(hostname: str) -> dict:
    """Check if HTTP redirects to HTTPS."""
    result = {"redirects": False, "final_url": None, "error": None}
    http_url = f"http://{hostname}/"
    try:
        resp = requests.head(http_url, timeout=10, allow_redirects=True,
                             headers={"User-Agent": "BrandAIReadinessAudit/1.0"})
        result["final_url"] = resp.url
        result["redirects"] = resp.url.startswith("https://")
    except Exception as e:
        result["error"] = str(e)[:200]
    return result


# ---------------------------------------------------------------------------
# Main Analysis
# ---------------------------------------------------------------------------
def analyze(url: str, page_paths: list = None,
            max_pages: int = DEFAULT_MAX_PAGES,
            max_depth: int = DEFAULT_MAX_DEPTH,
            sitemap_urls: list = None) -> dict:
    """Main analysis: fetch pages, check TLS, and build a robots-aware crawl graph."""
    parsed = urlparse(url)
    hostname = parsed.hostname or parsed.netloc
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    robots_groups = load_audit_robots(base_url)

    result = {
        "domain": hostname,
        "tls": check_tls(hostname),
        "http_to_https_redirect": check_http_to_https(hostname),
        "pages": [],
        "crawl_graph": {},
        "orphan_pages": {"checked": False, "count": 0, "urls": [], "confidence": "not_checked"},
        "robots_policy_loaded": robots_groups is not None,
    }

    # Determine pages to fetch
    if page_paths:
        page_urls = [urljoin(base_url, p) for p in page_paths]
    else:
        # Default: homepage
        page_urls = [base_url + "/"]

    # Fetch each page
    for page_url in page_urls:
        path = urlparse(page_url).path or "/"
        if robots_groups is not None and evaluate_bot(robots_groups, "BrandAIReadinessAudit", path)["blocked"]:
            page_result = {"url": page_url, "skipped": True, "skip_reason": "Disallowed by robots.txt for BrandAIReadinessAudit"}
        else:
            page_result = fetch_page(page_url)
        result["pages"].append(page_result)

    # BFS crawl from homepage
    result["crawl_graph"] = bfs_crawl(base_url + "/", max_pages, max_depth, robots_groups)

    # Cross-reference normalized sitemap URLs against the link frontier. This is
    # an access/discovery signal only; it makes no judgement about page content.
    if sitemap_urls:
        crawled_urls = set(result["crawl_graph"].get("discovered_urls", []))
        sitemap_set = {normalize_url(item) for item in sitemap_urls if item}
        same_host_sitemap_urls = {item for item in sitemap_set if urlparse(item).netloc.lower() == hostname.lower()}
        orphans = sorted(same_host_sitemap_urls - crawled_urls)
        bounded = result["crawl_graph"]["pages_discovered"] >= max_pages or result["crawl_graph"]["max_depth_reached"] >= max_depth
        result["orphan_pages"] = {
            "checked": True,
            "count": len(orphans),
            "urls": orphans[:50],
            "confidence": "bounded_crawl" if bounded else "high",
            "note": "Potential orphans: navigation generated only after rendering is evaluated by render-extraction-audit.",
        }

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Core fetch audit: status, redirects, canonical, latency, challenge detection, TLS, crawl depth."
    )
    parser.add_argument("--url", required=True, help="Site root URL (e.g., https://example.com)")
    parser.add_argument("--pages", default=None,
                        help="Comma-separated page paths to audit (e.g., /products,/pricing,/about)")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Max pages to crawl in BFS (default: {DEFAULT_MAX_PAGES})")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH,
                        help=f"Max BFS depth (default: {DEFAULT_MAX_DEPTH})")
    parser.add_argument("--sitemap-page-urls", default=None,
                        help="Comma-separated page URLs from sitemap_validator output for orphan comparison")
    parser.add_argument("--sitemap-page-urls-file", default=None,
                        help="JSON file containing sitemap_validator's page_urls array")
    parser.add_argument("--output", default="", help="Optional file path to write JSON output")
    args = parser.parse_args()

    page_paths = None
    if args.pages:
        page_paths = [p.strip() for p in args.pages.split(",") if p.strip()]

    sitemap_page_urls = [item.strip() for item in args.sitemap_page_urls.split(",") if item.strip()] if args.sitemap_page_urls else None
    if args.sitemap_page_urls_file:
        try:
            with open(args.sitemap_page_urls_file, "r", encoding="utf-8") as handle:
                file_data = json.load(handle)
            sitemap_page_urls = file_data.get("page_urls", file_data) if isinstance(file_data, dict) else file_data
            if not isinstance(sitemap_page_urls, list):
                raise ValueError("expected a JSON array or an object with page_urls")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"cannot read --sitemap-page-urls-file: {exc}")
    result = analyze(args.url, page_paths, args.max_pages, args.max_depth, sitemap_page_urls)
    output_json = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
