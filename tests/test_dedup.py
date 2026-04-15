"""Tests for Phase 0 dedup shadow logging.

Verifies:
- Oksskolten similarity query (bidirectional, threshold, missing table)
- Shadow decision logging (with/without matches, with/without prior analysis)
- Pipeline integration (log populated after analyze)
"""

import json
import sqlite3

import aiosqlite

from src.dedup import (
    _extract_cves,
    query_oksskolten_similarities,
    record_shadow_decision,
)
from src.state import get_shadow_stats, save_analysis


# ─── _extract_cves ────────────────────────────────────────────────


def test_extract_cves_from_cve_ids_field():
    analysis = {"cve_ids": ["CVE-2024-1", "cve-2024-2 "]}
    assert _extract_cves(analysis) == ["CVE-2024-1", "CVE-2024-2"]


def test_extract_cves_from_cves_field_fallback():
    analysis = {"cves": ["CVE-2024-9"]}
    assert _extract_cves(analysis) == ["CVE-2024-9"]


def test_extract_cves_empty_when_missing():
    assert _extract_cves({}) == []


def test_extract_cves_handles_non_list():
    assert _extract_cves({"cve_ids": "not a list"}) == []


# ─── query_oksskolten_similarities ────────────────────────────────


async def test_query_returns_empty_when_db_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("OKSSKOLTEN_DB_PATH", str(tmp_path / "nope.db"))
    result = await query_oksskolten_similarities(42)
    assert result == []


async def test_query_returns_empty_when_table_missing(monkeypatch, tmp_path):
    db_path = tmp_path / "oks.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE articles (id INTEGER)")
    conn.commit()
    conn.close()

    monkeypatch.setenv("OKSSKOLTEN_DB_PATH", str(db_path))
    result = await query_oksskolten_similarities(42)
    assert result == []


async def test_query_returns_matches_above_threshold(monkeypatch, oksskolten_db):
    conn = sqlite3.connect(oksskolten_db)
    conn.execute(
        "INSERT INTO article_similarities (article_id, similar_to_id, score) VALUES (?, ?, ?)",
        (10, 20, 0.75),
    )
    conn.execute(
        "INSERT INTO article_similarities (article_id, similar_to_id, score) VALUES (?, ?, ?)",
        (10, 30, 0.55),  # below default 0.6 threshold
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("OKSSKOLTEN_DB_PATH", oksskolten_db)
    result = await query_oksskolten_similarities(10, min_score=0.6)

    assert len(result) == 1
    assert result[0].matched_article_id == 20
    assert result[0].dice_score == 0.75


async def test_query_bidirectional_lookup(monkeypatch, oksskolten_db):
    """If article X is similar_to_id of a later article Y, lookup by X should find Y."""
    conn = sqlite3.connect(oksskolten_db)
    # article 50 was inserted LATER, with similarity to article 10
    conn.execute(
        "INSERT INTO article_similarities (article_id, similar_to_id, score) VALUES (?, ?, ?)",
        (50, 10, 0.8),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("OKSSKOLTEN_DB_PATH", oksskolten_db)
    # Looking up article 10 should still find article 50 as match
    result = await query_oksskolten_similarities(10)
    assert len(result) == 1
    assert result[0].matched_article_id == 50


async def test_query_sorts_by_score_desc(monkeypatch, oksskolten_db):
    conn = sqlite3.connect(oksskolten_db)
    conn.executemany(
        "INSERT INTO article_similarities (article_id, similar_to_id, score) VALUES (?, ?, ?)",
        [(1, 2, 0.7), (1, 3, 0.9), (1, 4, 0.8)],
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("OKSSKOLTEN_DB_PATH", oksskolten_db)
    result = await query_oksskolten_similarities(1)
    scores = [m.dice_score for m in result]
    assert scores == sorted(scores, reverse=True)


# ─── record_shadow_decision ──────────────────────────────────────


async def test_shadow_logs_no_match_when_empty(
    state_db, monkeypatch, tmp_path, sample_article, sample_analysis
):
    monkeypatch.setenv("OKSSKOLTEN_DB_PATH", str(tmp_path / "nope.db"))

    await record_shadow_decision(sample_article, sample_analysis)

    async with aiosqlite.connect(state_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM dedup_shadow_log WHERE article_id = ?",
            (sample_article["id"],),
        )
        row = dict(await cursor.fetchone())

    assert row["matched_article_id"] is None
    assert row["would_merge"] == 0
    assert row["relevance_score"] == 9
    assert row["severity"] == "critical"
    assert json.loads(row["cves_json"]) == ["CVE-2024-12345"]


async def test_shadow_logs_match_without_prior_analysis(
    state_db, monkeypatch, oksskolten_db, sample_article, sample_analysis
):
    conn = sqlite3.connect(oksskolten_db)
    conn.execute(
        "INSERT INTO article_similarities VALUES (?, ?, ?, ?)",
        (sample_article["id"], 99, 0.82, "2024-01-01"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("OKSSKOLTEN_DB_PATH", oksskolten_db)

    await record_shadow_decision(sample_article, sample_analysis)

    async with aiosqlite.connect(state_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM dedup_shadow_log WHERE article_id = ?",
            (sample_article["id"],),
        )
        row = dict(await cursor.fetchone())

    assert row["matched_article_id"] == 99
    assert row["dice_score"] == 0.82
    assert row["would_merge"] == 0  # matched article not analyzed yet
    assert row["matched_already_analyzed"] == 0
    assert row["matched_relevance_score"] is None


async def test_shadow_logs_would_merge_when_matched_already_analyzed(
    state_db, monkeypatch, oksskolten_db, sample_article, sample_analysis
):
    # Save analysis for article 99 first (matched article)
    prior_article = {
        "id": 99,
        "title": "Earlier version of same story",
        "url": "https://example.com/99",
        "feed_name": "OtherFeed",
    }
    prior_analysis = {
        "relevance_score": 8,
        "severity": "high",
        "summary_vi": "Tin cu",
        "cve_ids": ["CVE-2024-12345"],
    }
    await save_analysis(prior_article, prior_analysis)

    conn = sqlite3.connect(oksskolten_db)
    conn.execute(
        "INSERT INTO article_similarities VALUES (?, ?, ?, ?)",
        (sample_article["id"], 99, 0.88, "2024-01-01"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("OKSSKOLTEN_DB_PATH", oksskolten_db)

    await record_shadow_decision(sample_article, sample_analysis)

    async with aiosqlite.connect(state_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM dedup_shadow_log WHERE article_id = ?",
            (sample_article["id"],),
        )
        row = dict(await cursor.fetchone())

    assert row["matched_article_id"] == 99
    assert row["would_merge"] == 1  # prior analysis exists → would merge
    assert row["matched_already_analyzed"] == 1
    assert row["matched_relevance_score"] == 8
    assert row["matched_severity"] == "high"
    assert json.loads(row["matched_cves_json"]) == ["CVE-2024-12345"]


async def test_shadow_decision_silent_on_error(
    state_db, monkeypatch, sample_article, sample_analysis
):
    """Any exception in shadow logging should not propagate."""
    async def boom(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr("src.state.log_shadow_decision", boom)

    # Must not raise
    await record_shadow_decision(sample_article, sample_analysis)


# ─── get_shadow_stats ────────────────────────────────────────────


async def test_shadow_stats_counts_entries(state_db, sample_article, sample_analysis):
    from src.state import log_shadow_decision

    await log_shadow_decision(
        article_id=1, matched_article_id=None, dice_score=None,
        would_merge=False, matched_already_analyzed=False,
        relevance_score=5, severity="medium", cves=[],
    )
    await log_shadow_decision(
        article_id=2, matched_article_id=1, dice_score=0.9,
        would_merge=True, matched_already_analyzed=True,
        relevance_score=7, severity="high", cves=["CVE-X"],
    )

    stats = await get_shadow_stats(days=30)

    assert stats["total"] == 2
    assert stats["with_match"] == 1
    assert stats["would_merge_count"] == 1
    assert stats["audit_total"] == 0
