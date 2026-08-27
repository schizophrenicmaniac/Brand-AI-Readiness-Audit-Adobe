"""URL identity helpers used only by the crawl-access audit.

They deliberately do not judge content or structured-data quality; they make
the audit's frontier, sitemap comparison, and redirect evidence consistent.
"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMETERS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "_ga", "_gl", "yclid", "twclid",
    "li_fat_id", "mc_cid", "mc_eid",
}


def normalize_url(url: str, *, drop_tracking: bool = True) -> str:
    """Return a stable crawl identity without guessing a site's slash policy."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if drop_tracking:
        pairs = [(key, value) for key, value in pairs if key.lower() not in TRACKING_PARAMETERS]
    query = urlencode(sorted(pairs), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def same_site(url: str, site_url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() == (urlsplit(site_url).hostname or "").lower()
