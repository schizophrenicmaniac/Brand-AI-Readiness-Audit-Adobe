#!/usr/bin/env python3
"""render_analyzer.py — turn render-extraction raw JSON into contract findings.

Consumes html_fetcher.analyze() output (raw static analysis) and, when available,
rendered_dom_extractor.analyze() output (headless-browser DOM). Emits RE-01…RE-14
findings in the shared report contract.

Degrades gracefully: with no rendered data (Playwright absent) it runs the static
heuristics only — SPA shell, opaque media, iframes, infinite scroll, boilerplate ratio —
and skips checks that require a browser diff (exact JS-only text, shadow DOM).

Import compile_findings(raw, rendered) from the orchestrator, or run standalone.
"""

import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.normpath(os.path.join(_SCRIPTS_DIR, "..", "..", "..", "lib"))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from report import make_finding  # noqa: E402

SKILL = "render-extraction-audit"

_CONTENT_CANVAS_HINTS = ("chart", "graph", "product", "config", "map", "diagram", "plot")

# Third-party widgets (cookie-consent banners, chat launchers) commonly inject text via
# JS that is absent from the raw HTML. That text is not page content, so it must not
# escalate the JS-rendering-gap finding to HIGH. Strip it before key-fact detection.
_WIDGET_NOISE = re.compile(
    r"(we use cookies|accept (all )?cookies|cookie (policy|preferences|settings|consent|notice)|"
    r"manage (your )?(cookies|preferences)|privacy preferences|your privacy|consent|gdpr|"
    r"how can we help|chat with us|live chat|start (a )?chat|message us|we'?re (online|here)|"
    r"leave a message|powered by (intercom|drift|zendesk|tawk|crisp|hubspot))",
    re.IGNORECASE)


def _strip_widget_noise(text: str) -> str:
    return _WIDGET_NOISE.sub(" ", text or "")


def _detect_key_facts(text):
    """Reuse html_fetcher's key-fact detector when importable; else no escalation."""
    try:
        from html_fetcher import detect_key_facts_in_text
        return detect_key_facts_in_text(text or "")
    except Exception:
        return []


def compile_findings(raw=None, rendered=None) -> list:
    findings = []

    def add(check_id, severity, title, summary, url, source, observed, locator="", expected=None):
        findings.append(make_finding(
            skill=SKILL, check_id=check_id, severity=severity, title=title,
            action_summary=summary, evidence_url=url, evidence_source=source,
            evidence_observed=observed, evidence_locator=locator, evidence_expected=expected))

    raw = raw or {}
    rendered_by_url = {}
    if rendered and rendered.get("pages"):
        for rp in rendered["pages"]:
            rendered_by_url[rp.get("url")] = rp

    for page in raw.get("pages", []):
        url = page.get("url", "")
        if page.get("status_code") != 200 or page.get("error"):
            continue
        rp = rendered_by_url.get(url)

        # RE-02 SPA shell (static)
        spa = page.get("spa_detection", {})
        if spa.get("is_shell"):
            nq = spa.get("noscript_quality", "missing")
            fw = ", ".join(f.get("framework", "?") for f in spa.get("frameworks", [])) or "JS framework"
            sev = "high" if nq in ("missing", "insufficient") else "medium"
            add("RE-02-shell", sev, "Page is a JS shell with little server-rendered content",
                "Server-render or pre-render the page (SSR/SSG), or provide an adequate <noscript> "
                "fallback, so crawlers without JS can read the content.",
                url, "html",
                f"{fw}; raw text {page.get('raw_text_length', 0)} chars; noscript: {nq}",
                locator="body")

        # RE-01 JS rendering gap (needs rendered DOM)
        if rp:
            diff = rp.get("js_content_diff", {})
            jd_len = diff.get("js_dependent_text_length", 0)
            if jd_len > 50:
                # Ignore consent/chat-widget text before deciding whether real key facts
                # (pricing, contact, specs) are JS-only. Only escalate to HIGH when a
                # substantial amount of JS-only text carries key facts — a short banner does not.
                cleaned_preview = _strip_widget_noise(diff.get("js_dependent_preview", ""))
                key_facts = _detect_key_facts(cleaned_preview)
                sev = "high" if (key_facts and jd_len >= 200) else "medium"
                fact_note = f" key facts: {', '.join(sorted({k['label'] for k in key_facts}))}" if key_facts else ""
                add("RE-01-jsgap", sev, "Content appears only after JavaScript execution",
                    "Ensure important content is present in the initial HTML (SSR/SSG) so non-JS "
                    "crawlers can extract it, not just browsers.",
                    url, "rendered_dom",
                    f"{jd_len} chars only in rendered DOM.{fact_note} e.g. '{diff.get('js_dependent_preview', '')[:160]}'",
                    locator="rendered vs raw diff")

            # RE-03 AJAX-loaded content
            if rp.get("ajax_content_delta", 0) > 500:
                add("RE-03-ajax", "medium", "Large content block loads late via AJAX",
                    "Render or inline late-loading content so crawlers that don't wait for network "
                    "idle still capture it.",
                    url, "rendered_dom",
                    f"{rp['ajax_content_delta']} chars arrived after DOMContentLoaded",
                    locator="networkidle delta")

            # RE-11 shadow DOM with content
            for sh in rp.get("shadow_dom_components", []):
                if sh.get("innerTextLength", 0) > 50:
                    add("RE-11-shadow", "medium", "Key content lives inside Shadow DOM",
                        "Expose content outside the shadow tree (light DOM / SSR) so simple "
                        "extractors can read it.",
                        url, "rendered_dom",
                        f"<{sh.get('tag')}> shadowRoot has {sh['innerTextLength']} chars",
                        locator=sh.get("tag", "web-component"))
                    break  # one is enough per page

        # RE-04 images without text alternative (aggregate per page)
        content_imgs = [i for i in page.get("images", [])
                        if not i.get("is_decorative")
                        and i.get("alt_quality") in ("missing", "empty", "generic", "filename")]
        if content_imgs:
            no_facts = not page.get("key_facts_in_raw_text")
            sev = "high" if (len(content_imgs) >= 3 and no_facts) else "medium"
            sample = ", ".join((i.get("src") or "?")[:60] for i in content_imgs[:3])
            add("RE-04-img-alt", sev,
                f"{len(content_imgs)} content image(s) lack usable text alternatives",
                "Add descriptive alt text (or a text equivalent) to content-bearing images; if the "
                "image carries facts (e.g. a pricing table), also provide those facts as HTML text.",
                url, "html", f"alt missing/generic on: {sample}", locator="img[alt]")

        # RE-06 canvas without fallback
        content_canvas = []
        for c in page.get("canvases", []):
            if c.get("has_fallback_content"):
                continue
            ident = f"{c.get('id') or ''} {c.get('class') or ''}".lower()
            content_canvas.append((c, any(h in ident for h in _CONTENT_CANVAS_HINTS)))
        if content_canvas:
            has_content_hint = any(flag for _, flag in content_canvas)
            add("RE-06-canvas", "high" if has_content_hint else "medium",
                f"{len(content_canvas)} <canvas> element(s) with no text fallback",
                "Provide fallback content inside <canvas> (or an adjacent text/table equivalent) so "
                "the data is machine-readable.",
                url, "html", f"canvas ids/classes: " +
                ", ".join((c.get('id') or c.get('class') or 'canvas') for c, _ in content_canvas[:3]),
                locator="canvas")

        # RE-07 video/audio without transcript
        media_issues = []
        for v in page.get("videos", []):
            if not v.get("is_background") and not v.get("has_track_captions") and not v.get("has_transcript_link"):
                media_issues.append(("video", v.get("src") or "?"))
        for a in page.get("audios", []):
            if not a.get("has_track_captions") and not a.get("has_transcript_link"):
                media_issues.append(("audio", a.get("src") or "?"))
        if media_issues:
            add("RE-07-media", "high",
                f"{len(media_issues)} video/audio element(s) without captions or transcript",
                "Add a captions/subtitles <track> and a text transcript so information spoken or "
                "shown in media is machine-readable.",
                url, "html",
                "; ".join(f"{k}: {s[:60]}" for k, s in media_issues[:3]), locator="video/audio")

        # RE-12 iframes with content
        content_iframes = [f for f in page.get("iframes", []) if not f.get("is_suppressed") and f.get("src")]
        if content_iframes:
            cross = [f for f in content_iframes if not f.get("is_same_origin")]
            sev = "medium" if cross else "low"
            add("RE-12-iframe", sev,
                f"{len(content_iframes)} content iframe(s) embed material from elsewhere",
                "Ensure iframe content is also available as crawlable HTML on the page, or link to an "
                "indexable source; cross-origin iframe text is not attributed to this page.",
                url, "html",
                "; ".join((f.get("src") or "?")[:60] for f in content_iframes[:3]), locator="iframe[src]")

        # RE-13 infinite scroll / load-more
        markers = page.get("infinite_scroll_markers", [])
        load_more = (rp or {}).get("load_more_patterns", {}) if rp else {}
        lm_count = len(load_more.get("loadMoreButtons", [])) + len(load_more.get("sentinelElements", []))
        if markers or lm_count:
            add("RE-13-infinite", "medium", "Listing content is gated behind scroll/Load-More",
                "Provide crawlable pagination (linked page URLs) so all listing items are reachable "
                "without executing scroll/click behavior.",
                url, "html" if markers else "rendered_dom",
                f"{len(markers)} static marker(s), {lm_count} rendered load-more/sentinel element(s)",
                locator="infinite-scroll")

        # RE-14 signal-to-noise
        tr = page.get("text_ratio", {})
        full = tr.get("full_text_length", 0)
        if full > 500:  # don't judge ratio on near-empty/shell pages
            if tr.get("main_content_identified") is False:
                add("RE-14-nomains", "medium", "No identifiable main content region",
                    "Wrap primary content in <main>/<article> so extractors can separate it from "
                    "navigation, headers, and footers.",
                    url, "html", f"main content {tr.get('main_content_length', 0)}/{full} chars",
                    locator="main")
            elif isinstance(tr.get("ratio"), (int, float)) and tr["ratio"] < 0.30:
                add("RE-14-boilerplate", "low", "Boilerplate dominates the page text",
                    "Increase unique main-content density; heavy nav/footer/legal boilerplate dilutes "
                    "the extractable signal.",
                    url, "html", f"main content ratio {tr['ratio']:.0%}", locator="main")

        # RE-09 icon-font ligature / CSS pseudo-text noise (low)
        ligature = [e for e in page.get("icon_font_elements", []) if e.get("ligature_risk")]
        css_text = page.get("css_content_declarations", [])
        if ligature or css_text:
            bits = []
            if ligature:
                bits.append(f"{len(ligature)} ligature icon element(s)")
            if css_text:
                bits.append(f"{len(css_text)} CSS content: declaration(s)")
            add("RE-09-pseudotext", "low", "Text conveyed via icon-font ligatures / CSS content",
                "Avoid conveying real words through icon ligatures or CSS ::before/::after content; "
                "put them in HTML text where extractors can read them.",
                url, "html", "; ".join(bits), locator="::before / icon font")

        # RE-08 PDF-only facts (low unless link text hints key facts)
        pdfs = page.get("pdf_links", [])
        if pdfs:
            fact_words = ("price", "pricing", "spec", "menu", "report", "datasheet", "brochure", "catalog")
            hot = [p for p in pdfs if any(w in (p.get("link_text") or "").lower() for w in fact_words)]
            sev = "medium" if hot else "low"
            add("RE-08-pdf", sev,
                f"{len(pdfs)} PDF link(s) may hold facts not summarized in HTML",
                "Summarize key PDF facts as HTML on the linking page so crawlers don't have to parse "
                "the PDF to extract them.",
                url, "html",
                "; ".join((p.get("link_text") or p.get("href") or "?")[:60] for p in (hot or pdfs)[:3]),
                locator="a[href$=.pdf]")

    return findings


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="Compile render-extraction findings from raw JSON or a live URL.")
    ap.add_argument("--url", help="Run html_fetcher (and rendered DOM if available) for this site root")
    ap.add_argument("--pages", help="Comma-separated page paths (with --url)")
    ap.add_argument("--raw-file")
    ap.add_argument("--rendered-file")
    ap.add_argument("--output")
    args = ap.parse_args()

    raw = rendered = None
    if args.url:
        import html_fetcher
        paths = [p.strip() for p in args.pages.split(",")] if args.pages else None
        raw = html_fetcher.analyze(args.url, paths)
        try:
            import rendered_dom_extractor  # optional; needs Playwright
            rendered = rendered_dom_extractor.analyze(args.url, paths, raw_data=raw)
        except SystemExit:
            rendered = None  # Playwright missing → static-only
        except Exception:
            rendered = None
    if args.raw_file:
        raw = _load(args.raw_file)
    if args.rendered_file:
        rendered = _load(args.rendered_file)

    findings = compile_findings(raw, rendered)
    report = {"skill": SKILL, "rendered_available": rendered is not None,
              "total_findings": len(findings), "findings": findings}
    out = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()
