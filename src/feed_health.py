"""Feed health monitoring for Oksskolten feeds.

Reads the feeds table in Oksskolten's SQLite (read-only), detects feeds
with fetch errors above a threshold, and sends a batched Telegram alert.

Dedup: only alerts when a feed's last_error message changes (i.e., not
the same error every cycle). Recovered feeds are cleared from tracking.
"""

import asyncio
import logging
import os
import sqlite3
from typing import Optional

import httpx

from src.state import clear_feed_alert, get_feed_alert, save_feed_alert

logger = logging.getLogger(__name__)


async def get_failing_feeds(db_path: str, threshold: int) -> list[dict]:
    """Read feeds with error_count >= threshold from Oksskolten DB."""

    def _query() -> list[dict]:
        if not os.path.exists(db_path):
            return []
        uri = f"file:{db_path}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            logger.error(f"Failed to open Oksskolten DB: {e}")
            return []
        try:
            cursor = conn.execute(
                """SELECT id, name, url, rss_url, error_count, last_error, disabled
                   FROM feeds
                   WHERE error_count >= ? AND last_error IS NOT NULL AND disabled = 0
                   ORDER BY error_count DESC""",
                (threshold,),
            )
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Feed health query error: {e}")
            return []
        finally:
            conn.close()

    return await asyncio.to_thread(_query)


async def get_all_feed_ids(db_path: str) -> set[int]:
    """Get all feed IDs currently healthy (error_count = 0) for recovery detection."""

    def _query() -> set[int]:
        if not os.path.exists(db_path):
            return set()
        uri = f"file:{db_path}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=10)
        except sqlite3.Error:
            return set()
        try:
            cursor = conn.execute(
                "SELECT id FROM feeds WHERE error_count = 0 OR last_error IS NULL"
            )
            return {row[0] for row in cursor.fetchall()}
        except sqlite3.Error:
            return set()
        finally:
            conn.close()

    return await asyncio.to_thread(_query)


async def send_feed_errors_alert(new_errors: list[dict]) -> None:
    """Send batched Telegram alert for new feed errors."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return

    lines = [
        f"\u26a0\ufe0f <b>[FEED ERRORS]</b> {len(new_errors)} feed(s) failing\n",
    ]
    for feed in new_errors:
        name = feed["name"]
        err = (feed["last_error"] or "unknown")[:150]
        count = feed["error_count"]
        lines.append(f"\n\u2022 <b>{_escape(name)}</b> (errors: {count})")
        lines.append(f"  <code>{_escape(err)}</code>")

    lines.append("\n\nCheck Oksskolten feeds settings to fix or disable.")
    text = "\n".join(lines)

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            if resp.status_code == 200:
                logger.info(f"Feed error alert sent: {len(new_errors)} feeds")
            else:
                logger.warning(f"Feed alert failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Feed alert send error: {e}")


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


async def check_and_alert(db_path: Optional[str] = None) -> dict:
    """Check Oksskolten feeds health and alert on new errors.

    Returns stats dict: {"failing": int, "new_alerts": int, "recovered": int}
    """
    db_path = db_path or os.getenv(
        "OKSSKOLTEN_DB_PATH", "/oksskolten-data/oksskolten.db"
    )
    threshold = int(os.getenv("FEED_ERROR_THRESHOLD", "3"))

    failing = await get_failing_feeds(db_path, threshold)
    stats = {"failing": len(failing), "new_alerts": 0, "recovered": 0}

    # Detect recoveries: feeds we previously alerted that are now healthy
    healthy_ids = await get_all_feed_ids(db_path)
    # We can't easily iterate all prior alerts without another query, but
    # for each failing feed we check dedup. For recoveries, check against
    # known-healthy ids that we've recorded alerts for.
    for feed_id in healthy_ids:
        prior = await get_feed_alert(feed_id)
        if prior:
            logger.info(f"Feed recovered: {prior['feed_name']}")
            await clear_feed_alert(feed_id)
            stats["recovered"] += 1

    # Detect new/changed errors
    new_errors = []
    for feed in failing:
        prior = await get_feed_alert(feed["id"])
        is_new = prior is None
        is_changed = prior and prior["last_error"] != feed["last_error"]
        if is_new or is_changed:
            new_errors.append(feed)
            await save_feed_alert(
                feed["id"], feed["name"], feed["last_error"], feed["error_count"]
            )

    if new_errors:
        await send_feed_errors_alert(new_errors)
        stats["new_alerts"] = len(new_errors)
        logger.warning(
            f"Feed health: {len(new_errors)} new error(s) out of {len(failing)} failing"
        )
    elif failing:
        logger.info(f"Feed health: {len(failing)} feeds still failing (already alerted)")
    else:
        logger.info("Feed health: all feeds OK")

    return stats
