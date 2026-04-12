# Varden

> *A cairn marks the path and warns of danger. So does Varden.*

AI-powered threat intel automation layer that sits next to [Oksskolten](https://github.com/babarot/oksskolten) and adds:

- **Auto-analysis** of every new article (not on-demand)
- **Relevance scoring** based on your company's tech stack
- **Severity classification** + MITRE ATT&CK mapping
- **Smart alerts** via Telegram/Slack only for high-relevance items
- **DeepSeek support** (or Claude, OpenAI, Ollama, any OpenAI-compatible provider)

## Why sidecar instead of fork?

Oksskolten does RSS fetching, dedup, and full-text extraction better than most tools. Instead of duplicating that work, this sidecar reads Oksskolten's SQLite directly (read-only) and adds the automation layer on top. When Oksskolten updates, you just `docker compose pull` — no merge conflicts.

## Architecture

```
  RSS feeds
      ↓
  ┌──────────────┐
  │  Oksskolten  │  ← fetch, dedup, full-text extract (unchanged)
  │   (port 3000)│
  └──────┬───────┘
         │ shared volume (read-only SQLite)
         ↓
  ┌──────────────┐
  │   Sidecar    │  ← poll, AI analyze, alert, digest
  └──────┬───────┘
         │
    ┌────┴─────┬─────────┐
    ↓          ↓         ↓
 Telegram   Slack    Email digest
```

## Quick start

### 1. Configure your company profile

Edit `config/company_profile.yaml` — this is the most important file. The AI uses it to decide what's relevant to YOU.

```yaml
tech_stack:
  network_security:
    - "Fortinet FortiGate"    # ← list what you actually run
  endpoint_security:
    - "CrowdStrike Falcon"
  # ... etc
```

### 2. Set up environment

```bash
cp .env.example .env
# Edit .env — pick AI provider and add credentials
```

Minimum required:
- `ANTHROPIC_API_KEY` (or DeepSeek / OpenAI-compatible equivalent)
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (or Slack webhook)

### 3. Run alongside Oksskolten

If you already have Oksskolten running:

**Option A: Share the volume (recommended)**

Edit your existing Oksskolten `docker-compose.yaml` to use a named volume, then add the sidecar service referencing the same volume. See `docker-compose.yaml` in this repo for reference.

**Option B: Bind mount**

```yaml
sidecar:
  # ...
  volumes:
    - /absolute/path/to/oksskolten/data:/oksskolten-data:ro
```

**Option C: API mode (no shared volume)**

Set `OKSSKOLTEN_MODE=api` in `.env`. Less reliable because API endpoints may differ between Oksskolten versions.

### 4. Start

```bash
docker compose up -d

# Test one cycle
docker compose run --rm sidecar run

# Check status
docker compose run --rm sidecar status
```

## Commands

```bash
python main.py run        # Single cycle (for testing)
python main.py daemon     # Scheduled daemon (production)
python main.py digest     # Send email digest of last 24h
python main.py status     # Show recent analyses
```

## How it works

Every 15 minutes (configurable):

1. **Cursor read** — get last processed Oksskolten article ID
2. **Fetch new articles** — query Oksskolten's SQLite for `id > cursor`
3. **AI analysis** — for each article, call Claude/DeepSeek with:
   - Your company profile (tech stack, actors, priorities)
   - Article full text
   - Prompt asking for relevance score 0-10 + severity + summary + MITRE + actions
4. **Alert dispatch** — if `relevance >= 7` AND `severity in [critical, high]` → Telegram/Slack
5. **State update** — save analysis, advance cursor

## Example alert

```
🔴 [CRITICAL] Relevance: 9/10 ●●●●●●●●●○

Critical Fortinet FortiOS RCE đang bị khai thác tích cực

📝 Lỗ hổng RCE nghiêm trọng (CVE-2026-xxxxx) trong FortiOS
   đang bị APT nhắm mục tiêu các tổ chức tài chính APAC.
   Patch khẩn cấp được release ngày hôm qua.

🎯 Công ty đang dùng Fortinet FortiGate trong tech stack,
   threat actor nhắm mục tiêu sector tài chính Việt Nam.

🔖 CVE: CVE-2026-xxxxx
📦 Sản phẩm: FortiOS 7.4, FortiGate
👥 Actors: APT-TBD
⚔️ MITRE: T1190

💡 Khuyến nghị:
  • Patch FortiOS lên 7.4.x ngay trong tối nay
  • Review FortiGate logs 72h qua tìm IoC
  • Enable WAF rules cho management interface

📰 The Hacker News
🔗 Đọc bài gốc
```

## Troubleshooting

### "Could not find articles table"

Oksskolten's schema may differ from what the sidecar expects. The source module auto-discovers tables at startup — check logs for discovered columns. If it fails, manually inspect:

```bash
docker exec -it oksskolten sqlite3 /app/data/oksskolten.db ".schema"
```

Then update column name mappings in `src/source.py` → `_discover_schema()`.

### "Oksskolten DB not found"

Verify the shared volume is mounted correctly:

```bash
docker compose exec sidecar ls -la /oksskolten-data/
```

### API mode returns 404

Oksskolten's API endpoints are not officially documented. Check Oksskolten's logs for actual route paths, then update `endpoint_candidates` in `src/source.py`.

### Too many alerts

Increase `ALERT_THRESHOLD` in `.env` (default 7, try 8 or 9). Or narrow `ALERT_SEVERITIES` to just `critical`.

### Too few alerts

Check if company profile is too narrow. The AI matches articles against tech stack — if your stack list is empty, nothing will score high.

## Cost estimate

Assuming 100 articles/day processed:
- **Claude Sonnet**: ~$0.30-0.60/day ($10-20/month)
- **DeepSeek Chat**: ~$0.03-0.08/day ($1-3/month)
- **Self-hosted Ollama**: $0 (just compute cost)

DeepSeek is ~10x cheaper and often sufficient for this task. Set `AI_PROVIDER=deepseek` in `.env` to switch.

## Files

```
varden/
├── main.py                    # Entry point + CLI
├── docker-compose.yaml        # Integration with Oksskolten
├── Dockerfile
├── requirements.txt
├── .env.example
├── config/
│   └── company_profile.yaml   # EDIT THIS - defines what's relevant to you
├── src/
│   ├── source.py              # Oksskolten SQLite/API reader
│   ├── state.py               # Cursor + analysis state
│   ├── ai_analyzer.py         # Multi-provider AI (Claude/DeepSeek/OpenAI-compat)
│   ├── notifier.py            # Telegram / Slack / Email
│   └── pipeline.py            # Main orchestration loop
└── data/                      # Runtime (gitignored)
    ├── varden_state.db       # Our own state
    └── varden.log
```
