"""Varden - Main entry point.

Commands:
  python main.py run        # Single cycle (testing)
  python main.py daemon     # Scheduled daemon (production)
  python main.py digest     # Send email digest of last 24h
  python main.py status     # Show current state
"""

import asyncio
import json
import logging
import os
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from src.pipeline import Pipeline
from src.state import init_state_db, get_cursor, get_recent_analyses

load_dotenv()

os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.getenv("LOG_FILE_PATH", "data/varden.log"), encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("sidecar")


async def cmd_run():
    """Run one cycle and exit."""
    await init_state_db()
    pipeline = Pipeline()
    await pipeline.run_cycle()


async def cmd_daemon():
    """Run as scheduled daemon."""
    await init_state_db()
    interval = int(os.getenv("POLL_INTERVAL_MINUTES", "15"))
    pipeline = Pipeline()

    logger.info(f"Starting daemon mode (poll every {interval} minutes)")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        pipeline.run_cycle,
        "interval",
        minutes=interval,
        id="pipeline",
        max_instances=1,
        coalesce=True,
    )

    # Optional: daily email digest at 8:00 UTC (15:00 VN)
    if os.getenv("EMAIL_ENABLED", "false").lower() == "true":
        scheduler.add_job(
            send_daily_digest,
            "cron",
            hour=8,
            minute=0,
            id="daily_digest",
            max_instances=1,
        )
        logger.info("Daily email digest enabled at 08:00 UTC")

    scheduler.start()

    # Run immediately on startup
    await pipeline.run_cycle()

    # Keep alive
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
        scheduler.shutdown()


async def send_daily_digest():
    """Send email digest of the last 24h."""
    from src.notifier import send_email_digest

    analyses = await get_recent_analyses(hours=24, limit=50)
    if not analyses:
        logger.info("No articles for daily digest")
        return

    # Reconstruct article + analysis pairs
    items = []
    for row in analyses:
        article = {
            "id": row["oksskolten_id"],
            "title": row["title"],
            "url": row["url"],
            "feed_name": row["feed_name"],
        }
        try:
            analysis = json.loads(row["analysis_json"])
        except (json.JSONDecodeError, TypeError):
            analysis = {
                "summary_vi": row["summary_vi"],
                "severity": row["severity"],
                "relevance_score": row["relevance_score"],
            }
        items.append((article, analysis))

    await send_email_digest(items)


async def cmd_digest():
    """Manually send digest for last 24h."""
    await init_state_db()
    await send_daily_digest()


async def cmd_status():
    """Print current state."""
    await init_state_db()
    cursor = await get_cursor()
    recent = await get_recent_analyses(hours=24, limit=10)

    print(f"\n=== Varden Status ===")
    print(f"Last processed article ID: {cursor}")
    print(f"Analyses in last 24h: {len(recent)}")
    print()

    if recent:
        print("Top 10 by relevance (last 24h):")
        print("-" * 70)
        for r in recent[:10]:
            score = r.get("relevance_score", 0)
            sev = (r.get("severity") or "info")[:8]
            title = (r.get("title") or "")[:55]
            print(f"  [{sev:<8}] {score}/10  {title}")
    print()


BANNER = r"""
   __      __            _
   \ \    / /_ _ _ _ __| |___ _ _
    \ \/\/ / _` | '_/ _` / -_) ' \
     \_/\_/\__,_|_| \__,_\___|_||_|

   Threat intel cairns for Oksskolten
"""

def print_usage():
    print(BANNER)
    print("""Varden - AI-powered threat intel automation layer

Usage:
  python main.py run        Run one processing cycle (testing)
  python main.py daemon     Run as scheduled daemon (production)
  python main.py digest     Send email digest for last 24h
  python main.py status     Show current state and recent analyses
""")


async def main():
    if len(sys.argv) < 2:
        print_usage()
        return

    command = sys.argv[1]
    commands = {
        "run": cmd_run,
        "daemon": cmd_daemon,
        "digest": cmd_digest,
        "status": cmd_status,
    }

    if command in commands:
        await commands[command]()
    else:
        print_usage()


if __name__ == "__main__":
    asyncio.run(main())
