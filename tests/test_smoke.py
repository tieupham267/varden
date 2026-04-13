"""Smoke test — end-to-end pipeline with fixture Oksskolten DB + mock AI.

Verifies the entire flow without external dependencies:
  fixture DB → source reads articles → AI analyzes (mocked) →
  state DB saves results → alerts dispatched (mocked) → cursor advances

Run: pytest tests/test_smoke.py -v
"""

import json
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from src.pipeline import Pipeline
from src.state import (
    get_cursor,
    get_recent_analyses,
    init_state_db,
)

# ── Fixtures ────────────────────────────────────────────────────────

SAMPLE_ARTICLES = [
    {
        "id": 1,
        "title": "Critical FortiOS RCE Zero-Day Exploited in the Wild",
        "url": "https://thehackernews.com/fortios-rce",
        "content": (
            "A critical remote code execution vulnerability (CVE-2026-12345) "
            "in Fortinet FortiOS has been actively exploited by threat actors "
            "targeting financial institutions across Southeast Asia. The flaw "
            "affects FortiGate firewalls running FortiOS 7.4.x and below. "
            "Fortinet has released an emergency patch. Organizations using "
            "FortiGate should update immediately and review logs for IOCs."
        ),
        "published_at": "2026-04-12T08:00:00Z",
        "feed_id": 1,
        "language": "en",
    },
    {
        "id": 2,
        "title": "New Python Package Typosquatting Campaign Discovered",
        "url": "https://bleepingcomputer.com/python-typosquat",
        "content": (
            "Security researchers have discovered a new typosquatting campaign "
            "on PyPI targeting popular machine learning libraries. The malicious "
            "packages contain backdoors that exfiltrate environment variables "
            "including API keys and cloud credentials. Over 5000 downloads "
            "were recorded before the packages were taken down."
        ),
        "published_at": "2026-04-12T10:00:00Z",
        "feed_id": 2,
        "language": "en",
    },
    {
        "id": 3,
        "title": "Weekly Threat Landscape Summary - April 2026",
        "url": "https://example.com/weekly-summary",
        "content": (
            "This week saw a continued increase in ransomware activity globally. "
            "No major new vulnerabilities were disclosed affecting enterprise "
            "infrastructure. Phishing campaigns remain the primary initial "
            "access vector across all sectors."
        ),
        "published_at": "2026-04-12T12:00:00Z",
        "feed_id": 1,
        "language": "en",
    },
]

# Mock AI responses — simulate realistic scoring
MOCK_AI_RESPONSES = {
    1: {
        "relevance_score": 9,
        "relevance_reason": "FortiOS/FortiGate is in the company tech stack (network_security)",
        "severity": "critical",
        "summary_vi": "Lo hong zero-day nghiem trong trong FortiOS dang bi khai thac.",
        "cve_ids": ["CVE-2026-12345"],
        "affected_products": ["FortiOS 7.4", "FortiGate"],
        "threat_actors": [],
        "mitre_attack": [{"tactic": "TA0001 - Initial Access", "technique": "T1190 - Exploit Public-Facing Application"}],
        "recommendations": ["Patch FortiOS ngay", "Review logs 72h"],
    },
    2: {
        "relevance_score": 4,
        "relevance_reason": "Python is used in dev tools but this targets ML libraries not in stack",
        "severity": "medium",
        "summary_vi": "Chien dich typosquatting nham vao cac thu vien Python ML.",
        "cve_ids": [],
        "affected_products": ["PyPI"],
        "threat_actors": [],
        "mitre_attack": [],
        "recommendations": ["Review pip dependencies"],
    },
    3: {
        "relevance_score": 2,
        "relevance_reason": "Generic weekly summary, no specific tech match",
        "severity": "info",
        "summary_vi": "Tom tat tinh hinh ransomware tuan qua, khong co gi dac biet.",
        "cve_ids": [],
        "affected_products": [],
        "threat_actors": [],
        "mitre_attack": [],
        "recommendations": [],
    },
}


@pytest.fixture
def oksskolten_fixture_db(tmp_path):
    """Create a realistic Oksskolten database with sample articles."""
    db_path = tmp_path / "oksskolten.db"
    conn = sqlite3.connect(str(db_path))

    conn.execute("CREATE TABLE feeds (id INTEGER PRIMARY KEY, title TEXT, url TEXT)")
    conn.execute("""
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            title TEXT,
            url TEXT,
            content TEXT,
            published_at TEXT,
            feed_id INTEGER,
            language TEXT
        )
    """)

    conn.execute("INSERT INTO feeds VALUES (1, 'The Hacker News', 'https://thehackernews.com')")
    conn.execute("INSERT INTO feeds VALUES (2, 'BleepingComputer', 'https://bleepingcomputer.com')")

    for article in SAMPLE_ARTICLES:
        conn.execute(
            "INSERT INTO articles (id, title, url, content, published_at, feed_id, language) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                article["id"],
                article["title"],
                article["url"],
                article["content"],
                article["published_at"],
                article["feed_id"],
                article["language"],
            ),
        )

    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
async def smoke_env(tmp_path, oksskolten_fixture_db, monkeypatch):
    """Set up full environment for smoke test."""
    state_path = str(tmp_path / "state.db")
    monkeypatch.setattr("src.state.STATE_DB", state_path)
    monkeypatch.setenv("OKSSKOLTEN_MODE", "sqlite")
    monkeypatch.setenv("OKSSKOLTEN_DB_PATH", oksskolten_fixture_db)
    monkeypatch.setenv("ALERT_THRESHOLD", "7")
    monkeypatch.setenv("ALERT_SEVERITIES", "critical,high")
    monkeypatch.setenv("BATCH_SIZE", "10")
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    monkeypatch.setenv("SLACK_ENABLED", "false")
    monkeypatch.setenv("EMAIL_ENABLED", "false")

    await init_state_db()
    return state_path


async def _mock_analyze(article: dict) -> dict | None:
    """Return pre-built analysis based on article ID."""
    return MOCK_AI_RESPONSES.get(article["id"])


# ── Smoke tests ─────────────────────────────────────────────────────


class TestSmokePipeline:
    """End-to-end pipeline smoke test with fixture DB."""

    async def test_full_cycle(self, smoke_env):
        """Run one full cycle: read articles → analyze → save → alert check."""
        with patch.object(Pipeline, "__init__", lambda self: None):
            pipeline = Pipeline()

        # Wire real source against fixture DB
        from src.source import OksskoltenSource

        pipeline.source = OksskoltenSource()
        pipeline.analyzer = AsyncMock()
        pipeline.analyzer.analyze = _mock_analyze
        pipeline.alert_threshold = 7
        pipeline.alert_severities = {"critical", "high"}
        pipeline.batch_size = 10

        with patch("src.pipeline.dispatch_alert", new_callable=AsyncMock, return_value=[]) as mock_alert, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await pipeline.run_cycle()

        # Verify: cursor advanced to article 3
        cursor = await get_cursor()
        assert cursor == 3

        # Verify: all 3 articles analyzed and saved
        analyses = await get_recent_analyses(hours=24 * 365, limit=10)
        assert len(analyses) == 3

        # Verify: scores saved correctly
        by_id = {r["oksskolten_id"]: r for r in analyses}
        assert by_id[1]["relevance_score"] == 9
        assert by_id[1]["severity"] == "critical"
        assert by_id[2]["relevance_score"] == 4
        assert by_id[3]["relevance_score"] == 2

        # Verify: only article 1 triggered alert check (score 9 >= 7, critical)
        mock_alert.assert_called_once()
        alerted_article = mock_alert.call_args[0][0]
        assert alerted_article["id"] == 1

    async def test_incremental_processing(self, smoke_env):
        """Second cycle should not re-process already analyzed articles."""
        with patch.object(Pipeline, "__init__", lambda self: None):
            pipeline = Pipeline()

        from src.source import OksskoltenSource

        pipeline.source = OksskoltenSource()
        pipeline.analyzer = AsyncMock()
        pipeline.analyzer.analyze = _mock_analyze
        pipeline.alert_threshold = 7
        pipeline.alert_severities = {"critical", "high"}
        pipeline.batch_size = 10

        with patch("src.pipeline.dispatch_alert", new_callable=AsyncMock, return_value=[]), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            # Cycle 1
            await pipeline.run_cycle()

            # Cycle 2 — same articles, should all be skipped
            pipeline.source._schema_cache = None  # reset cache for new conn
            await pipeline.run_cycle()

        # Still 3 analyses total (not 6)
        analyses = await get_recent_analyses(hours=24 * 365, limit=10)
        assert len(analyses) == 3

    async def test_source_reads_fixture_db(self, smoke_env):
        """Verify source correctly reads the fixture Oksskolten database."""
        from src.source import OksskoltenSource

        source = OksskoltenSource()
        articles = await source.fetch_new_articles(since_id=None, limit=50)

        assert len(articles) == 3
        assert articles[0]["title"] == "Critical FortiOS RCE Zero-Day Exploited in the Wild"
        assert articles[0]["feed_name"] == "The Hacker News"
        assert articles[1]["feed_name"] == "BleepingComputer"

    async def test_state_db_integrity(self, smoke_env):
        """Verify state DB schema and operations work end-to-end."""
        from src.state import already_analyzed, mark_alert_sent, save_analysis, set_cursor

        article = {"id": 99, "title": "Test", "url": "https://ex.com", "feed_name": "Feed"}
        analysis = {"severity": "high", "relevance_score": 8, "summary_vi": "Test"}

        # Save → check exists → mark alert → verify
        assert await already_analyzed(99) is False
        await save_analysis(article, analysis)
        assert await already_analyzed(99) is True

        await mark_alert_sent(99, ["telegram"])
        await set_cursor(99)
        assert await get_cursor() == 99

        rows = await get_recent_analyses(hours=24 * 365, limit=10)
        row = next(r for r in rows if r["oksskolten_id"] == 99)
        assert row["alert_sent"] == 1
        assert "telegram" in row["alert_channels"]

    async def test_analysis_json_preserved(self, smoke_env):
        """Verify full analysis JSON is stored and retrievable."""
        from src.state import save_analysis

        article = {"id": 50, "title": "Test", "url": "", "feed_name": "F"}
        analysis = MOCK_AI_RESPONSES[1]

        await save_analysis(article, analysis)
        rows = await get_recent_analyses(hours=24 * 365, limit=10)
        row = next(r for r in rows if r["oksskolten_id"] == 50)

        stored = json.loads(row["analysis_json"])
        assert stored["cve_ids"] == ["CVE-2026-12345"]
        assert stored["relevance_score"] == 9
        assert len(stored["mitre_attack"]) == 1
