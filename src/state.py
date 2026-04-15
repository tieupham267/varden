"""State tracking for sidecar - separate DB from Oksskolten.

Tracks:
- Last processed Oksskolten article ID (cursor for incremental polling)
- Analysis results (cache to avoid re-analyzing)
- Alert dedup (don't spam same article twice)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

STATE_DB = os.getenv("STATE_DB_PATH", "data/varden_state.db")


async def init_state_db():
    """Initialize the state database."""
    os.makedirs(os.path.dirname(STATE_DB), exist_ok=True)
    async with aiosqlite.connect(STATE_DB) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS cursor (
                key TEXT PRIMARY KEY,
                value INTEGER,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS analyzed_articles (
                oksskolten_id INTEGER PRIMARY KEY,
                title TEXT,
                url TEXT,
                feed_name TEXT,
                analyzed_at TEXT NOT NULL,
                severity TEXT,
                relevance_score INTEGER,
                summary_vi TEXT,
                analysis_json TEXT,
                alert_sent INTEGER DEFAULT 0,
                alert_channels TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_analyzed_severity
                ON analyzed_articles(severity);
            CREATE INDEX IF NOT EXISTS idx_analyzed_relevance
                ON analyzed_articles(relevance_score);
            CREATE INDEX IF NOT EXISTS idx_analyzed_time
                ON analyzed_articles(analyzed_at);

            CREATE TABLE IF NOT EXISTS feed_error_alerts (
                feed_id INTEGER PRIMARY KEY,
                feed_name TEXT,
                last_error TEXT,
                error_count INTEGER,
                alerted_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dedup_shadow_log (
                article_id INTEGER PRIMARY KEY,
                matched_article_id INTEGER,
                dice_score REAL,
                would_merge INTEGER,
                matched_already_analyzed INTEGER,
                relevance_score INTEGER,
                severity TEXT,
                cves_json TEXT,
                matched_relevance_score INTEGER,
                matched_severity TEXT,
                matched_cves_json TEXT,
                audit_label TEXT,
                audit_note TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_shadow_created
                ON dedup_shadow_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_shadow_would_merge
                ON dedup_shadow_log(would_merge);
            CREATE INDEX IF NOT EXISTS idx_shadow_audit
                ON dedup_shadow_log(audit_label);
        """)
        await db.commit()


async def get_cursor() -> int:
    """Get last processed Oksskolten article ID."""
    async with aiosqlite.connect(STATE_DB) as db:
        cursor = await db.execute(
            "SELECT value FROM cursor WHERE key = 'last_article_id'"
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def set_cursor(article_id: int):
    """Update cursor to the latest processed article ID."""
    async with aiosqlite.connect(STATE_DB) as db:
        await db.execute(
            """INSERT INTO cursor (key, value, updated_at)
               VALUES ('last_article_id', ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 updated_at = excluded.updated_at""",
            (article_id, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def save_analysis(article: dict, analysis: dict):
    """Save AI analysis result for an article."""
    async with aiosqlite.connect(STATE_DB) as db:
        await db.execute(
            """INSERT OR REPLACE INTO analyzed_articles
               (oksskolten_id, title, url, feed_name, analyzed_at,
                severity, relevance_score, summary_vi, analysis_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                article["id"],
                article.get("title", ""),
                article.get("url", ""),
                article.get("feed_name", ""),
                datetime.now(timezone.utc).isoformat(),
                analysis.get("severity", "info"),
                analysis.get("relevance_score", 0),
                analysis.get("summary_vi", ""),
                json.dumps(analysis, ensure_ascii=False),
            ),
        )
        await db.commit()


async def mark_alert_sent(article_id: int, channels: list[str]):
    """Mark alert as sent to avoid duplicates."""
    async with aiosqlite.connect(STATE_DB) as db:
        await db.execute(
            """UPDATE analyzed_articles
               SET alert_sent = 1, alert_channels = ?
               WHERE oksskolten_id = ?""",
            (json.dumps(channels), article_id),
        )
        await db.commit()


async def already_analyzed(article_id: int) -> bool:
    """Check if article has been analyzed already."""
    async with aiosqlite.connect(STATE_DB) as db:
        cursor = await db.execute(
            "SELECT 1 FROM analyzed_articles WHERE oksskolten_id = ?",
            (article_id,),
        )
        return await cursor.fetchone() is not None


async def get_recent_analyses(hours: int = 24, limit: int = 100) -> list[dict]:
    """Get recent analyses for digest reports."""
    async with aiosqlite.connect(STATE_DB) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM analyzed_articles
               WHERE analyzed_at >= datetime('now', ? || ' hours')
               ORDER BY relevance_score DESC, analyzed_at DESC
               LIMIT ?""",
            (f"-{hours}", limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# ── Feed error alert dedup ──────────────────────────────────────────


async def get_feed_alert(feed_id: int) -> Optional[dict]:
    """Get previously-alerted error for a feed, or None."""
    async with aiosqlite.connect(STATE_DB) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM feed_error_alerts WHERE feed_id = ?",
            (feed_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def save_feed_alert(
    feed_id: int, feed_name: str, last_error: str, error_count: int
) -> None:
    """Record that we alerted for this feed error."""
    async with aiosqlite.connect(STATE_DB) as db:
        await db.execute(
            """INSERT OR REPLACE INTO feed_error_alerts
               (feed_id, feed_name, last_error, error_count, alerted_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                feed_id,
                feed_name,
                last_error,
                error_count,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()


async def clear_feed_alert(feed_id: int) -> None:
    """Remove alert record when feed recovers."""
    async with aiosqlite.connect(STATE_DB) as db:
        await db.execute(
            "DELETE FROM feed_error_alerts WHERE feed_id = ?",
            (feed_id,),
        )
        await db.commit()


# ── Dedup shadow logging (Phase 0) ──────────────────────────────────


async def get_analysis(article_id: int) -> Optional[dict]:
    """Retrieve previous analysis for an article, or None."""
    async with aiosqlite.connect(STATE_DB) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM analyzed_articles WHERE oksskolten_id = ?",
            (article_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def log_shadow_decision(
    article_id: int,
    matched_article_id: Optional[int],
    dice_score: Optional[float],
    would_merge: bool,
    matched_already_analyzed: bool,
    relevance_score: int,
    severity: str,
    cves: list[str],
    matched_relevance_score: Optional[int] = None,
    matched_severity: Optional[str] = None,
    matched_cves: Optional[list[str]] = None,
) -> None:
    """Record a dedup shadow decision. Called after AI analysis.

    Does not affect pipeline behavior — pure observability for Phase 0.
    """
    async with aiosqlite.connect(STATE_DB) as db:
        await db.execute(
            """INSERT OR REPLACE INTO dedup_shadow_log
               (article_id, matched_article_id, dice_score, would_merge,
                matched_already_analyzed, relevance_score, severity, cves_json,
                matched_relevance_score, matched_severity, matched_cves_json,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                article_id,
                matched_article_id,
                dice_score,
                1 if would_merge else 0,
                1 if matched_already_analyzed else 0,
                relevance_score,
                severity,
                json.dumps(cves, ensure_ascii=False),
                matched_relevance_score,
                matched_severity,
                json.dumps(matched_cves, ensure_ascii=False) if matched_cves is not None else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()


async def get_shadow_stats(days: int = 14) -> dict:
    """Aggregate shadow log stats for metrics computation."""
    async with aiosqlite.connect(STATE_DB) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT
                 COUNT(*) AS total,
                 SUM(CASE WHEN matched_article_id IS NOT NULL THEN 1 ELSE 0 END) AS with_match,
                 SUM(CASE WHEN would_merge = 1 THEN 1 ELSE 0 END) AS would_merge_count,
                 SUM(CASE WHEN audit_label = 'correct' THEN 1 ELSE 0 END) AS audit_correct,
                 SUM(CASE WHEN audit_label = 'false_merge' THEN 1 ELSE 0 END) AS audit_false,
                 SUM(CASE WHEN audit_label IS NOT NULL THEN 1 ELSE 0 END) AS audit_total
               FROM dedup_shadow_log
               WHERE created_at >= datetime('now', ? || ' days')""",
            (f"-{days}",),
        )
        row = await cursor.fetchone()
        return dict(row) if row else {}
