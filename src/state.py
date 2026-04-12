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
