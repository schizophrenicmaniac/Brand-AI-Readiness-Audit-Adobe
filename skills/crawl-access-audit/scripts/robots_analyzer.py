#!/usr/bin/env python3
"""robots_analyzer.py — Fetch and parse robots.txt with AI-bot classification.

Part of the crawl-access-audit skill in the Brand AI Readiness Audit marketplace.
Fetches robots.txt from a given site root, parses all directives, and classifies
blocked bots as search (discoverability-critical) vs. training (policy choice).

Usage:
    python3 robots_analyzer.py --url https://example.com
    python3 robots_analyzer.py --url https://example.com --critical-paths /products,/pricing,/blog

Output: JSON to stdout.
"""

import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse, urljoin

try:
    import requests
except ImportError:
    print(json.dumps({"error": "Missing dependency: requests. Install with: pip install requests"}))
    sys.exit(1)

# ---------------------------------------------------------------------------
# Reference file loader — resolves paths relative to this script's location
# so the script works correctly regardless of the caller's working directory.
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REFS_DIR = os.path.join(_SCRIPTS_DIR, "..", "references")


def _load_json(filename: str) -> dict:
    path = os.path.normpath(os.path.join(_REFS_DIR, filename))
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(json.dumps({"error": f"Reference file not found: {path}. Run from the skill root or ensure references/ is present."}), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON in {path}: {e}"}), file=sys.stderr)
        sys.exit(1)

# ---------------------------------------------------------------------------
# Load data from SSOT reference files — edit those files, not this script.
# references/ai-crawler-registry.json  → BOT_REGISTRY
# references/crawl-config.json         → SUPPRESSED_PATHS, DEFAULT_CRITICAL_PATHS, thresholds
# ---------------------------------------------------------------------------
_registry_data = _load_json("ai-crawler-registry.json")
_config = _load_json("crawl-config.json")

# Bot registry: keys lowercased for case-insensitive matching
BOT_REGISTRY: dict = _registry_data["bots"]

# Paths suppressed from critical-block findings (CMS admin, infra, etc.)
SUPPRESSED_PATHS: set = set(_config["robots"]["suppressed_paths"])

# High-value paths checked for unintentional crawler blocks
DEFAULT_CRITICAL_PATHS: list = _config["robots"]["default_critical_paths"]

# Crawl-delay severity thresholds (seconds)
_CRAWL_DELAY_THRESHOLDS = _config["robots"]["crawl_delay_thresholds"]


def classify_bot(user_agent_str: str) -> dict:
    """Classify a user-agent string using the bot registry."""
    key = user_agent_str.strip().lower()
    if key in BOT_REGISTRY:
        return BOT_REGISTRY[key]
    # Fuzzy match: check if any registry key is a substring
    for reg_key, info in BOT_REGISTRY.items():
        if reg_key in key or key in reg_key:
            return info
    return {"category": "unknown", "operator": "unknown", "impact": "unknown"}


def parse_robots_txt(content: str) -> list:
    """Parse robots.txt content into a list of directive groups.

    Each group = { user_agent: str, rules: [...], crawl_delay: float|None }
    """
    groups = []
    current_agents, current_rules, current_crawl_delay = [], [], None

    def flush_group():
        nonlocal current_agents, current_rules, current_crawl_delay
        if current_agents:
            groups.append({
                "user_agents": list(current_agents),
                "rules": list(current_rules),
                "crawl_delay": current_crawl_delay,
            })
        current_agents, current_rules, current_crawl_delay = [], [], None

    for raw_line in content.splitlines():
        # Strip comments
        line = raw_line.split("#")[0].strip()
        if not line:
            continue

        # Parse directive
        match = re.match(r"^(\S+)\s*:\s*(.*)$", line, re.IGNORECASE)
        if not match:
            continue

        directive = match.group(1).lower()
        value = match.group(2).strip()

        if directive == "user-agent":
            # Adjacent User-agent lines share one group; a new agent after any
            # rule starts a new group. This avoids rule leakage between groups.
            if current_rules or current_crawl_delay is not None:
                flush_group()
            current_agents.append(value)
        elif directive in ("disallow", "allow"):
            current_rules.append({"type": directive, "path": value})
        elif directive == "crawl-delay":
            try:
                current_crawl_delay = float(value)
            except ValueError:
                current_crawl_delay = None
        # Sitemap directives are handled separately (they're global)

    # Flush last group
    flush_group()

    return groups


def extract_sitemap_urls(content: str) -> list:
    """Extract Sitemap: directives from robots.txt."""
    sitemaps = []
    for line in content.splitlines():
        line_clean = line.split("#")[0].strip()
        match = re.match(r"^sitemap\s*:\s*(.+)$", line_clean, re.IGNORECASE)
        if match:
            sitemaps.append(match.group(1).strip())
    return sitemaps


def _matching_groups(groups: list, user_agent: str) -> list:
    """Return only the most-specific robots group(s) for a bot."""
    bot = user_agent.lower()
    matches = []
    for group in groups:
        for token in group["user_agents"]:
            token = token.lower().strip()
            if token == "*" or (token and bot.startswith(token)):
                matches.append((len(token) if token != "*" else 0, group))
                break
    if not matches:
        return []
    best = max(score for score, _ in matches)
    return [group for score, group in matches if score == best]


def _rule_matches(rule_path: str, path: str) -> bool:
    if not rule_path:
        return False
    anchored = rule_path.endswith("$")
    expression = re.escape(rule_path[:-1] if anchored else rule_path).replace(r"\*", ".*")
    return re.match("^" + expression + ("$" if anchored else ""), path) is not None


def check_path_blocked(rules: list, path: str) -> bool:
    """RFC-style longest-match evaluation; Allow wins equal-length ties."""
    matches = []
    for rule in rules:
        rule_path = rule["path"]
        if _rule_matches(rule_path, path):
            matches.append((len(rule_path.rstrip("$")), rule["type"]))
    if not matches:
        return False
    longest = max(length for length, _ in matches)
    return not any(kind == "allow" for length, kind in matches if length == longest)


def evaluate_bot(groups: list, user_agent: str, path: str) -> dict:
    selected = _matching_groups(groups, user_agent)
    rules = [rule for group in selected for rule in group["rules"]]
    delays = [group["crawl_delay"] for group in selected if group["crawl_delay"] is not None]
    return {
        "blocked": check_path_blocked(rules, path),
        "matched_user_agents": [agent for group in selected for agent in group["user_agents"]],
        "crawl_delay": max(delays) if delays else None,
    }


def is_suppressed_path(path: str) -> bool:
    """Check if a path should be suppressed from critical-path-block findings."""
    path_lower = path.lower()
    for suppressed in SUPPRESSED_PATHS:
        if path_lower.startswith(suppressed):
            return True
    return False


def analyze(url: str, critical_paths: list) -> dict:
    """Main analysis: fetch robots.txt and produce structured output."""
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = urljoin(base_url, "/robots.txt")

    result = {
        "url": robots_url,
        "status": None,
        "raw_content": None,
        "parse_error": None,
        "directives": [],
        "sitemap_urls": [],
        "wildcard_disallow_all": False,
        "ai_search_bots_blocked": [],
        "ai_training_bots_blocked": [],
        "critical_path_blocks": [],
        "crawl_delay_issues": [],
    }

    # Fetch robots.txt
    try:
        resp = requests.get(robots_url, timeout=15, allow_redirects=True,
                            headers={"User-Agent": "BrandAIReadinessAudit/1.0"})
        result["status"] = resp.status_code
    except requests.exceptions.SSLError as e:
        result["status"] = None
        result["parse_error"] = f"SSL error fetching robots.txt: {str(e)}"
        return result
    except requests.exceptions.ConnectionError as e:
        result["status"] = None
        result["parse_error"] = f"Connection error fetching robots.txt: {str(e)}"
        return result
    except requests.exceptions.Timeout:
        result["status"] = None
        result["parse_error"] = "Timeout fetching robots.txt (>15s)"
        return result
    except Exception as e:
        result["status"] = None
        result["parse_error"] = f"Error fetching robots.txt: {str(e)}"
        return result

    # Handle non-200 responses
    if resp.status_code == 404:
        # No robots.txt — implies allow all
        result["raw_content"] = ""
        result["parse_error"] = "No robots.txt found (404). All crawlers implicitly allowed."
        return result
    elif resp.status_code == 403:
        # Treat as no robots.txt (RFC says treat as allow-all)
        result["raw_content"] = ""
        result["parse_error"] = "robots.txt returned 403. Per RFC 9309, treat as allow-all."
        return result
    elif resp.status_code >= 500:
        result["raw_content"] = ""
        result["parse_error"] = f"robots.txt returned server error ({resp.status_code}). Crawlers may retry or treat as allow-all."
        return result
    elif resp.status_code != 200:
        result["raw_content"] = ""
        result["parse_error"] = f"robots.txt returned unexpected status {resp.status_code}."
        return result

    content = resp.text
    result["raw_content"] = content[:10000]  # Cap for output size

    # Extract sitemap URLs
    result["sitemap_urls"] = extract_sitemap_urls(content)

    # Parse directive groups
    groups = parse_robots_txt(content)

    for group in groups:
        for ua in group["user_agents"]:
            bot_info = classify_bot(ua)
            result["directives"].append({
                "user_agent": ua,
                "bot_category": bot_info["category"],
                "bot_operator": bot_info.get("operator", "unknown"),
                "impact_if_blocked": bot_info.get("impact", "unknown"),
                "rules": group["rules"],
                "crawl_delay": group["crawl_delay"],
            })

    # Apply wildcard and bot-specific directives using the effective rule set.
    for bot, bot_info in BOT_REGISTRY.items():
        root = evaluate_bot(groups, bot, "/")
        record = {"user_agent": bot, "operator": bot_info["operator"], "matched_user_agents": root["matched_user_agents"]}
        if root["blocked"] and bot_info["category"] == "search_bot":
            result["ai_search_bots_blocked"].append({**record, "impact": bot_info["impact"]})
        elif root["blocked"] and bot_info["category"] == "training_bot":
            result["ai_training_bots_blocked"].append(record)
        if bot_info["category"] != "training_bot":
            for path in critical_paths:
                if not is_suppressed_path(path) and evaluate_bot(groups, bot, path)["blocked"]:
                    result["critical_path_blocks"].append({"user_agent": bot, "path": path, "bot_category": bot_info["category"]})
            delay = root["crawl_delay"]
            if delay is not None:
                severity = "high" if delay > _CRAWL_DELAY_THRESHOLDS["high_seconds"] else "medium" if delay > _CRAWL_DELAY_THRESHOLDS["medium_seconds"] else "low" if delay > _CRAWL_DELAY_THRESHOLDS["low_seconds"] else "info"
                if severity != "info":
                    result["crawl_delay_issues"].append({"user_agent": bot, "crawl_delay": delay, "severity": severity})

    result["wildcard_disallow_all"] = evaluate_bot(groups, "BrandAIReadinessAudit", "/")["blocked"]

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and parse robots.txt with AI-bot classification."
    )
    parser.add_argument("--url", required=True, help="Site root URL (e.g., https://example.com)")
    parser.add_argument(
        "--critical-paths",
        default=None,
        help="Comma-separated list of critical paths to check for blocking (default: common high-value paths)"
    )
    args = parser.parse_args()

    critical_paths = DEFAULT_CRITICAL_PATHS
    if args.critical_paths:
        critical_paths = [p.strip() for p in args.critical_paths.split(",") if p.strip()]

    result = analyze(args.url, critical_paths)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
