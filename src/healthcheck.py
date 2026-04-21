"""Healthcheck orchestrator.

Runs all registered checks (env validation + live probes) in parallel when
possible, returns an aggregated :class:`HealthReport`.

Called from:
    - ``python main.py healthcheck`` CLI
    - daemon startup gate (``HEALTHCHECK_ON_STARTUP=true``)
    - Docker ``HEALTHCHECK`` (``--no-live --json``)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

from src.health_checks.env_validator import check_env_completeness
from src.health_checks.types import (
    CheckResult,
    CheckStatus,
    HealthReport,
    scrub_secrets,
)

logger = logging.getLogger(__name__)


def _status_symbol(status: CheckStatus) -> str:
    return {
        CheckStatus.OK: "OK",
        CheckStatus.WARN: "WARN",
        CheckStatus.FAIL: "FAIL",
        CheckStatus.SKIPPED: "SKIP",
    }[status]


def _ansi_color(status: CheckStatus, enabled: bool) -> tuple[str, str]:
    if not enabled:
        return "", ""
    codes = {
        CheckStatus.OK: "\033[32m",
        CheckStatus.WARN: "\033[33m",
        CheckStatus.FAIL: "\033[31m",
        CheckStatus.SKIPPED: "\033[90m",
    }
    return codes.get(status, ""), "\033[0m"


def _scrub_result(result: CheckResult) -> CheckResult:
    """Apply :func:`scrub_secrets` to detail/remediation fields."""
    scrubbed_detail = scrub_secrets(result.detail)
    scrubbed_remed = (
        scrub_secrets(result.remediation) if result.remediation else None
    )
    if scrubbed_detail == result.detail and scrubbed_remed == result.remediation:
        return result
    return CheckResult(
        name=result.name,
        status=result.status,
        latency_ms=result.latency_ms,
        detail=scrubbed_detail,
        remediation=scrubbed_remed,
    )


async def _collect_live_probes() -> list[CheckResult]:
    """Gather live connectivity probe results in parallel.

    Imports are local so Phase 1 CLI works even if Phase 2 modules are absent.
    """
    try:
        from src.health_checks.ai_probes import probe_ai_provider
        from src.health_checks.notifier_probes import (
            probe_email,
            probe_slack,
            probe_telegram,
        )
        from src.health_checks.source_probe import probe_oksskolten_source
    except ImportError as e:
        logger.warning("Live probes unavailable: %s", e)
        return []

    probes = [
        probe_ai_provider(),
        probe_telegram(),
        probe_slack(),
        probe_email(),
        probe_oksskolten_source(),
    ]
    results = await asyncio.gather(*probes, return_exceptions=True)

    normalized: list[CheckResult] = []
    for idx, item in enumerate(results):
        if isinstance(item, CheckResult):
            normalized.append(item)
        elif isinstance(item, BaseException):
            normalized.append(
                CheckResult(
                    name=f"probe.unknown[{idx}]",
                    status=CheckStatus.FAIL,
                    detail=f"Probe raised {type(item).__name__}: {item!s}"[:200],
                )
            )
    return normalized


async def run_healthcheck(
    *,
    live: bool = True,
    only: frozenset[str] | None = None,
) -> HealthReport:
    """Run the full healthcheck suite.

    Args:
        live: When False, skip live connectivity probes (env-only mode).
        only: Optional filter; when set, drop any result whose ``name`` does
            not start with one of these prefixes (e.g. ``{"env", "ai_provider"}``).
    """
    started_at = datetime.now(timezone.utc)

    env_results = await check_env_completeness()
    live_results = await _collect_live_probes() if live else []

    all_results = [_scrub_result(r) for r in env_results + live_results]

    if only:
        all_results = [
            r for r in all_results if any(r.name.startswith(p) for p in only)
        ]

    finished_at = datetime.now(timezone.utc)
    return HealthReport(
        results=tuple(all_results),
        started_at=started_at,
        finished_at=finished_at,
    )


def format_report_text(report: HealthReport, *, color: bool | None = None) -> str:
    """Render a human-readable table."""
    use_color = sys.stdout.isatty() if color is None else color

    header = f"{'CHECK':<28} {'STATUS':<6} {'LATENCY':<10} DETAIL"
    sep = "-" * 88
    lines = [header, sep]

    for r in report.results:
        start, end = _ansi_color(r.status, use_color)
        status = f"{start}{_status_symbol(r.status):<6}{end}"
        latency = f"{r.latency_ms}ms" if r.latency_ms is not None else "-"
        detail = r.detail or ""
        lines.append(f"{r.name:<28} {status} {latency:<10} {detail}")
        if r.remediation and r.status in (CheckStatus.FAIL, CheckStatus.WARN):
            lines.append(f"{'':<28} {'':<6} {'':<10} → {r.remediation}")

    lines.append(sep)
    overall_start, overall_end = _ansi_color(report.overall_status, use_color)
    lines.append(
        f"Overall: {overall_start}{_status_symbol(report.overall_status)}{overall_end} "
        f"({report.duration_ms}ms total) · exit={report.exit_code}"
    )
    return "\n".join(lines)


def format_report_json(report: HealthReport) -> str:
    """Render the report as a JSON string (stable keys, pretty indent)."""
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


def healthcheck_on_startup_enabled() -> bool:
    return os.getenv("HEALTHCHECK_ON_STARTUP", "true").strip().lower() == "true"


def healthcheck_fail_fast_enabled() -> bool:
    return os.getenv("HEALTHCHECK_FAIL_FAST", "false").strip().lower() == "true"
