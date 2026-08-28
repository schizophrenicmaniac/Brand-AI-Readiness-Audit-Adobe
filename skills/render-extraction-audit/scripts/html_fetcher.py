#!/usr/bin/env python3
"""html_fetcher.py — Raw HTML fetch and static analysis for render-extraction-audit.

Part of the render-extraction-audit skill in the Brand AI Readiness Audit marketplace.
Fetches each page's raw HTML via simple HTTP GET (no JS execution), extracts visible
text, inventories opaque media (images, SVGs, canvas, video, audio, iframes, PDF
links), detects SPA shell patterns, icon font usage, CSS content-property text,
tab/accordion structures, infinite scroll markers, and computes text-to-boilerplate
ratio.

Usage:
    python3 html_fetcher.py --url https://example.com
    python3 html_fetcher.py --url https://example.com --pages /products,/pricing,/about
    python3 html_fetcher.py --url https://example.com --max-pages 10

Output: JSON to stdout.
"""

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin

try:
    import requests
except ImportError:
    print(json.dumps({"error": "Missing dependency: requests. Install with: pip install requests"}))
    sys.exit(1)

# ---------------------------------------------------------------------------
# Reference file loader — resolves paths relative to this script's location.
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


# ---------------------------------------------------------------------------
# Load SSOT reference files
# ---------------------------------------------------------------------------
_config = _load_json("render-config.json")
_html_config = _config["html_fetch"]
_img_config = _config["image_alt"]
_icon_config = _config["icon_fonts"]
_iframe_suppressed = _config["suppressed_iframe_sources"]
_suppressed_img = _config["suppressed_image_patterns"]

_signatures = _load_json("opaque-media-signatures.json")
_spa_frameworks = _signatures["spa_frameworks"]
_tab_patterns = _signatures["tab_accordion_patterns"]
_scroll_markers = _signatures["infinite_scroll_markers"]
_icon_sigs = _signatures["icon_font_signatures"]

_key_facts = _load_json("key-fact-patterns.json")

DEFAULT_MAX_PAGES = _html_config["default_max_pages"]
DEFAULT_TIMEOUT = _html_config["default_timeout_seconds"]
SPA_SHELL_MAX_CHARS = _html_config["spa_shell_max_body_text_chars"]
BOILERPLATE_THRESHOLD = _html_config["boilerplate_ratio_threshold"]
MIN_MAIN_CONTENT_CHARS = _html_config["min_main_content_chars"]
PDF_SUMMARY_MIN_CHARS = _html_config["pdf_summary_min_chars"]
MIN_NOSCRIPT_CHARS = _html_config["min_noscript_quality_chars"]


# ---------------------------------------------------------------------------
# HTML Parser — extracts text, media inventory, structural elements
# ---------------------------------------------------------------------------
class RenderAuditParser(HTMLParser):
    """Parse raw HTML to extract text content and media inventory."""

    # Tags whose text content is boilerplate / non-main-content
    BOILERPLATE_TAGS = {"nav", "header", "footer", "aside"}
    # Tags that indicate main content area
    MAIN_CONTENT_TAGS = {"main", "article"}
    # Tags to skip for text extraction (script, style, etc.)
    SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
    # Void elements (self-closing)
    VOID_ELEMENTS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr"
    }

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.base_domain = urlparse(base_url).netloc.lower()

        # Text extraction
        self._tag_stack = []
        self._in_skip_tag = 0
        self._in_boilerplate = 0
        self._in_main_content = 0
        self._full_text_parts = []
        self._main_text_parts = []
        self._boilerplate_text_parts = []

        # Noscript content
        self._in_noscript = False
        self._noscript_parts = []

        # Title
        self._in_title = False
        self._title_parts = []
        self.title = None

        # Media inventory
        self.images = []
        self.svgs = []
        self.canvases = []
        self.videos = []
        self.audios = []
        self.iframes = []
        self.pdf_links = []

        # Structural detection
        self.tabs_accordions = []
        self.infinite_scroll_markers = []
        self.icon_font_elements = []

        # CSS content property
        self.css_content_declarations = []

        # SPA detection
        self.script_srcs = []
        self.has_noscript = False
        self.container_divs = []

        # Style blocks for CSS analysis
        self._in_style = False
        self._style_parts = []

        # Figcaption tracking
        self._in_figcaption = False
        self._figcaption_text = []
        self._current_figure_images = []
        self._in_figure = False

        # Current context for PDF link analysis
        self._current_link_href = None
        self._current_link_text_parts = []
        self._surrounding_text_buffer = []

    def _current_tag(self):
        return self._tag_stack[-1] if self._tag_stack else None

    def handle_starttag(self, tag, attrs):
        attrs_dict = {k.lower(): v for k, v in attrs if k}
        tag_lower = tag.lower()

        self._tag_stack.append(tag_lower)

        # Track skip tags
        if tag_lower in self.SKIP_TAGS:
            self._in_skip_tag += 1

        # Track noscript
        if tag_lower == "noscript":
            self._in_noscript = True
            self.has_noscript = True

        # Track boilerplate areas
        if tag_lower in self.BOILERPLATE_TAGS:
            self._in_boilerplate += 1

        # Track main content areas
        role = attrs_dict.get("role", "").lower()
        if tag_lower in self.MAIN_CONTENT_TAGS or role == "main":
            self._in_main_content += 1

        # Title
        if tag_lower == "title":
            self._in_title = True

        # Style block
        if tag_lower == "style":
            self._in_style = True
            self._style_parts = []

        # Figure tracking
        if tag_lower == "figure":
            self._in_figure = True
            self._current_figure_images = []
        if tag_lower == "figcaption":
            self._in_figcaption = True
            self._figcaption_text = []

        # --- Media inventory ---

        # Images
        if tag_lower == "img":
            img_info = self._analyze_image(attrs_dict)
            self.images.append(img_info)
            if self._in_figure:
                self._current_figure_images.append(img_info)

        # SVG (just detect presence — detailed text extraction needs rendered DOM)
        if tag_lower == "svg":
            self.svgs.append({
                "has_title": False,
                "has_desc": False,
                "has_text_elements": False,
                "aria_label": attrs_dict.get("aria-label"),
                "role": attrs_dict.get("role"),
                "context": "inline",
            })

        # Canvas
        if tag_lower == "canvas":
            self.canvases.append({
                "id": attrs_dict.get("id"),
                "class": attrs_dict.get("class"),
                "aria_label": attrs_dict.get("aria-label"),
                "role": attrs_dict.get("role"),
                "width": attrs_dict.get("width"),
                "height": attrs_dict.get("height"),
                "has_fallback_content": False,  # Updated when we see content inside
            })

        # Video
        if tag_lower == "video":
            self.videos.append({
                "src": attrs_dict.get("src"),
                "poster": attrs_dict.get("poster"),
                "autoplay": "autoplay" in attrs_dict or attrs_dict.get("autoplay") is not None,
                "muted": "muted" in attrs_dict or attrs_dict.get("muted") is not None,
                "loop": "loop" in attrs_dict or attrs_dict.get("loop") is not None,
                "controls": "controls" in attrs_dict or attrs_dict.get("controls") is not None,
                "has_track_captions": False,
                "has_track_subtitles": False,
                "has_transcript_link": False,
                "is_background": False,
            })

        # Audio
        if tag_lower == "audio":
            self.audios.append({
                "src": attrs_dict.get("src"),
                "has_track_captions": False,
                "has_transcript_link": False,
            })

        # Track elements (inside video/audio)
        if tag_lower == "track":
            kind = attrs_dict.get("kind", "").lower()
            if kind == "captions":
                if self.videos:
                    self.videos[-1]["has_track_captions"] = True
                if self.audios:
                    self.audios[-1]["has_track_captions"] = True
            elif kind == "subtitles":
                if self.videos:
                    self.videos[-1]["has_track_subtitles"] = True

        # Source elements (inside video/audio)
        if tag_lower == "source":
            src = attrs_dict.get("src", "")
            if self.videos and not self.videos[-1]["src"]:
                self.videos[-1]["src"] = src
            elif self.audios and not self.audios[-1]["src"]:
                self.audios[-1]["src"] = src

        # Iframes
        if tag_lower == "iframe":
            iframe_src = attrs_dict.get("src", "")
            self.iframes.append({
                "src": iframe_src,
                "title": attrs_dict.get("title"),
                "is_same_origin": self._is_same_origin(iframe_src),
                "is_suppressed": self._is_suppressed_iframe(iframe_src),
                "loading": attrs_dict.get("loading"),
            })

        # Links (for PDF detection and transcript detection)
        if tag_lower == "a":
            href = attrs_dict.get("href", "")
            self._current_link_href = href
            self._current_link_text_parts = []

            # PDF links
            if href and self._is_pdf_link(href):
                self.pdf_links.append({
                    "href": urljoin(self.base_url, href),
                    "link_text": "",  # Filled on endtag
                    "surrounding_text": "",  # Approximated
                    "has_html_summary": False,
                })

            # Transcript links for video/audio
            if href and self._is_transcript_link(href, attrs_dict):
                if self.videos:
                    self.videos[-1]["has_transcript_link"] = True
                if self.audios:
                    self.audios[-1]["has_transcript_link"] = True

        # Script sources (for SPA detection)
        if tag_lower == "script":
            src = attrs_dict.get("src", "")
            if src:
                self.script_srcs.append(src)

        # Container divs (for SPA detection)
        if tag_lower == "div":
            div_id = attrs_dict.get("id", "")
            if div_id in ("root", "app", "__next", "__nuxt", "___gatsby", "__remix"):
                self.container_divs.append(div_id)

        # Tab/accordion detection
        self._check_tab_accordion(tag_lower, attrs_dict)

        # Infinite scroll detection
        self._check_infinite_scroll(tag_lower, attrs_dict)

        # Icon font detection
        self._check_icon_font(tag_lower, attrs_dict)

        # Check for role on main content containers
        if role in ("navigation", "banner", "contentinfo", "complementary"):
            self._in_boilerplate += 1

    def handle_endtag(self, tag):
        tag_lower = tag.lower()

        # Pop tag stack
        if self._tag_stack and self._tag_stack[-1] == tag_lower:
            self._tag_stack.pop()

        # Track skip tags
        if tag_lower in self.SKIP_TAGS:
            self._in_skip_tag = max(0, self._in_skip_tag - 1)

        # Noscript
        if tag_lower == "noscript":
            self._in_noscript = False

        # Track boilerplate
        if tag_lower in self.BOILERPLATE_TAGS:
            self._in_boilerplate = max(0, self._in_boilerplate - 1)

        # Track main content
        role = ""  # Can't easily get role on endtag, track via tag stack
        if tag_lower in self.MAIN_CONTENT_TAGS:
            self._in_main_content = max(0, self._in_main_content - 1)

        # Title
        if tag_lower == "title":
            self._in_title = False
            self.title = "".join(self._title_parts).strip()

        # Style block — parse for CSS content declarations
        if tag_lower == "style":
            self._in_style = False
            style_text = "".join(self._style_parts)
            self._parse_css_content(style_text)

        # Figure / figcaption
        if tag_lower == "figcaption":
            self._in_figcaption = False
            caption_text = "".join(self._figcaption_text).strip()
            # Associate caption with figure images
            for img in self._current_figure_images:
                img["figcaption"] = caption_text
        if tag_lower == "figure":
            self._in_figure = False
            self._current_figure_images = []

        # Link end — finalize PDF link text
        if tag_lower == "a":
            if self.pdf_links and self._current_link_href and self._is_pdf_link(self._current_link_href):
                self.pdf_links[-1]["link_text"] = "".join(self._current_link_text_parts).strip()
            self._current_link_href = None
            self._current_link_text_parts = []

        # Canvas end — check if it had fallback content
        if tag_lower == "canvas" and self.canvases:
            # Fallback detection is handled in handle_data when inside canvas
            pass

        # Boilerplate role tracking
        if tag_lower in ("div", "section", "aside"):
            # Simplified — we tracked on starttag, should untrack here too
            pass

    def handle_data(self, data):
        stripped = data.strip()
        if not stripped:
            return

        # Title
        if self._in_title:
            self._title_parts.append(data)
            return

        # Noscript
        if self._in_noscript:
            self._noscript_parts.append(data)
            return

        # Style block
        if self._in_style:
            self._style_parts.append(data)
            return

        # Skip script/template/svg content for text extraction
        if self._in_skip_tag > 0:
            return

        # Figcaption
        if self._in_figcaption:
            self._figcaption_text.append(data)

        # Link text
        if self._current_link_href is not None:
            self._current_link_text_parts.append(data)

        # Canvas fallback content
        if self._tag_stack and self._tag_stack[-1] == "canvas" and self.canvases:
            self.canvases[-1]["has_fallback_content"] = True

        # Text extraction
        self._full_text_parts.append(stripped)

        if self._in_main_content > 0:
            self._main_text_parts.append(stripped)
        elif self._in_boilerplate > 0:
            self._boilerplate_text_parts.append(stripped)
        else:
            # Not explicitly in main or boilerplate — count as potential main content
            self._main_text_parts.append(stripped)

        # Surrounding text buffer for PDF context
        self._surrounding_text_buffer.append(stripped)
        if len(self._surrounding_text_buffer) > 10:
            self._surrounding_text_buffer.pop(0)

    # --- Analysis helpers ---

    def _analyze_image(self, attrs: dict) -> dict:
        """Analyze a single <img> element for alt text quality."""
        src = attrs.get("src", "")
        alt = attrs.get("alt")
        aria_label = attrs.get("aria-label")
        role = attrs.get("role", "")
        aria_hidden = attrs.get("aria-hidden", "").lower()
        width = attrs.get("width", "")
        height = attrs.get("height", "")
        loading = attrs.get("loading", "")
        data_src = attrs.get("data-src", "")
        srcset = attrs.get("srcset", "")

        # Determine if decorative
        is_decorative = (
            role in ("presentation", "none") or
            aria_hidden == "true" or
            self._is_tracking_pixel(src, width, height)
        )

        # Alt quality assessment
        alt_quality = "missing"
        if alt is not None:
            if alt == "":
                alt_quality = "empty" if not is_decorative else "empty_decorative"
            elif self._is_generic_alt(alt):
                alt_quality = "generic"
            elif self._is_filename_alt(alt):
                alt_quality = "filename"
            elif len(alt) < _img_config["min_descriptive_alt_chars"]:
                alt_quality = "too_short"
            elif len(alt) > _img_config["max_alt_chars"]:
                alt_quality = "too_long"
            else:
                alt_quality = "descriptive"

        return {
            "src": src or data_src,
            "alt": alt,
            "alt_quality": alt_quality,
            "aria_label": aria_label,
            "figcaption": None,  # Set by figure/figcaption handler
            "is_decorative": is_decorative,
            "is_lazy": loading == "lazy" or bool(data_src),
            "role": role,
            "width": width,
            "height": height,
        }

    def _is_generic_alt(self, alt: str) -> bool:
        """Check if alt text matches generic patterns."""
        alt_lower = alt.strip().lower()
        for pattern in _img_config["generic_alt_patterns"]:
            if alt_lower == pattern or alt_lower == pattern + "s":
                return True
        return False

    def _is_filename_alt(self, alt: str) -> bool:
        """Check if alt text looks like a filename."""
        return bool(re.search(_img_config["filename_alt_regex"], alt.strip(), re.IGNORECASE))

    def _is_tracking_pixel(self, src: str, width: str, height: str) -> bool:
        """Check if image is a tracking pixel or spacer."""
        src_lower = (src or "").lower()
        for pattern in _suppressed_img["src_substrings"]:
            if pattern in src_lower:
                return True
        try:
            max_px = _suppressed_img["dimensional_max_px"]
            w = int(width) if width and width.isdigit() else 9999
            h = int(height) if height and height.isdigit() else 9999
            if w <= max_px and h <= max_px:
                return True
        except (ValueError, TypeError):
            pass
        return False

    def _is_same_origin(self, src: str) -> bool:
        """Check if an iframe src is same-origin."""
        if not src or src.startswith("about:") or src.startswith("javascript:"):
            return False
        try:
            parsed = urlparse(urljoin(self.base_url, src))
            return parsed.netloc.lower() == self.base_domain
        except Exception:
            return False

    def _is_suppressed_iframe(self, src: str) -> bool:
        """Check if iframe src matches suppressed patterns (analytics, ads, etc.)."""
        src_lower = (src or "").lower()
        for pattern in _iframe_suppressed:
            if pattern in src_lower:
                return True
        return False

    def _is_pdf_link(self, href: str) -> bool:
        """Check if a link points to a PDF."""
        href_lower = (href or "").lower().split("?")[0].split("#")[0]
        return href_lower.endswith(".pdf")

    def _is_transcript_link(self, href: str, attrs: dict) -> bool:
        """Check if a link is likely a transcript link."""
        href_lower = (href or "").lower()
        text_hints = ["transcript", "caption", "subtitle"]
        for hint in text_hints:
            if hint in href_lower:
                return True
        aria_label = (attrs.get("aria-label", "") or "").lower()
        title = (attrs.get("title", "") or "").lower()
        for hint in text_hints:
            if hint in aria_label or hint in title:
                return True
        return False

    def _check_tab_accordion(self, tag: str, attrs: dict):
        """Detect tab/accordion patterns."""
        role = attrs.get("role", "").lower()
        class_str = attrs.get("class", "")
        aria_hidden = attrs.get("aria-hidden", "")
        aria_expanded = attrs.get("aria-expanded", "")

        # ARIA role detection
        if role in _tab_patterns["aria_roles"]:
            self.tabs_accordions.append({
                "type": "tab" if role in ("tablist", "tab") else "tabpanel",
                "detection": f"role=\"{role}\"",
                "tag": tag,
                "id": attrs.get("id"),
                "aria_hidden": aria_hidden,
                "aria_expanded": aria_expanded,
                "content_in_dom": True,  # If we see the element, content is in raw DOM
            })
            return

        # CSS class detection
        if class_str:
            class_lower = class_str.lower()
            for pattern in _tab_patterns["css_classes"]:
                if pattern.lower() in class_lower:
                    self.tabs_accordions.append({
                        "type": "tab_accordion",
                        "detection": f"class contains '{pattern}'",
                        "tag": tag,
                        "id": attrs.get("id"),
                        "aria_hidden": aria_hidden,
                        "aria_expanded": aria_expanded,
                        "content_in_dom": True,
                    })
                    return

        # HTML5 details/summary
        if tag in _tab_patterns["html5_elements"]:
            self.tabs_accordions.append({
                "type": "details_summary",
                "detection": f"<{tag}> element",
                "tag": tag,
                "id": attrs.get("id"),
                "open": "open" in attrs,
                "content_in_dom": True,
            })

        # Data attribute detection
        for da_pattern in _tab_patterns.get("data_attributes", []):
            key, val = da_pattern.split("=", 1) if "=" in da_pattern else (da_pattern, None)
            key = key.strip()
            if val:
                val = val.strip().strip('"').strip("'")
            actual = attrs.get(key, "")
            if val and actual == val:
                self.tabs_accordions.append({
                    "type": "tab_accordion",
                    "detection": f'{key}="{val}"',
                    "tag": tag,
                    "id": attrs.get("id"),
                    "content_in_dom": True,
                })
                return
            elif not val and key in attrs:
                self.tabs_accordions.append({
                    "type": "tab_accordion",
                    "detection": f'has attribute {key}',
                    "tag": tag,
                    "id": attrs.get("id"),
                    "content_in_dom": True,
                })
                return

    def _check_infinite_scroll(self, tag: str, attrs: dict):
        """Detect infinite scroll / load-more markers."""
        class_str = (attrs.get("class", "") or "").lower()
        # Data attribute detection
        for da in _scroll_markers["data_attributes"]:
            if da in attrs or da.replace("-", "_") in attrs:
                self.infinite_scroll_markers.append({
                    "detection": f"data attribute '{da}'",
                    "tag": tag,
                    "id": attrs.get("id"),
                })
                return

        # Class detection
        for cls in _scroll_markers["css_classes"]:
            if cls.lower() in class_str:
                self.infinite_scroll_markers.append({
                    "detection": f"class contains '{cls}'",
                    "tag": tag,
                    "id": attrs.get("id"),
                })
                return

    def _check_icon_font(self, tag: str, attrs: dict):
        """Detect icon font usage."""
        class_str = (attrs.get("class", "") or "").lower()
        if not class_str:
            return
        for lib_name, lib_info in _icon_sigs.items():
            for cls in lib_info["classes"]:
                if cls.lower() in class_str:
                    self.icon_font_elements.append({
                        "library": lib_name,
                        "detection": f"class contains '{cls}'",
                        "tag": tag,
                        "class": attrs.get("class", ""),
                        "ligature_risk": lib_info.get("ligature_risk", False),
                    })
                    return

    def _parse_css_content(self, css_text: str):
        """Extract meaningful CSS content property declarations from style text."""
        # Match content: "..." or content: '...' declarations
        pattern = r'content\s*:\s*["\']([^"\']+)["\']'
        matches = re.finditer(pattern, css_text, re.IGNORECASE)
        for match in matches:
            content_val = match.group(1).strip()
            # Skip empty, decorative-only, or counter/quotes content
            if not content_val or content_val in ("", " ", "none", "normal"):
                continue
            # Skip pure symbol content (bullets, arrows, etc.)
            if len(content_val) <= 2 and not content_val.isalpha():
                continue
            # Skip common decorative patterns
            if content_val in ("•", "→", "←", "↑", "↓", "×", "✓", "✗",
                               "▸", "▾", "▴", "◂", "—", "–", "|", "/",
                               "\\", "·", "*", "»", "«"):
                continue
            # If it has actual words, it's meaningful
            if re.search(r'[a-zA-Z]{2,}', content_val):
                self.css_content_declarations.append({
                    "content": content_val,
                    "context": css_text[max(0, match.start() - 80):match.start()].strip(),
                })

    # --- Result assembly ---

    def get_full_text(self) -> str:
        return " ".join(self._full_text_parts)

    def get_main_text(self) -> str:
        return " ".join(self._main_text_parts)

    def get_boilerplate_text(self) -> str:
        return " ".join(self._boilerplate_text_parts)

    def get_noscript_content(self) -> str:
        return " ".join(self._noscript_parts).strip()

    def get_text_ratio(self) -> dict:
        """Compute text-to-boilerplate ratio."""
        full_len = len(self.get_full_text())
        main_len = len(self.get_main_text())

        if full_len == 0:
            return {
                "full_text_length": 0,
                "main_content_length": 0,
                "boilerplate_length": 0,
                "ratio": 0.0,
                "main_content_identified": False,
            }

        return {
            "full_text_length": full_len,
            "main_content_length": main_len,
            "boilerplate_length": len(self.get_boilerplate_text()),
            "ratio": round(main_len / full_len, 3) if full_len > 0 else 0.0,
            "main_content_identified": main_len > MIN_MAIN_CONTENT_CHARS,
        }


# ---------------------------------------------------------------------------
# SPA Framework Detection
# ---------------------------------------------------------------------------
def detect_spa_framework(html_text: str, script_srcs: list, container_divs: list) -> dict:
    """Detect SPA framework from raw HTML markers."""
    html_lower = html_text.lower()
    detected = []

    for fw_name, fw_info in _spa_frameworks.items():
        score = 0
        signals = []

        # Check HTML markers
        for marker in fw_info["html_markers"]:
            if marker.lower() in html_lower:
                score += 2
                signals.append(f"HTML: {marker}")

        # Check script sources
        for marker in fw_info.get("script_markers", []):
            for src in script_srcs:
                if marker.lower() in src.lower():
                    score += 1
                    signals.append(f"Script: {marker}")
                    break

        # Check meta markers
        for marker in fw_info.get("meta_markers", []):
            if marker.lower() in html_lower:
                score += 1
                signals.append(f"Meta: {marker}")

        if score >= 2:
            # Check for SSR indicators
            has_ssr = False
            for ssr_marker in fw_info.get("ssr_indicators", []):
                if ssr_marker.lower() in html_lower:
                    has_ssr = True
                    break

            detected.append({
                "framework": fw_info["framework_label"],
                "confidence": "high" if score >= 3 else "medium",
                "signals": signals[:5],
                "has_ssr_indicators": has_ssr,
            })

    return {
        "detected": len(detected) > 0,
        "frameworks": detected,
    }


# ---------------------------------------------------------------------------
# Key Fact Detection in Text
# ---------------------------------------------------------------------------
def detect_key_facts_in_text(text: str) -> list:
    """Detect key fact categories present in a text string."""
    if not text:
        return []

    text_lower = text.lower()
    found = []

    for cat_name, cat_info in _key_facts["categories"].items():
        for pattern in cat_info["patterns"]:
            try:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    found.append({
                        "category": cat_name,
                        "label": cat_info["label"],
                        "severity_if_opaque": cat_info["severity_if_opaque"],
                        "severity_if_js_only": cat_info["severity_if_js_only"],
                    })
                    break  # One match per category is enough
            except re.error:
                # Pattern might not be valid regex, try literal match
                if pattern.lower() in text_lower:
                    found.append({
                        "category": cat_name,
                        "label": cat_info["label"],
                        "severity_if_opaque": cat_info["severity_if_opaque"],
                        "severity_if_js_only": cat_info["severity_if_js_only"],
                    })
                    break

    return found


# ---------------------------------------------------------------------------
# Video Background Detection
# ---------------------------------------------------------------------------
def is_background_video(video_info: dict) -> bool:
    """Determine if a video is likely used as a background/ambiance element."""
    return (
        video_info.get("autoplay", False) and
        video_info.get("muted", False) and
        video_info.get("loop", False) and
        not video_info.get("controls", False)
    )


# ---------------------------------------------------------------------------
# Single Page Analysis
# ---------------------------------------------------------------------------
def analyze_page(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Fetch and analyze a single page's raw HTML."""
    result = {
        "url": url,
        "status_code": None,
        "error": None,
        "title": None,
        "raw_text": "",
        "raw_text_length": 0,
        "raw_html_length": 0,
        "noscript_content": "",
        "noscript_content_length": 0,
        "spa_detection": {"detected": False},
        "text_ratio": {},
        "images": [],
        "svgs": [],
        "canvases": [],
        "videos": [],
        "audios": [],
        "iframes": [],
        "pdf_links": [],
        "tabs_accordions": [],
        "infinite_scroll_markers": [],
        "icon_font_elements": [],
        "css_content_declarations": [],
        "key_facts_in_raw_text": [],
    }

    try:
        resp = requests.get(
            url, timeout=timeout, allow_redirects=True,
            headers={"User-Agent": "BrandAIReadinessAudit/1.0"}
        )
        result["status_code"] = resp.status_code

        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result

        html_text = resp.text
        result["raw_html_length"] = len(html_text)

        # Parse HTML
        parser = RenderAuditParser(url)
        try:
            parser.feed(html_text)
        except Exception as e:
            result["error"] = f"HTML parse error: {str(e)[:200]}"
            # Continue with what we got

        # Text extraction
        result["title"] = parser.title
        raw_text = parser.get_full_text()
        result["raw_text"] = raw_text[:50000]  # Cap for output size
        result["raw_text_length"] = len(raw_text)

        # Noscript
        noscript = parser.get_noscript_content()
        result["noscript_content"] = noscript[:5000]
        result["noscript_content_length"] = len(noscript)

        # SPA detection
        result["spa_detection"] = detect_spa_framework(
            html_text, parser.script_srcs, parser.container_divs
        )
        # Check if it's a shell (very little text content)
        result["spa_detection"]["is_shell"] = (
            result["raw_text_length"] < SPA_SHELL_MAX_CHARS and
            len(parser.script_srcs) > 0 and
            len(parser.container_divs) > 0
        )
        result["spa_detection"]["has_noscript"] = parser.has_noscript
        result["spa_detection"]["noscript_quality"] = (
            "adequate" if len(noscript) >= MIN_NOSCRIPT_CHARS
            else "insufficient" if parser.has_noscript
            else "missing"
        )

        # Text ratio
        result["text_ratio"] = parser.get_text_ratio()

        # Media inventory
        result["images"] = parser.images
        result["svgs"] = parser.svgs
        result["canvases"] = parser.canvases

        # Video — mark background videos
        for v in parser.videos:
            v["is_background"] = is_background_video(v)
        result["videos"] = parser.videos
        result["audios"] = parser.audios
        result["iframes"] = parser.iframes
        result["pdf_links"] = parser.pdf_links

        # Structural detection
        result["tabs_accordions"] = parser.tabs_accordions
        result["infinite_scroll_markers"] = parser.infinite_scroll_markers
        result["icon_font_elements"] = parser.icon_font_elements

        # CSS content declarations
        result["css_content_declarations"] = parser.css_content_declarations

        # Key fact detection in raw text
        result["key_facts_in_raw_text"] = detect_key_facts_in_text(raw_text)

    except requests.exceptions.Timeout:
        result["error"] = f"Timeout (>{timeout}s)"
    except requests.exceptions.SSLError as e:
        result["error"] = f"SSL error: {str(e)[:200]}"
    except requests.exceptions.ConnectionError as e:
        result["error"] = f"Connection error: {str(e)[:200]}"
    except Exception as e:
        result["error"] = f"Error: {str(e)[:200]}"

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def analyze(url: str, page_paths: list = None, max_pages: int = DEFAULT_MAX_PAGES) -> dict:
    """Main analysis: fetch and analyze pages."""
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    result = {
        "domain": parsed.netloc,
        "pages": [],
    }

    # Determine pages to fetch
    if page_paths:
        page_urls = [urljoin(base_url, p) for p in page_paths[:max_pages]]
    else:
        # Default: homepage only (orchestrator provides pages)
        page_urls = [base_url + "/"]

    # Analyze each page
    for page_url in page_urls:
        page_result = analyze_page(page_url)
        result["pages"].append(page_result)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Raw HTML fetch and static analysis for render-extraction-audit."
    )
    parser.add_argument("--url", required=True, help="Site root URL (e.g., https://example.com)")
    parser.add_argument("--pages", default=None,
                        help="Comma-separated page paths to audit (e.g., /products,/pricing,/about)")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Max pages to analyze (default: {DEFAULT_MAX_PAGES})")
    args = parser.parse_args()

    page_paths = None
    if args.pages:
        page_paths = [p.strip() for p in args.pages.split(",") if p.strip()]

    result = analyze(args.url, page_paths, args.max_pages)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
