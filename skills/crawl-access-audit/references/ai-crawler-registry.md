# AI Crawler User-Agent Registry

> **Purpose:** Lookup table used by `scripts/robots_analyzer.py` to classify blocked
> bots by discoverability impact. Updated August 2026.

## Classification

Crawlers fall into two functional categories:

| Category | Label | Meaning |
|----------|-------|---------|
| **Search / Retrieval** | `search_bot` | Fetches pages *at query time* to build cited answers. Blocking these directly removes your brand from AI-generated responses. |
| **Training / Grounding** | `training_bot` | Scrapes content for model pre-training or grounding datasets. Blocking these is a legitimate IP-protection policy choice and does **not** remove you from real-time AI answers. |

---

## Registry

### Search / Retrieval Bots (blocking = discoverability loss)

| User-Agent String | Operator | Product | Impact if Blocked |
|-------------------|----------|---------|-------------------|
| `OAI-SearchBot` | OpenAI | ChatGPT Search (cited answers) | **Critical** — brand invisible in ChatGPT search results |
| `ChatGPT-User` | OpenAI | Live browsing mode (user-triggered) | **Critical** — blocks ChatGPT live browsing |
| `Claude-SearchBot` | Anthropic | Claude search/retrieval | **Critical** — brand invisible in Claude answers |
| `Claude-Web` | Anthropic | Claude web access | **Critical** — blocks Claude web citations |
| `PerplexityBot` | Perplexity AI | Perplexity answer engine | **Critical** — brand invisible in Perplexity |
| `Applebot` | Apple | Siri, Spotlight, Safari Suggestions | **High** — blocks Apple ecosystem AI features |
| `Googlebot` | Google | Google Search (feeds AI Overviews) | **Critical** — blocks Google Search + AI Overviews |
| `Bingbot` | Microsoft | Bing Search (feeds Copilot) | **Critical** — blocks Bing + Microsoft Copilot |
| `YandexBot` | Yandex | Yandex Search | **High** — blocks Yandex search visibility |
| `DuckDuckBot` | DuckDuckGo | DuckDuckGo Search (feeds DuckDuckGo AI Chat) | **High** — blocks DDG AI Chat citations |

### Training / Grounding Bots (blocking = policy choice, not a defect)

| User-Agent String | Operator | Purpose | Impact if Blocked |
|-------------------|----------|---------|-------------------|
| `GPTBot` | OpenAI | Pre-training data collection | **Info** — policy choice; does not affect real-time ChatGPT Search |
| `ClaudeBot` | Anthropic | Training data collection | **Info** — policy choice |
| `Google-Extended` | Google | Gemini training / grounding | **Info** — policy choice; Googlebot handles search indexing separately |
| `CCBot` | Common Crawl | Open web dataset (used by many AI labs) | **Info** — policy choice |
| `Bytespider` | ByteDance | TikTok / model training | **Info** — policy choice |
| `Meta-ExternalAgent` | Meta | Meta AI training | **Info** — policy choice |
| `Applebot-Extended` | Apple | Apple Intelligence training | **Info** — policy choice |
| `Amazonbot` | Amazon | Alexa / Amazon AI training | **Info** — policy choice |
| `FacebookExternalHit` | Meta | Link preview + training | **Info** — policy choice (link previews may be affected) |
| `cohere-ai` | Cohere | Model training | **Info** — policy choice |
| `AI2Bot` | Allen AI | Research model training | **Info** — policy choice |
| `Diffbot` | Diffbot | Knowledge graph extraction | **Low** — may affect some aggregation services |

---

## Single Source of Truth (SSOT)

The authoritative, machine-readable dataset is stored in:
📁 [`references/ai-crawler-registry.json`](file:///Users/divyansh/Projects/Brand%20AI%20Readiness%20Audit%20%7C%20Adobe/skills/crawl-access-audit/references/ai-crawler-registry.json)

`scripts/robots_analyzer.py` loads this JSON file at runtime dynamically. To add new bots or update impact classifications, update `ai-crawler-registry.json` — changes take effect immediately across all audit executions without altering Python code.

---

## Maintenance Notes

- This registry should be updated as new AI crawlers emerge.
- The `search_bot` vs. `training_bot` distinction is based on the bot's *functional purpose*, not the company behind it. A single company (e.g., OpenAI) may operate bots in both categories.
- When a bot's category is ambiguous, default to `training_bot` to avoid false-positive critical findings.


