"""Tests for src/pipeline.py — orchestration logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.pipeline import Pipeline


@pytest.fixture
def pipeline(monkeypatch):
    """Pipeline with mocked source & analyzer."""
    monkeypatch.setenv("ALERT_THRESHOLD", "7")
    monkeypatch.setenv("ALERT_SEVERITIES", "critical,high")
    monkeypatch.setenv("BATCH_SIZE", "20")

    with patch("src.pipeline.OksskoltenSource"), \
         patch("src.pipeline.AIAnalyzer"):
        p = Pipeline()

    p.source = MagicMock()
    p.analyzer = MagicMock()
    return p


# ── No articles ─────────────────────────────────────────────────────


async def test_no_new_articles(pipeline):
    pipeline.source.fetch_new_articles = AsyncMock(return_value=[])

    with patch("src.pipeline.get_cursor", new_callable=AsyncMock, return_value=0):
        await pipeline.run_cycle()
    # Should exit gracefully, no crash


# ── Normal flow ─────────────────────────────────────────────────────


async def test_processes_and_alerts(pipeline, sample_article, sample_analysis):
    pipeline.source.fetch_new_articles = AsyncMock(return_value=[sample_article])
    pipeline.analyzer.analyze = AsyncMock(return_value=sample_analysis)

    with patch("src.pipeline.get_cursor", new_callable=AsyncMock, return_value=0), \
         patch("src.pipeline.already_analyzed", new_callable=AsyncMock, return_value=False), \
         patch("src.pipeline.save_analysis", new_callable=AsyncMock) as mock_save, \
         patch("src.pipeline.dispatch_alert", new_callable=AsyncMock, return_value=["telegram"]) as mock_alert, \
         patch("src.pipeline.mark_alert_sent", new_callable=AsyncMock) as mock_mark, \
         patch("src.pipeline.set_cursor", new_callable=AsyncMock) as mock_cursor, \
         patch("asyncio.sleep", new_callable=AsyncMock):
        await pipeline.run_cycle()

    mock_save.assert_called_once()
    mock_alert.assert_called_once()
    mock_mark.assert_called_once_with(42, ["telegram"])
    mock_cursor.assert_called_once_with(42)


# ── Dedup: skip already-analyzed ────────────────────────────────────


async def test_skips_already_analyzed(pipeline, sample_article):
    pipeline.source.fetch_new_articles = AsyncMock(return_value=[sample_article])

    with patch("src.pipeline.get_cursor", new_callable=AsyncMock, return_value=0), \
         patch("src.pipeline.already_analyzed", new_callable=AsyncMock, return_value=True), \
         patch("src.pipeline.set_cursor", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        await pipeline.run_cycle()

    pipeline.analyzer.analyze.assert_not_called()


# ── Alert thresholds ────────────────────────────────────────────────


async def test_no_alert_below_threshold(pipeline, sample_article):
    low = {"relevance_score": 3, "severity": "low", "relevance_reason": "meh"}
    pipeline.source.fetch_new_articles = AsyncMock(return_value=[sample_article])
    pipeline.analyzer.analyze = AsyncMock(return_value=low)

    with patch("src.pipeline.get_cursor", new_callable=AsyncMock, return_value=0), \
         patch("src.pipeline.already_analyzed", new_callable=AsyncMock, return_value=False), \
         patch("src.pipeline.save_analysis", new_callable=AsyncMock), \
         patch("src.pipeline.dispatch_alert", new_callable=AsyncMock) as mock_alert, \
         patch("src.pipeline.set_cursor", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        await pipeline.run_cycle()

    mock_alert.assert_not_called()


async def test_no_alert_wrong_severity(pipeline, sample_article):
    """Score >= threshold but severity not in allowed list."""
    analysis = {"relevance_score": 9, "severity": "medium", "relevance_reason": "t"}
    pipeline.source.fetch_new_articles = AsyncMock(return_value=[sample_article])
    pipeline.analyzer.analyze = AsyncMock(return_value=analysis)

    with patch("src.pipeline.get_cursor", new_callable=AsyncMock, return_value=0), \
         patch("src.pipeline.already_analyzed", new_callable=AsyncMock, return_value=False), \
         patch("src.pipeline.save_analysis", new_callable=AsyncMock), \
         patch("src.pipeline.dispatch_alert", new_callable=AsyncMock) as mock_alert, \
         patch("src.pipeline.set_cursor", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        await pipeline.run_cycle()

    mock_alert.assert_not_called()


# ── Error handling ──────────────────────────────────────────────────


async def test_analysis_crash_does_not_stop_batch(pipeline):
    """One article crashing should not prevent processing the rest."""
    articles = [
        {"id": 1, "title": "Crash", "url": "", "content": "x" * 100, "feed_name": "F"},
        {"id": 2, "title": "OK", "url": "", "content": "x" * 100, "feed_name": "F"},
    ]
    pipeline.source.fetch_new_articles = AsyncMock(return_value=articles)
    pipeline.analyzer.analyze = AsyncMock(
        side_effect=[Exception("boom"), {"relevance_score": 1, "severity": "info"}]
    )

    with patch("src.pipeline.get_cursor", new_callable=AsyncMock, return_value=0), \
         patch("src.pipeline.already_analyzed", new_callable=AsyncMock, return_value=False), \
         patch("src.pipeline.save_analysis", new_callable=AsyncMock) as mock_save, \
         patch("src.pipeline.set_cursor", new_callable=AsyncMock) as mock_cursor, \
         patch("asyncio.sleep", new_callable=AsyncMock):
        await pipeline.run_cycle()

    # Article 2 should still be saved despite article 1 crashing
    mock_save.assert_called_once()
    mock_cursor.assert_called_once_with(2)


async def test_analyze_returns_none_counted_as_failed(pipeline, sample_article):
    pipeline.source.fetch_new_articles = AsyncMock(return_value=[sample_article])
    pipeline.analyzer.analyze = AsyncMock(return_value=None)

    with patch("src.pipeline.get_cursor", new_callable=AsyncMock, return_value=0), \
         patch("src.pipeline.already_analyzed", new_callable=AsyncMock, return_value=False), \
         patch("src.pipeline.save_analysis", new_callable=AsyncMock) as mock_save, \
         patch("src.pipeline.set_cursor", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        await pipeline.run_cycle()

    mock_save.assert_not_called()


# ── Cursor advancement ──────────────────────────────────────────────


async def test_cursor_advances_to_max_id(pipeline):
    articles = [
        {"id": 5, "title": "A", "url": "", "content": "x" * 100, "feed_name": "F"},
        {"id": 3, "title": "B", "url": "", "content": "x" * 100, "feed_name": "F"},
        {"id": 8, "title": "C", "url": "", "content": "x" * 100, "feed_name": "F"},
    ]
    pipeline.source.fetch_new_articles = AsyncMock(return_value=articles)
    pipeline.analyzer.analyze = AsyncMock(return_value={"relevance_score": 1, "severity": "info"})

    with patch("src.pipeline.get_cursor", new_callable=AsyncMock, return_value=0), \
         patch("src.pipeline.already_analyzed", new_callable=AsyncMock, return_value=False), \
         patch("src.pipeline.save_analysis", new_callable=AsyncMock), \
         patch("src.pipeline.set_cursor", new_callable=AsyncMock) as mock_cursor, \
         patch("asyncio.sleep", new_callable=AsyncMock):
        await pipeline.run_cycle()

    mock_cursor.assert_called_once_with(8)


async def test_cursor_not_advanced_if_no_progress(pipeline):
    """If all articles are already analyzed, cursor still advances to max_id."""
    articles = [{"id": 10, "title": "A", "url": "", "content": "x" * 100, "feed_name": "F"}]
    pipeline.source.fetch_new_articles = AsyncMock(return_value=articles)

    with patch("src.pipeline.get_cursor", new_callable=AsyncMock, return_value=5), \
         patch("src.pipeline.already_analyzed", new_callable=AsyncMock, return_value=True), \
         patch("src.pipeline.set_cursor", new_callable=AsyncMock) as mock_cursor, \
         patch("asyncio.sleep", new_callable=AsyncMock):
        await pipeline.run_cycle()

    # max_id=10 > cursor=5, so cursor should advance
    mock_cursor.assert_called_once_with(10)
