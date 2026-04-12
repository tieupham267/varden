# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Varden

AI-powered threat intelligence sidecar for [Oksskolten](https://github.com/babarot/oksskolten). Reads Oksskolten's SQLite DB (read-only shared volume), analyzes articles via LLM, scores relevance against a company's tech stack, and dispatches alerts via Telegram/Slack/Email. Vietnamese-language analysis output.

## Commands

```bash
# Local development
pip install -r requirements.txt
python main.py run          # Single analysis cycle (testing)
python main.py daemon       # Scheduled polling (production)
python main.py digest       # Email digest of last 24h
python main.py status       # Show recent analyses

# Docker
docker compose up -d
docker compose run --rm sidecar run       # Test one cycle
docker compose run --rm sidecar status    # Check state
docker compose logs -f varden             # Monitor logs
```

No test suite exists yet.

## Architecture

```
RSS feeds → Oksskolten (fetch/dedup/extract)
         → Shared SQLite volume (read-only)
         → Varden pipeline (analyze/alert/digest)
         → Telegram / Slack / Email
```

**Sidecar pattern**: Varden never writes to Oksskolten's DB. It maintains its own `data/varden_state.db` for cursor tracking and analysis cache. This keeps it update-proof against Oksskolten changes.

### Pipeline flow (`src/pipeline.py`)

1. Read cursor (last processed article ID) from state DB
2. Fetch new articles from Oksskolten SQLite (`id > cursor`)
3. For each article: AI analyze → save result → alert if thresholds met
4. Advance cursor. 0.5s delay between articles for rate limiting.

### Key modules

- **`src/source.py`** — Reads Oksskolten's SQLite with auto-schema discovery (handles table/column name variations). Falls back to API mode. Caps content at 10,000 chars, skips articles <50 chars.
- **`src/state.py`** — aiosqlite-based local state: cursor position, analysis cache, alert dedup tracking.
- **`src/ai_analyzer.py`** — Multi-provider LLM orchestration (Anthropic/DeepSeek/OpenAI-compatible). Builds Vietnamese-language prompts with company context from `config/company_profile.yaml`. Returns structured JSON: relevance_score, severity, summary_vi, CVEs, MITRE ATT&CK, recommendations.
- **`src/notifier.py`** — Telegram (HTML), Slack (blocks), Email (SMTP/HTML digest). All three can run simultaneously.
- **`src/pipeline.py`** — Orchestrator tying source → analyzer → state → notifier. ~60 lines.

## Configuration

- **`config/company_profile.yaml`** — Primary tuning lever. Defines tech stack (10+ categories), watched threat actors, priority MITRE techniques, boost/reduce keywords. The AI uses this to compute relevance scores.
- **`.env`** — All runtime config: AI provider selection, API keys, alert thresholds (`ALERT_THRESHOLD` 0-10, `ALERT_SEVERITIES` CSV), poll interval, batch size, notification channels. See `.env.example` for full reference.
- **Alert logic**: fires when `relevance >= ALERT_THRESHOLD` AND `severity in ALERT_SEVERITIES` (AND logic, both must match).

## Tech stack

- Python 3.12, fully async (asyncio, aiosqlite, httpx)
- APScheduler for daemon mode
- Jinja2 for email templates
- No web framework — CLI-only entry point
- Docker deployment with named volume sharing
