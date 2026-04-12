"""Tests for src/state.py — cursor tracking, analysis cache, alert dedup."""

from src.state import (
    already_analyzed,
    get_cursor,
    get_recent_analyses,
    mark_alert_sent,
    save_analysis,
    set_cursor,
)


# ── Cursor ──────────────────────────────────────────────────────────


async def test_initial_cursor_is_zero(state_db):
    assert await get_cursor() == 0


async def test_set_and_get_cursor(state_db):
    await set_cursor(42)
    assert await get_cursor() == 42


async def test_cursor_updates_in_place(state_db):
    await set_cursor(10)
    await set_cursor(20)
    assert await get_cursor() == 20


# ── Save / dedup ────────────────────────────────────────────────────


async def test_save_and_check_analyzed(state_db, sample_article, sample_analysis):
    assert await already_analyzed(42) is False
    await save_analysis(sample_article, sample_analysis)
    assert await already_analyzed(42) is True


async def test_not_analyzed_returns_false(state_db):
    assert await already_analyzed(999) is False


async def test_save_analysis_stores_fields(state_db, sample_article, sample_analysis):
    await save_analysis(sample_article, sample_analysis)
    rows = await get_recent_analyses(hours=24 * 365, limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["oksskolten_id"] == 42
    assert row["severity"] == "critical"
    assert row["relevance_score"] == 9
    assert row["title"] == sample_article["title"]


async def test_save_analysis_replace_on_conflict(state_db, sample_article):
    """INSERT OR REPLACE should update if same oksskolten_id."""
    await save_analysis(sample_article, {"severity": "high", "relevance_score": 5, "summary_vi": "v1"})
    await save_analysis(sample_article, {"severity": "critical", "relevance_score": 9, "summary_vi": "v2"})

    rows = await get_recent_analyses(hours=24 * 365, limit=10)
    assert len(rows) == 1
    assert rows[0]["severity"] == "critical"


# ── Alert tracking ──────────────────────────────────────────────────


async def test_mark_alert_sent(state_db, sample_article, sample_analysis):
    await save_analysis(sample_article, sample_analysis)
    await mark_alert_sent(42, ["telegram", "slack"])

    rows = await get_recent_analyses(hours=24 * 365, limit=10)
    row = rows[0]
    assert row["alert_sent"] == 1
    assert "telegram" in row["alert_channels"]
    assert "slack" in row["alert_channels"]


# ── Recent analyses ─────────────────────────────────────────────────


async def test_recent_analyses_ordered_by_relevance(state_db):
    for i, score in enumerate([3, 9, 5], start=1):
        article = {"id": i, "title": f"Art {i}", "url": "", "feed_name": "Feed"}
        analysis = {"severity": "high", "relevance_score": score, "summary_vi": "test"}
        await save_analysis(article, analysis)

    rows = await get_recent_analyses(hours=24 * 365, limit=10)
    scores = [r["relevance_score"] for r in rows]
    assert scores == [9, 5, 3]


async def test_recent_analyses_respects_limit(state_db):
    for i in range(10):
        article = {"id": i, "title": f"Art {i}", "url": "", "feed_name": "Feed"}
        analysis = {"severity": "info", "relevance_score": 1, "summary_vi": "t"}
        await save_analysis(article, analysis)

    rows = await get_recent_analyses(hours=24 * 365, limit=3)
    assert len(rows) == 3
