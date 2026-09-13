#!/usr/bin/env python3
"""safe_http.py — one safe, cached, rate-limited HTTP path for the whole audit.

Every network call in the marketplace routes through here (via `install()`, which
swaps `requests.Session` for `SafeSession`). This concentrates the read-only /
recommend-only guardrails in a single place:

  * SSRF guard   — only http/https, only ports 80/443, no URL credentials, and the
                   resolved host must be a public IP (rejects loopback, private,
                   link-local incl. 169.254.169.254, reserved, multicast) for IPv4
                   and IPv6. Re-checked on every redirect hop.
  * Rate control — minimum spacing per origin + a single global monotonic deadline.
  * Size cap     — response bodies are truncated to a byte ceiling.
  * Cache        — GET/HEAD responses are memoized, so five skills fetching the same
                   page cost one real request (also bounds total traffic and runtime).

Robots.txt is enforced by the orchestrator at page-selection time (it only lets
allowed URLs into the fetch set), so it is not re-implemented here.

# ponytail: global monkeypatch of requests.Session — simplest way to cover every
# existing call site without editing 25 of them; switch to per-script injection only
# if a caller ever needs an unpatched session in the same process.
"""

import ipaddress
import socket
import time
from urllib.parse import urlsplit

import requests

DEFAULT_MAX_BYTES = 3_000_000          # 3 MB per response
DEFAULT_TIMEOUT = 15                   # seconds, per request
DEFAULT_SPACING = 0.5                  # seconds between requests to the same origin
ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = (None, 80, 443)


class DeadlineExceeded(Exception):
    """Raised when the global audit deadline is hit; orchestrator emits a partial report."""


class UnsafeRequestError(requests.exceptions.RequestException):
    """Raised when a URL fails the SSRF policy. Subclasses RequestException so existing
    try/except RequestException blocks treat it as a normal fetch failure."""


_POLICY = {
    "deadline_monotonic": None,
    "max_bytes": DEFAULT_MAX_BYTES,
    "timeout": DEFAULT_TIMEOUT,
    "spacing": DEFAULT_SPACING,
    "allow_private": False,
    "last_request_at": {},   # origin -> monotonic time
    "cache": {},             # (method, url) -> Response
}
_ORIG_SESSION_CLASS = None


# ---------------------------------------------------------------------------
# SSRF policy
# ---------------------------------------------------------------------------
def _classify_bad(ip_str: str) -> bool:
    """True if an IP is not a safe public address."""
    addr = ipaddress.ip_address(ip_str)
    # IPv4-mapped IPv6 (::ffff:10.0.0.1) — classify the embedded v4 too.
    if getattr(addr, "ipv4_mapped", None) is not None:
        addr = addr.ipv4_mapped
    return (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


def validate_url(url: str, allow_private: bool = False) -> str:
    """Enforce the SSRF policy. Return the url on success, else raise UnsafeRequestError.

    Uses literal-IP hosts without DNS; hostnames are resolved via getaddrinfo and every
    resolved address must pass. Fail-closed: unresolvable hosts are rejected.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeRequestError(f"blocked scheme: {parts.scheme!r} in {url}")
    if parts.username or parts.password:
        raise UnsafeRequestError(f"URL credentials are not allowed: {url}")
    host = parts.hostname
    if not host:
        raise UnsafeRequestError(f"no host in URL: {url}")
    try:
        port = parts.port
    except ValueError:
        raise UnsafeRequestError(f"invalid port in URL: {url}")
    if port not in ALLOWED_PORTS:
        raise UnsafeRequestError(f"blocked port {port} in {url} (only 80/443 allowed)")

    if allow_private:
        return url

    # Collect the IPs this host would connect to.
    ips = []
    try:
        ipaddress.ip_address(host)          # host is already an IP literal
        ips = [host]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port or 443, proto=socket.IPPROTO_TCP)
            ips = [info[4][0] for info in infos]
        except socket.gaierror as exc:
            raise UnsafeRequestError(f"cannot resolve host {host!r}: {exc}")
    if not ips:
        raise UnsafeRequestError(f"no addresses resolved for {host!r}")
    for ip in ips:
        if _classify_bad(ip):
            raise UnsafeRequestError(f"blocked non-public address {ip} for host {host!r}")
    return url


# ---------------------------------------------------------------------------
# Rate / deadline / size helpers
# ---------------------------------------------------------------------------
def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}:{p.port or ''}"


def _check_deadline():
    dl = _POLICY["deadline_monotonic"]
    if dl is not None and time.monotonic() >= dl:
        raise DeadlineExceeded("global audit deadline reached")


def _respect_spacing(url: str):
    origin = _origin(url)
    last = _POLICY["last_request_at"].get(origin)
    if last is not None:
        wait = _POLICY["spacing"] - (time.monotonic() - last)
        if wait > 0:
            # Don't sleep past the deadline.
            dl = _POLICY["deadline_monotonic"]
            if dl is not None:
                wait = min(wait, max(0.0, dl - time.monotonic()))
            time.sleep(wait)
    _check_deadline()


def _cap_body(resp):
    max_bytes = _POLICY["max_bytes"]
    try:
        body = b""
        for chunk in resp.iter_content(8192):
            if chunk:
                body += chunk
                if len(body) >= max_bytes:
                    body = body[:max_bytes]
                    break
        resp._content = body
        resp._content_consumed = True
    except Exception:
        try:
            _ = resp.content  # best effort
        except Exception:
            pass


# ---------------------------------------------------------------------------
# The safe session
# ---------------------------------------------------------------------------
class SafeSession(requests.Session):
    """requests.Session with SSRF + rate + deadline + size cap + response cache.

    Constructed with no args (requests.get does `Session()`), so all configuration
    comes from the module-level policy set by `install()`.
    """

    def send(self, request, **kwargs):
        method = (request.method or "GET").upper()
        url = request.url
        # allow_redirects is part of the cache identity: crawl-access fetches with
        # allow_redirects=False (to inspect the chain) must NOT be served a followed
        # response cached by a content skill, and vice-versa.
        redirects = kwargs.get("allow_redirects", True)
        key = (method, url, bool(redirects))

        if method in ("GET", "HEAD") and key in _POLICY["cache"]:
            return _POLICY["cache"][key]

        _check_deadline()
        validate_url(url, allow_private=_POLICY["allow_private"])
        _respect_spacing(url)

        kwargs.setdefault("timeout", _POLICY["timeout"])
        kwargs["stream"] = True  # stream so we can cap the body ourselves
        resp = super().send(request, **kwargs)
        _POLICY["last_request_at"][_origin(url)] = time.monotonic()

        if method != "HEAD":
            _cap_body(resp)
        if method in ("GET", "HEAD"):
            _POLICY["cache"][key] = resp
        return resp


# ---------------------------------------------------------------------------
# install / uninstall / state
# ---------------------------------------------------------------------------
def install(deadline_monotonic=None, max_bytes=DEFAULT_MAX_BYTES,
            timeout=DEFAULT_TIMEOUT, spacing=DEFAULT_SPACING, allow_private=False):
    """Route all `requests` traffic through SafeSession and set the policy."""
    global _ORIG_SESSION_CLASS
    if _ORIG_SESSION_CLASS is None:
        _ORIG_SESSION_CLASS = requests.sessions.Session
    _POLICY.update({
        "deadline_monotonic": deadline_monotonic,
        "max_bytes": max_bytes,
        "timeout": timeout,
        "spacing": spacing,
        "allow_private": allow_private,
        "last_request_at": {},
        "cache": {},
    })
    requests.sessions.Session = SafeSession
    requests.Session = SafeSession


def uninstall():
    global _ORIG_SESSION_CLASS
    if _ORIG_SESSION_CLASS is not None:
        requests.sessions.Session = _ORIG_SESSION_CLASS
        requests.Session = _ORIG_SESSION_CLASS
        _ORIG_SESSION_CLASS = None
    reset_state()


def reset_state():
    _POLICY["last_request_at"] = {}
    _POLICY["cache"] = {}


def set_deadline_seconds(seconds: float):
    _POLICY["deadline_monotonic"] = time.monotonic() + seconds
