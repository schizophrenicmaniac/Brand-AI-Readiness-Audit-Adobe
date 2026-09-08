#!/usr/bin/env python3
"""freshness_extractor.py — Extract date signals, staleness markers, entity identity, and knowledge graph presence.

Part of the freshness-corroboration-audit skill in the Brand AI Readiness Audit marketplace.
Fetches pages, extracts HTTP and DOM temporal metadata, scans for staleness cues and dead outbound links,
compiles core entity claims, checks Wikipedia/Wikidata grounding, and generates search templates for corroboration.

Usage:
    python freshness_extractor.py --url https://example.com
    python freshness_extractor.py --url https://example.com --pages /,/about,/pricing --max-pages 10
    python freshness_extractor.py --url https://example.com --output /tmp/freshness-raw.json

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


_CONFIG = _load_json("freshness-config.json")

_USER_AGENT = _CONFIG.get("extraction", {}).get(
    "user_agent",
    "BrandAIReadinessAudit/1.0 (+https://github.com/brand-ai-readiness-audit)",
)
_DEFAULT_TIMEOUT = _CONFIG.get("extraction", {}).get("default_timeout_seconds", 15)
_DEFAULT_MAX_PAGES = _CONFIG.get("extraction", {}).get("default_max_pages", 10)


def clean_json_string(raw_str: str) -> str:
    """Clean common invalid formatting in JSON-LD strings."""
    text = raw_str.strip()
    if text.startswith("<!--"):
        text = re.sub(r"^<!--\s*", "", text)
        text = re.sub(r"\s*-->$", "", text)
    if text.startswith("<![CDATA["):
        text = re.sub(r"^<!\[CDATA\[\s*", "", text)
        text = re.sub(r"\s*\]\]>$", "", text)
    return text.strip()


def parse_date_string(date_str: str):
    """Attempt to parse common date formats into a datetime object."""
    if not date_str or not isinstance(date_str, str):
        return None
    date_str = date_str.strip()
    # Normalize ISO 8601 with Z or offset
    iso_clean = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", date_str)
    iso_formats = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
    ]
    for fmt in iso_formats:
        try:
            return datetime.strptime(iso_clean, fmt)
        except Exception:
            pass

    # Try year-month-day substring extraction
    ymd_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", date_str)
    if ymd_match:
        try:
            return datetime(int(ymd_match.group(1)), int(ymd_match.group(2)), int(ymd_match.group(3)))
        except Exception:
            pass

    return None


def extract_json_ld_entities(soup: BeautifulSoup) -> list:
    """Extract and flatten all JSON-LD entities from the page."""
    entities = []
    scripts = soup.find_all("script", type="application/ld+json")
    for script in scripts:
        raw_content = script.string or script.get_text() or ""
        cleaned = clean_json_string(raw_content)
        if not cleaned:
            continue
        try:
            data = json.loads(cleaned)
            entities.extend(_flatten_entities(data))
        except Exception:
            pass
    return entities


def _flatten_entities(data) -> list:
    """Recursively extract entities with @type."""
    items = []
    if isinstance(data, dict):
        if "@graph" in data and isinstance(data["@graph"], list):
            for entry in data["@graph"]:
                items.extend(_flatten_entities(entry))
        else:
            if "@type" in data:
                items.append(data)
            for k, v in data.items():
                if k not in ("@type", "@context"):
                    items.extend(_flatten_entities(v))
    elif isinstance(data, list):
        for entry in data:
            items.extend(_flatten_entities(entry))
    return items


def extract_date_signals(resp: requests.Response, soup: BeautifulSoup, entities: list) -> dict:
    """Extract temporal signals from HTTP headers, meta tags, schema, and visible content."""
    date_cfg = _CONFIG.get("date_detection", {})
    signals = {
        "http_headers": {},
        "meta_dates": [],
        "schema_dates": [],
        "visible_dates": [],
        "parsed_dates": [],
    }

    # 1. HTTP Headers
    for header in date_cfg.get("http_headers", ["last-modified", "date"]):
        val = resp.headers.get(header)
        if val:
            signals["http_headers"][header] = val
            dt = parse_date_string(val)
            if dt:
                signals["parsed_dates"].append({"source": f"header:{header}", "raw": val, "iso": dt.isoformat(), "timestamp": dt.timestamp()})

    # 2. Meta Tags
    target_meta_names = set(m.lower() for m in date_cfg.get("meta_tags", []))
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").strip().lower()
        if name in target_meta_names:
            content = meta.get("content", "").strip()
            if content:
                signals["meta_dates"].append({"name": name, "value": content})
                dt = parse_date_string(content)
                if dt:
                    signals["parsed_dates"].append({"source": f"meta:{name}", "raw": content, "iso": dt.isoformat(), "timestamp": dt.timestamp()})

    # 3. Schema.org dates
    target_props = date_cfg.get("schema_properties", ["datePublished", "dateModified", "uploadDate", "releaseDate"])
    for ent in entities:
        ent_type = ent.get("@type", "Entity")
        for prop in target_props:
            val = ent.get(prop)
            if val and isinstance(val, str):
                signals["schema_dates"].append({"type": str(ent_type), "property": prop, "value": val})
                dt = parse_date_string(val)
                if dt:
                    signals["parsed_dates"].append({"source": f"schema:{ent_type}.{prop}", "raw": val, "iso": dt.isoformat(), "timestamp": dt.timestamp()})

    # 4. Visible content dates
    body = soup.find("body")
    body_text = body.get_text(separator=" ", strip=True) if body else ""
    date_regexes = date_cfg.get("visible_date_regexes", [
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
    ])
    matched_dates = set()
    for pattern in date_regexes:
        found = re.findall(pattern, body_text, re.IGNORECASE)
        for d in found:
            matched_dates.add(d.strip())

    for d in list(matched_dates)[:8]:
        signals["visible_dates"].append(d)
        dt = parse_date_string(d)
        if dt:
            signals["parsed_dates"].append({"source": "visible_text", "raw": d, "iso": dt.isoformat(), "timestamp": dt.timestamp()})

    # Determine freshest date
    if signals["parsed_dates"]:
        freshest = max(signals["parsed_dates"], key=lambda x: x["timestamp"])
        signals["freshest_date"] = freshest
    else:
        signals["freshest_date"] = None

    signals["has_any_date_signal"] = bool(signals["parsed_dates"])
    return signals


def extract_staleness_markers(soup: BeautifulSoup, page_url: str, session: requests.Session, check_dead_links: bool = True) -> dict:
    """Scan visible text for outdated pricing, future-tense past events, stale copyright, and dead links."""
    markers = {
        "outdated_pricing_keywords": [],
        "future_past_conflicts": [],
        "stale_copyright": None,
        "dead_outbound_links": [],
    }

    body = soup.find("body")
    text = body.get_text(separator=" ", strip=True) if body else ""
    text_lower = text.lower()

    # Outdated pricing keywords
    pricing_keywords = _CONFIG.get("staleness_signals", {}).get("outdated_pricing_keywords", [])
    for kw in pricing_keywords:
        if kw in text_lower:
            markers["outdated_pricing_keywords"].append(kw)

    # Future-tense past years
    future_past_patterns = _CONFIG.get("staleness_signals", {}).get("future_tense_past_years", [])
    for pattern in future_past_patterns:
        if pattern in text_lower:
            markers["future_past_conflicts"].append(pattern)

    # Stale copyright check
    current_year = datetime.now().year
    copyright_match = re.findall(r"(?:©|&copy;|copyright)\s*(?:20\d{2}\s*[-–]\s*)?(20\d{2})\b", text, re.IGNORECASE)
    if copyright_match:
        years = [int(y) for y in copyright_match]
        max_cr_year = max(years)
        if max_cr_year < (current_year - 1):
            markers["stale_copyright"] = {
                "detected_year": max_cr_year,
                "current_year": current_year,
                "years_behind": current_year - max_cr_year,
            }

    # Dead outbound link checking (sample up to max_links)
    dead_link_cfg = _CONFIG.get("staleness_signals", {}).get("dead_link_check", {})
    if check_dead_links and dead_link_cfg.get("enabled", True):
        max_links = dead_link_cfg.get("max_links_per_page", 15)
        timeout = dead_link_cfg.get("timeout_seconds", 5)
        parsed_page = urlparse(page_url)

        outbound_links = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            if not href.startswith("http"):
                continue
            parsed_href = urlparse(href)
            if parsed_href.netloc and parsed_href.netloc.lower() != parsed_page.netloc.lower():
                if href not in outbound_links:
                    outbound_links.append(href)
            if len(outbound_links) >= max_links:
                break

        for link in outbound_links:
            try:
                # Use HEAD with fallback to GET stream to check link health quickly
                head_resp = session.head(link, timeout=timeout, allow_redirects=True)
                if head_resp.status_code in (404, 410):
                    markers["dead_outbound_links"].append({
                        "url": link,
                        "status_code": head_resp.status_code,
                        "error": "HTTP status indicates page removed/not found"
                    })
                elif head_resp.status_code >= 500:
                    markers["dead_outbound_links"].append({
                        "url": link,
                        "status_code": head_resp.status_code,
                        "error": "Server error on external link"
                    })
            except requests.exceptions.RequestException as req_err:
                markers["dead_outbound_links"].append({
                    "url": link,
                    "status_code": 0,
                    "error": str(req_err)
                })

    return markers


def extract_entity_data(soup: BeautifulSoup, entities: list, base_url: str) -> dict:
    """Extract brand name, sameAs links, NAP information, and core claims."""
    entity_data = {
        "brand_name": "",
        "sameAs": [],
        "nap": {
            "name": "",
            "telephone": "",
            "address": {}
        },
        "claims": {
            "founding_year": None,
            "hq_location": None,
            "leadership": [],
            "flagship_products": [],
            "pricing_sample": []
        }
    }

    # Brand name extraction from multiple signals
    brand_candidates = []
    og_site_name = soup.find("meta", property="og:site_name")
    if og_site_name and og_site_name.get("content"):
        brand_candidates.append(og_site_name["content"].strip())

    title_tag = soup.find("title")
    if title_tag and title_tag.get_text():
        t = title_tag.get_text().strip()
        parts = re.split(r"[-|:–•]", t)
        if len(parts) > 1:
            brand_candidates.append(parts[-1].strip())
            brand_candidates.append(parts[0].strip())
        else:
            brand_candidates.append(t)

    # Extract from JSON-LD entities
    for ent in entities:
        etype = ent.get("@type", "")
        types = [etype] if isinstance(etype, str) else (etype if isinstance(etype, list) else [])

        if any(t in ("Organization", "Corporation", "LocalBusiness", "WebSite") for t in types):
            if ent.get("name") and not entity_data["brand_name"]:
                entity_data["brand_name"] = ent["name"].strip()

            same_as = ent.get("sameAs", [])
            if isinstance(same_as, str):
                entity_data["sameAs"].append(same_as)
            elif isinstance(same_as, list):
                entity_data["sameAs"].extend([s for s in same_as if isinstance(s, str)])

            if ent.get("telephone"):
                entity_data["nap"]["telephone"] = ent["telephone"]
            if ent.get("address"):
                entity_data["nap"]["address"] = ent["address"]

            # Claims: Founding date
            if ent.get("foundingDate"):
                entity_data["claims"]["founding_year"] = str(ent["foundingDate"])

            # Claims: Founder / Leadership
            for lead_key in ("founder", "founders", "employee", "alumni"):
                leader = ent.get(lead_key)
                if isinstance(leader, dict) and leader.get("name"):
                    entity_data["claims"]["leadership"].append(leader["name"])
                elif isinstance(leader, list):
                    for l in leader:
                        if isinstance(l, dict) and l.get("name"):
                            entity_data["claims"]["leadership"].append(l["name"])
                        elif isinstance(l, str):
                            entity_data["claims"]["leadership"].append(l)

        if "Product" in types:
            pname = ent.get("name")
            if pname and pname not in entity_data["claims"]["flagship_products"]:
                entity_data["claims"]["flagship_products"].append(pname)
            offers = ent.get("offers")
            if isinstance(offers, dict) and offers.get("price"):
                entity_data["claims"]["pricing_sample"].append(f"{offers.get('price')} {offers.get('priceCurrency', 'USD')}")

    # Fallback brand name if JSON-LD had none
    if not entity_data["brand_name"] and brand_candidates:
        entity_data["brand_name"] = brand_candidates[0]
    if not entity_data["brand_name"]:
        parsed = urlparse(base_url)
        entity_data["brand_name"] = parsed.netloc.replace("www.", "").split(".")[0].capitalize()

    entity_data["nap"]["name"] = entity_data["brand_name"]

    # Visible text regex fallbacks for claims if schema missing
    body = soup.find("body")
    text = body.get_text(separator=" ", strip=True) if body else ""

    if not entity_data["claims"]["founding_year"]:
        f_match = re.search(r"\b(?:founded|established|started)\s+(?:in\s+)?(19\d{2}|20\d{2})\b", text, re.IGNORECASE)
        if f_match:
            entity_data["claims"]["founding_year"] = f_match.group(1)

    if not entity_data["claims"]["hq_location"]:
        hq_match = re.search(r"\b(?:headquartered|headquarters|based)\s+in\s+([A-Z][a-zA-Z\s,]+(?:USA|CA|UK|NY|Germany|France|India|Japan|Australia)?)\b", text)
        if hq_match:
            entity_data["claims"]["hq_location"] = hq_match.group(1).strip()[:40]

    # Deduplicate sameAs links
    entity_data["sameAs"] = list(dict.fromkeys(entity_data["sameAs"]))
    return entity_data


def check_wikipedia_wikidata(brand_name: str, same_as_links: list, session: requests.Session) -> dict:
    """Check entity presence on Wikipedia and Wikidata via public APIs and sameAs links."""
    endpoints = _CONFIG.get("grounding_endpoints", {})
    wiki_api = endpoints.get("wikipedia_api", "https://en.wikipedia.org/w/api.php")
    wikidata_api = endpoints.get("wikidata_api", "https://www.wikidata.org/w/api.php")
    timeout = endpoints.get("timeout_seconds", 8)

    result = {
        "wikipedia": {
            "has_entry": False,
            "url": None,
            "title": None,
            "snippet": None,
        },
        "wikidata": {
            "has_entry": False,
            "qid": None,
            "description": None,
            "url": None,
        },
    }

    # 1. Check if sameAs already has direct Wikipedia/Wikidata URLs
    for link in same_as_links:
        if "wikipedia.org/wiki/" in link:
            result["wikipedia"]["has_entry"] = True
            result["wikipedia"]["url"] = link
            result["wikipedia"]["title"] = link.split("/wiki/")[-1].replace("_", " ")
        if "wikidata.org/wiki/Q" in link:
            result["wikidata"]["has_entry"] = True
            result["wikidata"]["url"] = link
            qid_match = re.search(r"Q\d+", link)
            if qid_match:
                result["wikidata"]["qid"] = qid_match.group(0)

    # 2. Query Wikipedia Search API if not already verified
    if not result["wikipedia"]["has_entry"] and brand_name:
        try:
            params = {
                "action": "query",
                "list": "search",
                "srsearch": brand_name,
                "utf8": "1",
                "format": "json",
                "srlimit": 3,
            }
            resp = session.get(wiki_api, params=params, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                search_results = data.get("query", {}).get("search", [])
                for item in search_results:
                    title = item.get("title", "")
                    if title.lower() == brand_name.lower() or brand_name.lower() in title.lower():
                        result["wikipedia"]["has_entry"] = True
                        result["wikipedia"]["title"] = title
                        result["wikipedia"]["url"] = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
                        result["wikipedia"]["snippet"] = item.get("snippet", "")
                        break
        except Exception:
            pass

    # 3. Query Wikidata Search API if not already verified
    if not result["wikidata"]["has_entry"] and brand_name:
        try:
            params = {
                "action": "wbsearchentities",
                "search": brand_name,
                "language": "en",
                "format": "json",
                "limit": 3,
            }
            resp = session.get(wikidata_api, params=params, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                search_results = data.get("search", [])
                for item in search_results:
                    label = item.get("label", "")
                    if label.lower() == brand_name.lower() or brand_name.lower() in label.lower():
                        result["wikidata"]["has_entry"] = True
                        result["wikidata"]["qid"] = item.get("id")
                        result["wikidata"]["description"] = item.get("description", "")
                        result["wikidata"]["url"] = item.get("concepturi") or f"https://www.wikidata.org/wiki/{item.get('id')}"
                        break
        except Exception:
            pass

    return result


def generate_corroboration_templates(brand_name: str, claims: dict) -> list:
    """Generate search query templates for the agent to verify core claims via search_web."""
    templates = []
    if not brand_name:
        return templates

    templates.append({
        "claim_type": "brand_identity",
        "claim_value": brand_name,
        "search_query": f'"{brand_name}" "company" OR "about"',
        "target_sources": "Corporate registries, Crunchbase, Bloomberg",
    })

    if claims.get("founding_year"):
        templates.append({
            "claim_type": "founding_year",
            "claim_value": claims["founding_year"],
            "search_query": f'"{brand_name}" "founded" OR "established" "{claims["founding_year"]}"',
            "target_sources": "News articles, industry profiles, Wikidata",
        })
    else:
        templates.append({
            "claim_type": "founding_year",
            "claim_value": None,
            "search_query": f'"{brand_name}" "founded" OR "established in"',
            "target_sources": "Business registries, Wikipedia, Crunchbase",
        })

    if claims.get("hq_location"):
        templates.append({
            "claim_type": "hq_location",
            "claim_value": claims["hq_location"],
            "search_query": f'"{brand_name}" headquarters "{claims["hq_location"]}"',
            "target_sources": "Corporate directory, Google Maps, Crunchbase",
        })

    for prod in claims.get("flagship_products", [])[:2]:
        templates.append({
            "claim_type": "flagship_product",
            "claim_value": prod,
            "search_query": f'"{brand_name}" "{prod}" review OR pricing',
            "target_sources": "G2, Capterra, TechCrunch, product reviews",
        })

    for leader in claims.get("leadership", [])[:2]:
        templates.append({
            "claim_type": "leadership",
            "claim_value": leader,
            "search_query": f'"{brand_name}" "{leader}" CEO OR founder',
            "target_sources": "LinkedIn, Forbes, Reuters, executive bios",
        })

    return templates


def analyze_page(url: str, session: requests.Session, check_dead_links: bool = True) -> dict:
    """Fetch and extract date signals, staleness markers, and entities from a single page."""
    try:
        resp = session.get(url, timeout=_DEFAULT_TIMEOUT, allow_redirects=True)
    except Exception as e:
        return {
            "url": url,
            "status_code": 0,
            "error": f"Failed to fetch {url}: {e}",
            "date_signals": {"has_any_date_signal": False},
            "staleness_markers": {},
            "entities": [],
        }

    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    entities = extract_json_ld_entities(soup)
    date_signals = extract_date_signals(resp, soup, entities)
    staleness = extract_staleness_markers(soup, resp.url, session, check_dead_links=check_dead_links)
    entity_data = extract_entity_data(soup, entities, resp.url)

    return {
        "url": url,
        "final_url": resp.url,
        "status_code": resp.status_code,
        "content_type": resp.headers.get("Content-Type", ""),
        "date_signals": date_signals,
        "staleness_markers": staleness,
        "entity_data": entity_data,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract freshness and corroboration data from a website.")
    parser.add_argument("--url", required=True, help="Site root URL (e.g. https://example.com)")
    parser.add_argument("--pages", default="", help="Comma-separated paths or URLs to audit")
    parser.add_argument("--max-pages", type=int, default=_DEFAULT_MAX_PAGES, help="Max pages to inspect")
    parser.add_argument("--skip-dead-links", action="store_true", help="Skip checking outbound link status for speed")
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
    accumulated_same_as = []
    detected_brand_name = ""
    aggregated_claims = {
        "founding_year": None,
        "hq_location": None,
        "leadership": [],
        "flagship_products": [],
        "pricing_sample": []
    }

    for u in urls_to_check:
        page_res = analyze_page(u, session, check_dead_links=not args.skip_dead_links)
        pages_output.append(page_res)

        ed = page_res.get("entity_data", {})
        if ed.get("brand_name") and not detected_brand_name:
            detected_brand_name = ed["brand_name"]
        accumulated_same_as.extend(ed.get("sameAs", []))

        # Aggregate claims across pages
        c = ed.get("claims", {})
        if c.get("founding_year") and not aggregated_claims["founding_year"]:
            aggregated_claims["founding_year"] = c["founding_year"]
        if c.get("hq_location") and not aggregated_claims["hq_location"]:
            aggregated_claims["hq_location"] = c["hq_location"]
        for lead in c.get("leadership", []):
            if lead not in aggregated_claims["leadership"]:
                aggregated_claims["leadership"].append(lead)
        for prod in c.get("flagship_products", []):
            if prod not in aggregated_claims["flagship_products"]:
                aggregated_claims["flagship_products"].append(prod)
        for price in c.get("pricing_sample", []):
            if price not in aggregated_claims["pricing_sample"]:
                aggregated_claims["pricing_sample"].append(price)

    accumulated_same_as = list(dict.fromkeys(accumulated_same_as))

    # Grounding check on Wikipedia / Wikidata
    grounding = check_wikipedia_wikidata(detected_brand_name, accumulated_same_as, session)

    # Generate search query templates for external corroboration
    corroboration_templates = generate_corroboration_templates(detected_brand_name, aggregated_claims)

    final_report = {
        "site": base_url,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "total_pages_audited": len(pages_output),
        "entity": {
            "detected_brand_name": detected_brand_name,
            "aggregated_claims": aggregated_claims,
            "sameAs_links": accumulated_same_as,
        },
        "grounding": grounding,
        "corroboration_templates": corroboration_templates,
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
