#!/usr/bin/env python3
"""rendered_dom_extractor.py — Headless browser rendering + DOM extraction.

Part of the render-extraction-audit skill in the Brand AI Readiness Audit marketplace.
Launches a headless browser (Playwright/Chromium) for each page, waits for network
idle, extracts rendered DOM text, detects shadow DOM components, AJAX-loaded content
blocks, interaction-gated content (tabs/accordions), and lazy-loaded images.

Usage:
    python3 rendered_dom_extractor.py --url https://example.com
    python3 rendered_dom_extractor.py --url https://example.com --pages /products,/pricing
    python3 rendered_dom_extractor.py --url https://example.com --max-pages 10

Output: JSON to stdout.

Dependencies:
    pip install playwright
    playwright install chromium
"""

import argparse
import json
import os
import sys
from urllib.parse import urlparse, urljoin

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
except ImportError:
    print(json.dumps({
        "error": "Missing dependency: playwright. Install with: pip install playwright && playwright install chromium",
        "install_instructions": [
            "pip install playwright",
            "playwright install chromium"
        ]
    }))
    sys.exit(1)

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
        print(json.dumps({"error": f"Reference file not found: {path}"}), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON in {path}: {e}"}), file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Load configuration
# ---------------------------------------------------------------------------
_config = _load_json("render-config.json")
_dom_config = _config["rendered_dom"]
_signatures = _load_json("opaque-media-signatures.json")

DEFAULT_MAX_PAGES = _dom_config["default_max_pages"]
PAGE_TIMEOUT_MS = _dom_config["page_timeout_ms"]
NETWORK_IDLE_TIMEOUT_MS = _dom_config["network_idle_timeout_ms"]
DOM_SNAPSHOT_DELAY_MS = _dom_config["dom_snapshot_delay_ms"]
MIN_JS_DEPENDENT_CHARS = _dom_config["min_js_dependent_text_chars"]
SHADOW_DOM_SCAN = _dom_config["shadow_dom_scan_enabled"]


# ---------------------------------------------------------------------------
# JavaScript evaluation snippets (injected into the page context)
# ---------------------------------------------------------------------------

# Extract all visible text from the rendered DOM
JS_EXTRACT_TEXT = """
() => {
    // Get visible text content, excluding script/style/noscript
    const walker = document.createTreeWalker(
        document.body || document.documentElement,
        NodeFilter.SHOW_TEXT,
        {
            acceptNode: function(node) {
                const parent = node.parentElement;
                if (!parent) return NodeFilter.FILTER_REJECT;
                const tag = parent.tagName.toLowerCase();
                if (['script', 'style', 'noscript', 'template'].includes(tag)) {
                    return NodeFilter.FILTER_REJECT;
                }
                // Check if element is visible
                const style = window.getComputedStyle(parent);
                if (style.display === 'none' || style.visibility === 'hidden') {
                    return NodeFilter.FILTER_REJECT;
                }
                if (style.opacity === '0') {
                    return NodeFilter.FILTER_REJECT;
                }
                // Check for off-screen positioning
                const rect = parent.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) {
                    return NodeFilter.FILTER_REJECT;
                }
                return NodeFilter.FILTER_ACCEPT;
            }
        }
    );

    const texts = [];
    let node;
    while (node = walker.nextNode()) {
        const text = node.textContent.trim();
        if (text) {
            texts.push(text);
        }
    }
    return texts.join(' ');
}
"""

# Extract ALL text (including hidden elements) for comparison
JS_EXTRACT_ALL_TEXT = """
() => {
    const walker = document.createTreeWalker(
        document.body || document.documentElement,
        NodeFilter.SHOW_TEXT,
        {
            acceptNode: function(node) {
                const parent = node.parentElement;
                if (!parent) return NodeFilter.FILTER_REJECT;
                const tag = parent.tagName.toLowerCase();
                if (['script', 'style', 'noscript', 'template'].includes(tag)) {
                    return NodeFilter.FILTER_REJECT;
                }
                return NodeFilter.FILTER_ACCEPT;
            }
        }
    );

    const texts = [];
    let node;
    while (node = walker.nextNode()) {
        const text = node.textContent.trim();
        if (text) {
            texts.push(text);
        }
    }
    return texts.join(' ');
}
"""

# Detect shadow DOM components
JS_DETECT_SHADOW_DOM = """
() => {
    const shadowHosts = [];
    const allElements = document.querySelectorAll('*');
    for (const el of allElements) {
        if (el.shadowRoot) {
            const text = el.shadowRoot.textContent || '';
            shadowHosts.push({
                tag: el.tagName.toLowerCase(),
                id: el.id || null,
                className: el.className ? String(el.className).substring(0, 200) : null,
                innerTextLength: text.length,
                innerTextPreview: text.substring(0, 500).trim(),
                childElementCount: el.shadowRoot.childElementCount || 0,
            });
        }
    }
    return shadowHosts;
}
"""

# Detect tab/accordion panels that are hidden (content not visible)
JS_DETECT_HIDDEN_PANELS = """
() => {
    const results = [];

    // Check aria-hidden tabpanels
    const hiddenPanels = document.querySelectorAll(
        '[role="tabpanel"][aria-hidden="true"], ' +
        '[role="tabpanel"][hidden], ' +
        '.tab-pane:not(.active):not(.show), ' +
        '.tab-content > :not(.active), ' +
        '.accordion-collapse:not(.show), ' +
        '.collapse:not(.show), ' +
        '.panel-collapse:not(.in), ' +
        'details:not([open])'
    );

    for (const panel of hiddenPanels) {
        const style = window.getComputedStyle(panel);
        const text = panel.textContent || '';
        results.push({
            tag: panel.tagName.toLowerCase(),
            id: panel.id || null,
            role: panel.getAttribute('role') || null,
            ariaHidden: panel.getAttribute('aria-hidden'),
            cssDisplay: style.display,
            cssVisibility: style.visibility,
            textLength: text.trim().length,
            textPreview: text.trim().substring(0, 300),
            isInDom: true,
            isCssHidden: style.display === 'none' || style.visibility === 'hidden',
        });
    }

    return results;
}
"""

# Detect lazy-loaded images
JS_DETECT_LAZY_IMAGES = """
() => {
    const results = [];
    const images = document.querySelectorAll('img');
    for (const img of images) {
        const isLazy = (
            img.loading === 'lazy' ||
            img.hasAttribute('data-src') ||
            img.hasAttribute('data-lazy') ||
            img.hasAttribute('data-original') ||
            img.classList.contains('lazy') ||
            img.classList.contains('lazyload') ||
            img.classList.contains('lazyloaded') ||
            img.classList.contains('ls-is-cached')
        );
        if (isLazy) {
            results.push({
                src: img.src || img.getAttribute('data-src') || '',
                alt: img.alt || null,
                loading: img.loading || null,
                hasDataSrc: img.hasAttribute('data-src'),
                naturalWidth: img.naturalWidth || 0,
                naturalHeight: img.naturalHeight || 0,
                isLoaded: img.complete && img.naturalWidth > 0,
            });
        }
    }
    return results;
}
"""

# Detect infinite scroll / load-more patterns in rendered DOM
JS_DETECT_LOAD_MORE = """
() => {
    const results = {
        loadMoreButtons: [],
        paginationMeta: [],
        sentinelElements: [],
    };

    // Load more buttons
    const buttons = document.querySelectorAll('button, a, [role="button"]');
    const loadMorePatterns = ['load more', 'show more', 'see more', 'view more',
                              'load all', 'show all', 'view all', 'next page',
                              'load next'];
    for (const btn of buttons) {
        const text = (btn.textContent || '').trim().toLowerCase();
        for (const pattern of loadMorePatterns) {
            if (text.includes(pattern)) {
                results.loadMoreButtons.push({
                    tag: btn.tagName.toLowerCase(),
                    text: text.substring(0, 100),
                    id: btn.id || null,
                });
                break;
            }
        }
    }

    // Pagination metadata
    const paginationEls = document.querySelectorAll(
        '[data-total-pages], [data-page], [data-total-items], ' +
        '[data-current-page], [data-per-page]'
    );
    for (const el of paginationEls) {
        const meta = {};
        for (const attr of el.attributes) {
            if (attr.name.startsWith('data-')) {
                meta[attr.name] = attr.value;
            }
        }
        results.paginationMeta.push(meta);
    }

    // Sentinel / observer elements
    const sentinels = document.querySelectorAll(
        '[data-infinite-scroll], .scroll-sentinel, .infinite-scroll-trigger, ' +
        '.waypoint, [data-load-more], .infinite-loader'
    );
    for (const el of sentinels) {
        results.sentinelElements.push({
            tag: el.tagName.toLowerCase(),
            id: el.id || null,
            className: el.className ? String(el.className).substring(0, 100) : null,
        });
    }

    return results;
}
"""

# Get rendered HTML (truncated)
JS_GET_HTML = """
() => {
    const html = document.documentElement.outerHTML || '';
    return html.substring(0, 500000);
}
"""


# ---------------------------------------------------------------------------
# Text diffing — find content that's in rendered but not in raw
# ---------------------------------------------------------------------------
def compute_text_diff(raw_text: str, rendered_text: str) -> dict:
    """Find text present in rendered DOM but absent from raw HTML.

    Uses a word-set approach: splits both texts into word sets and finds
    words/phrases unique to the rendered version. Returns stats and a preview.
    """
    if not rendered_text:
        return {
            "js_dependent_text": "",
            "js_dependent_text_length": 0,
            "js_dependent_word_count": 0,
            "js_dependent_preview": "",
        }

    raw_words = set(raw_text.lower().split()) if raw_text else set()
    rendered_words = rendered_text.lower().split()

    # Find sequences of words that are new in the rendered version
    js_only_segments = []
    current_segment = []

    for word in rendered_words:
        if word not in raw_words:
            current_segment.append(word)
        else:
            if current_segment:
                segment_text = " ".join(current_segment)
                if len(segment_text) > 3:  # Skip trivial differences
                    js_only_segments.append(segment_text)
                current_segment = []

    if current_segment:
        segment_text = " ".join(current_segment)
        if len(segment_text) > 3:
            js_only_segments.append(segment_text)

    js_dependent_text = " [...] ".join(js_only_segments)

    return {
        "js_dependent_text": js_dependent_text[:20000],
        "js_dependent_text_length": len(js_dependent_text),
        "js_dependent_word_count": sum(len(s.split()) for s in js_only_segments),
        "js_dependent_preview": js_dependent_text[:1000],
    }


# ---------------------------------------------------------------------------
# Single Page Rendering
# ---------------------------------------------------------------------------
def render_page(page, url: str) -> dict:
    """Render a single page in the headless browser and extract DOM data."""
    result = {
        "url": url,
        "error": None,
        "rendered_text": "",
        "rendered_text_length": 0,
        "rendered_all_text": "",
        "rendered_all_text_length": 0,
        "rendered_html_length": 0,
        "shadow_dom_components": [],
        "hidden_panels": [],
        "lazy_loaded_images": [],
        "load_more_patterns": {},
        "dom_content_loaded_text_length": 0,
        "network_idle_text_length": 0,
        "ajax_content_delta": 0,
    }

    try:
        # Navigate and wait for DOMContentLoaded
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)

        # Snapshot at DOMContentLoaded
        try:
            dcl_text = page.evaluate(JS_EXTRACT_ALL_TEXT)
            result["dom_content_loaded_text_length"] = len(dcl_text) if dcl_text else 0
        except Exception:
            result["dom_content_loaded_text_length"] = 0

        # Wait for network idle (additional async content)
        try:
            page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
        except PWTimeoutError:
            pass  # Continue with what we have — some sites never reach network idle

        # Small delay for any final JS rendering
        page.wait_for_timeout(DOM_SNAPSHOT_DELAY_MS)

        # Extract visible text
        try:
            visible_text = page.evaluate(JS_EXTRACT_TEXT)
            result["rendered_text"] = (visible_text or "")[:50000]
            result["rendered_text_length"] = len(visible_text) if visible_text else 0
        except Exception as e:
            result["error"] = f"Text extraction failed: {str(e)[:200]}"

        # Extract all text (including hidden)
        try:
            all_text = page.evaluate(JS_EXTRACT_ALL_TEXT)
            result["rendered_all_text"] = (all_text or "")[:50000]
            result["rendered_all_text_length"] = len(all_text) if all_text else 0
            result["network_idle_text_length"] = len(all_text) if all_text else 0
        except Exception:
            pass

        # AJAX content delta
        result["ajax_content_delta"] = (
            result["network_idle_text_length"] - result["dom_content_loaded_text_length"]
        )

        # Rendered HTML length
        try:
            html = page.evaluate(JS_GET_HTML)
            result["rendered_html_length"] = len(html) if html else 0
        except Exception:
            pass

        # Shadow DOM detection
        if SHADOW_DOM_SCAN:
            try:
                shadow_hosts = page.evaluate(JS_DETECT_SHADOW_DOM)
                result["shadow_dom_components"] = shadow_hosts or []
            except Exception:
                pass

        # Hidden tab/accordion panels
        try:
            hidden_panels = page.evaluate(JS_DETECT_HIDDEN_PANELS)
            result["hidden_panels"] = hidden_panels or []
        except Exception:
            pass

        # Lazy-loaded images
        try:
            lazy_imgs = page.evaluate(JS_DETECT_LAZY_IMAGES)
            result["lazy_loaded_images"] = lazy_imgs or []
        except Exception:
            pass

        # Load more / infinite scroll patterns
        try:
            load_more = page.evaluate(JS_DETECT_LOAD_MORE)
            result["load_more_patterns"] = load_more or {}
        except Exception:
            pass

    except PWTimeoutError:
        result["error"] = f"Page load timeout (>{PAGE_TIMEOUT_MS}ms)"
    except Exception as e:
        result["error"] = f"Rendering error: {str(e)[:300]}"

    return result


# ---------------------------------------------------------------------------
# Main Analysis
# ---------------------------------------------------------------------------
def analyze(url: str, page_paths: list = None,
            max_pages: int = DEFAULT_MAX_PAGES,
            raw_data: dict = None) -> dict:
    """Main analysis: render pages and extract DOM data.

    Args:
        url: Site root URL.
        page_paths: Optional list of specific page paths to audit.
        max_pages: Maximum pages to render.
        raw_data: Optional raw HTML analysis data (from html_fetcher.py) for
                  computing JS-dependent content diff.
    """
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    result = {
        "domain": parsed.netloc,
        "pages": [],
        "browser_info": {},
    }

    # Determine pages to render
    if page_paths:
        page_urls = [urljoin(base_url, p) for p in page_paths[:max_pages]]
    else:
        page_urls = [base_url + "/"]

    # Build raw text lookup for diffing
    raw_text_map = {}
    if raw_data and "pages" in raw_data:
        for rp in raw_data["pages"]:
            raw_text_map[rp["url"]] = rp.get("raw_text", "")

    # Launch browser and render each page
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="BrandAIReadinessAudit/1.0 (Headless Chromium)",
            viewport={"width": 1280, "height": 800},
            java_script_enabled=True,
        )

        result["browser_info"] = {
            "engine": "Chromium (Playwright)",
            "viewport": "1280x800",
            "js_enabled": True,
        }

        for page_url in page_urls:
            page = context.new_page()
            try:
                page_result = render_page(page, page_url)

                # Compute JS-dependent content diff
                raw_text = raw_text_map.get(page_url, "")
                if raw_text or page_result.get("rendered_all_text"):
                    page_result["js_content_diff"] = compute_text_diff(
                        raw_text, page_result.get("rendered_all_text", "")
                    )
                else:
                    page_result["js_content_diff"] = {
                        "js_dependent_text": "",
                        "js_dependent_text_length": 0,
                        "js_dependent_word_count": 0,
                        "js_dependent_preview": "",
                    }

                result["pages"].append(page_result)
            finally:
                page.close()

        context.close()
        browser.close()

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Headless browser rendering + DOM extraction for render-extraction-audit."
    )
    parser.add_argument("--url", required=True, help="Site root URL (e.g., https://example.com)")
    parser.add_argument("--pages", default=None,
                        help="Comma-separated page paths (e.g., /products,/pricing,/about)")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Max pages to render (default: {DEFAULT_MAX_PAGES})")
    parser.add_argument("--raw-data", default=None,
                        help="Path to JSON output from html_fetcher.py (for diff computation)")
    args = parser.parse_args()

    page_paths = None
    if args.pages:
        page_paths = [p.strip() for p in args.pages.split(",") if p.strip()]

    raw_data = None
    if args.raw_data:
        try:
            with open(args.raw_data, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception as e:
            print(json.dumps({"warning": f"Could not load raw data: {e}"}), file=sys.stderr)

    result = analyze(args.url, page_paths, args.max_pages, raw_data)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
