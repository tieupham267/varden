"""Pipeline - the main processing loop.

Flow:
1. Read cursor (last processed Oksskolten article ID)
2. Fetch new articles from Oksskolten (SQLite or API)
3. For each: skip if already analyzed → call AI → save result
4. If relevance >= threshold AND severity matches → dispatch alert
5. Advance cursor
"""

import asyncio
import logging
import os

from src.ai_analyzer import AIAnalyzer
from src.balance import check_and_alert as check_balance_alert
from src.dedup import record_shadow_decision
from src.feed_health import check_and_alert as check_feed_health
from src.notifier import dispatch_alert
from src.source import OksskoltenSource
from src.state import (
    already_analyzed,
    get_cursor,
    mark_alert_sent,
    save_analysis,
    set_cursor,
)

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self):
        self.source = OksskoltenSource()
        self.analyzer = AIAnalyzer()
        self.alert_threshold = int(os.getenv("ALERT_THRESHOLD", "7"))
        self.alert_severities = set(
            s.strip().lower()
            for s in os.getenv("ALERT_SEVERITIES", "critical,high").split(",")
        )
        self.batch_size = int(os.getenv("BATCH_SIZE", "20"))

    async def run_cycle(self):
        """Execute one full processing cycle."""
        logger.info("=" * 60)
        logger.info("Starting pipeline cycle")

        # Step 1: Read cursor
        cursor = await get_cursor()
        logger.info(f"Cursor: last processed article ID = {cursor}")

        # Step 2: Fetch new articles
        articles = await self.source.fetch_new_articles(
            since_id=cursor if cursor > 0 else None,
            limit=self.batch_size,
        )
        if not articles:
            logger.info("No new articles to process")
            return

        logger.info(f"Fetched {len(articles)} new articles")

        # Step 3 & 4: Analyze each, alert if relevant
        stats = {
            "analyzed": 0,
            "skipped": 0,
            "failed": 0,
            "alerted": 0,
            "max_id": cursor,
        }

        for article in articles:
            article_id = article["id"]
            stats["max_id"] = max(stats["max_id"], article_id)

            # Skip if already analyzed (resume safety)
            if await already_analyzed(article_id):
                stats["skipped"] += 1
                continue

            # Analyze with AI
            logger.info(f"  Analyzing #{article_id}: {article['title'][:60]}...")
            try:
                analysis = await self.analyzer.analyze(article)
            except Exception as e:
                logger.error(f"  Analysis crashed: {e}")
                stats["failed"] += 1
                continue

            if not analysis:
                stats["failed"] += 1
                continue

            # Save result
            await save_analysis(article, analysis)
            stats["analyzed"] += 1

            # Shadow-log dedup decision (Phase 0, observability only)
            await record_shadow_decision(article, analysis)

            score = analysis.get("relevance_score", 0)
            sev = analysis.get("severity", "info").lower()
            logger.info(
                f"    → relevance={score}/10, severity={sev}, "
                f"reason: {analysis.get('relevance_reason', '')[:80]}"
            )

            # Dispatch alert if above threshold
            should_alert = (
                score >= self.alert_threshold
                and sev in self.alert_severities
            )
            if should_alert:
                logger.info(f"    → ALERTING (score {score} >= {self.alert_threshold})")
                channels = await dispatch_alert(article, analysis)
                if channels:
                    await mark_alert_sent(article_id, channels)
                    stats["alerted"] += 1

            # Small delay to avoid rate limits
            await asyncio.sleep(0.5)

        # Step 5: Advance cursor
        if stats["max_id"] > cursor:
            await set_cursor(stats["max_id"])
            logger.info(f"Cursor advanced to {stats['max_id']}")

        logger.info(
            f"Cycle complete: analyzed={stats['analyzed']}, "
            f"alerted={stats['alerted']}, skipped={stats['skipped']}, "
            f"failed={stats['failed']}"
        )

        # Check provider balance after processing
        await check_balance_alert(os.getenv("AI_PROVIDER", "anthropic").lower())

        # Check Oksskolten feed health
        await check_feed_health()
