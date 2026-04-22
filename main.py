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


async def _run_startup_healthcheck():
    """Run healthcheck on daemon startup; exit if FAIL_FAST and any check failed."""
    from src.healthcheck import (
        format_report_text,
        healthcheck_fail_fast_enabled,
        healthcheck_on_startup_enabled,
        run_healthcheck,
    )
    from src.health_checks.types import CheckStatus

    if not healthcheck_on_startup_enabled():
        logger.info("Startup healthcheck disabled (HEALTHCHECK_ON_STARTUP=false)")
        return

    logger.info("Running startup healthcheck...")
    report = await run_healthcheck(live=True)
    logger.info(
        "Healthcheck finished: overall=%s exit_code=%d duration_ms=%d",
        report.overall_status.value,
        report.exit_code,
        report.duration_ms,
    )
    # Always log the full table at info level for operator visibility.
    for line in format_report_text(report, color=False).splitlines():
        logger.info("  %s", line)

    if (
        report.overall_status == CheckStatus.FAIL
        and healthcheck_fail_fast_enabled()
    ):
        logger.error(
            "Startup healthcheck failed and HEALTHCHECK_FAIL_FAST=true — aborting"
        )
        sys.exit(1)


async def cmd_run():
    """Run one cycle and exit."""
    await init_state_db()
    pipeline = Pipeline()
    await pipeline.run_cycle()


async def cmd_daemon():
    """Run as scheduled daemon."""
    await init_state_db()

    await _run_startup_healthcheck()

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

    # Show balance if supported
    from src.balance import check_balance
    provider = os.getenv("AI_PROVIDER", "anthropic").lower()
    info = await check_balance(provider)
    if info:
        status = "OK" if info["is_available"] else "UNAVAILABLE"
        print(f"AI Provider: {provider} [{status}]")
        print(f"Balance: {info['balance']:.2f} {info['currency']}")
    else:
        print(f"AI Provider: {provider} (balance check not supported)")
    print()


async def cmd_feeds():
    """Check Oksskolten feed health."""
    await init_state_db()
    from src.feed_health import check_and_alert, get_failing_feeds

    db_path = os.getenv("OKSSKOLTEN_DB_PATH", "/oksskolten-data/oksskolten.db")
    threshold = int(os.getenv("FEED_ERROR_THRESHOLD", "3"))
    failing = await get_failing_feeds(db_path, threshold)

    print(f"\n=== Feed Health ===")
    print(f"Threshold: {threshold} errors")
    print(f"Failing feeds: {len(failing)}")
    print()

    if failing:
        print(f"{'Feed':<35} {'Errors':<8} {'Last error':<50}")
        print("-" * 95)
        for f in failing:
            name = (f["name"] or "")[:33]
            err = (f["last_error"] or "")[:48]
            print(f"{name:<35} {f['error_count']:<8} {err}")
        print()

    # Run alert check (will send Telegram if new errors)
    stats = await check_and_alert()
    print(f"New alerts sent: {stats['new_alerts']}")
    print(f"Recovered feeds: {stats['recovered']}")
    print()


async def cmd_dedup_metrics():
    """Compute and print dedup shadow log metrics."""
    await init_state_db()
    from src.metrics import compute_metrics, format_report

    days = 14
    json_mode = False
    for arg in sys.argv[2:]:
        if arg.startswith("--days="):
            days = int(arg.split("=", 1)[1])
        elif arg == "--json":
            json_mode = True

    metrics = await compute_metrics(days=days)

    if json_mode:
        print(json.dumps(metrics.to_dict(), indent=2))
    else:
        print()
        print(format_report(metrics))
        print()


async def cmd_healthcheck():
    """Validate env + live connectivity; return meaningful exit code."""
    from src.healthcheck import (
        format_report_json,
        format_report_text,
        run_healthcheck,
    )

    live = True
    json_mode = False
    only: frozenset[str] | None = None

    for arg in sys.argv[2:]:
        if arg == "--no-live":
            live = False
        elif arg == "--json":
            json_mode = True
        elif arg.startswith("--only="):
            prefixes = [p.strip() for p in arg.split("=", 1)[1].split(",") if p.strip()]
            only = frozenset(prefixes) if prefixes else None

    report = await run_healthcheck(live=live, only=only)

    if json_mode:
        print(format_report_json(report))
    else:
        print()
        print(format_report_text(report))
        print()

    sys.exit(report.exit_code)


async def cmd_profile_gen():
    """Generate company_profile.yaml from description files via LLM."""
    from pathlib import Path

    from src.profile_generator import (
        DEFAULT_INPUT_DIR,
        DEFAULT_OUTPUT,
        generate_profile,
        save_profile,
    )

    input_dir = DEFAULT_INPUT_DIR
    output = DEFAULT_OUTPUT
    overwrite = False
    for arg in sys.argv[2:]:
        if arg.startswith("--input-dir="):
            input_dir = Path(arg.split("=", 1)[1])
        elif arg.startswith("--output="):
            output = Path(arg.split("=", 1)[1])
        elif arg == "--overwrite":
            overwrite = True

    try:
        profile = await generate_profile(input_dir)
        path = save_profile(profile, output, overwrite=overwrite)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        logger.error("profile-gen failed: %s", exc)
        sys.exit(1)

    print(f"\n✓ Generated: {path}")
    print(
        "  Review the file and rename to config/company_profile.yaml when ready.\n"
    )


async def cmd_balance():
    """Check and display AI provider balance."""
    from src.balance import check_balance
    provider = os.getenv("AI_PROVIDER", "anthropic").lower()
    info = await check_balance(provider)

    if not info:
        print(f"Balance check not supported for provider: {provider}")
        print("Supported: deepseek")
        return

    status = "OK" if info["is_available"] else "UNAVAILABLE"
    threshold = float(os.getenv("BALANCE_ALERT_THRESHOLD", "2"))
    print(f"\n=== {provider.upper()} Balance ===")
    print(f"Status:    {status}")
    print(f"Balance:   {info['balance']:.2f} {info['currency']}")
    print(f"Threshold: {threshold:.2f} {info['currency']}")
    if info["balance"] < threshold:
        print(f"⚠️  LOW BALANCE — below threshold!")
    else:
        print(f"✓  Balance OK")
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
  python main.py balance    Check AI provider balance
  python main.py feeds      Check Oksskolten feed health (fetch errors)
  python main.py dedup-metrics [--days=14] [--json]
                            Show semantic dedup shadow-log metrics
  python main.py healthcheck [--no-live] [--json] [--only=env,ai_provider]
                            Validate env + live connectivity to all services
  python main.py profile-gen [--input-dir=DIR] [--output=FILE] [--overwrite]
                            Generate company_profile.yaml from config/inputs/*
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
        "balance": cmd_balance,
        "feeds": cmd_feeds,
        "dedup-metrics": cmd_dedup_metrics,
        "healthcheck": cmd_healthcheck,
        "profile-gen": cmd_profile_gen,
    }

    if command in commands:
        await commands[command]()
    else:
        print_usage()


if __name__ == "__main__":
    asyncio.run(main())
