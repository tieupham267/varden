"""Notifier channel probes: Telegram, Slack, SMTP.

Design decisions (confirmed with user):
    - Telegram: read-only ``getMe`` — does not write to the chat
    - Slack: POST a real ping message to the webhook (only verification path
      Slack incoming webhooks support). Message is clearly labeled as a
      healthcheck so users can filter it.
    - SMTP: send a real email to ``EMAIL_TO`` (the only true end-to-end check).

Probes are SKIPPED when the channel's ``*_ENABLED`` flag is false.
"""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import socket
from email.mime.text import MIMEText
from time import perf_counter

import httpx

from src.health_checks.types import CheckResult, CheckStatus

logger = logging.getLogger(__name__)


def _timeout_seconds() -> float:
    try:
        return float(os.getenv("HEALTHCHECK_PROBE_TIMEOUT_SECONDS", "10"))
    except ValueError:
        return 10.0


def _env_bool(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() == "true"


def _elapsed_ms(start: float) -> int:
    return int((perf_counter() - start) * 1000)


def _hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "unknown-host"


async def probe_telegram() -> CheckResult:
    """Hit Telegram Bot API ``getMe`` (read-only)."""
    if not _env_bool("TELEGRAM_ENABLED"):
        return CheckResult(
            name="notifier.telegram",
            status=CheckStatus.SKIPPED,
            detail="TELEGRAM_ENABLED=false",
        )
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        return CheckResult(
            name="notifier.telegram",
            status=CheckStatus.FAIL,
            detail="TELEGRAM_BOT_TOKEN missing",
        )

    timeout = _timeout_seconds()
    start = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{bot_token}/getMe"
            )
        if resp.status_code == 200:
            try:
                data = resp.json()
                bot_name = data.get("result", {}).get("username", "?")
            except Exception:
                bot_name = "?"
            return CheckResult(
                name="notifier.telegram",
                status=CheckStatus.OK,
                latency_ms=_elapsed_ms(start),
                detail=f"bot=@{bot_name}",
            )
        if resp.status_code in (401, 403, 404):
            return CheckResult(
                name="notifier.telegram",
                status=CheckStatus.FAIL,
                latency_ms=_elapsed_ms(start),
                detail=f"HTTP {resp.status_code} (token rejected)",
                remediation="Verify TELEGRAM_BOT_TOKEN is correct",
            )
        return CheckResult(
            name="notifier.telegram",
            status=CheckStatus.WARN,
            latency_ms=_elapsed_ms(start),
            detail=f"HTTP {resp.status_code}",
        )
    except httpx.TimeoutException:
        return CheckResult(
            name="notifier.telegram",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail=f"timeout after {timeout}s",
        )
    except httpx.HTTPError as e:
        return CheckResult(
            name="notifier.telegram",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail=f"{type(e).__name__}: {str(e)[:120]}",
        )


async def probe_slack() -> CheckResult:
    """POST a small ping message to the configured Slack webhook.

    User asked for end-to-end verification rather than format-only validation.
    The message is clearly labeled as ``healthcheck ping`` so users can filter.
    """
    if not _env_bool("SLACK_ENABLED"):
        return CheckResult(
            name="notifier.slack",
            status=CheckStatus.SKIPPED,
            detail="SLACK_ENABLED=false",
        )
    webhook = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook:
        return CheckResult(
            name="notifier.slack",
            status=CheckStatus.FAIL,
            detail="SLACK_WEBHOOK_URL missing",
        )

    timeout = _timeout_seconds()
    start = perf_counter()
    payload = {
        "text": f":stethoscope: Varden healthcheck ping — {_hostname()}",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(webhook, json=payload)
        if 200 <= resp.status_code < 300:
            return CheckResult(
                name="notifier.slack",
                status=CheckStatus.OK,
                latency_ms=_elapsed_ms(start),
                detail=f"HTTP {resp.status_code}",
            )
        if 400 <= resp.status_code < 500:
            return CheckResult(
                name="notifier.slack",
                status=CheckStatus.FAIL,
                latency_ms=_elapsed_ms(start),
                detail=f"HTTP {resp.status_code} (webhook invalid or revoked)",
                remediation="Regenerate SLACK_WEBHOOK_URL",
            )
        return CheckResult(
            name="notifier.slack",
            status=CheckStatus.WARN,
            latency_ms=_elapsed_ms(start),
            detail=f"HTTP {resp.status_code}",
        )
    except httpx.TimeoutException:
        return CheckResult(
            name="notifier.slack",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail=f"timeout after {timeout}s",
        )
    except httpx.HTTPError as e:
        return CheckResult(
            name="notifier.slack",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail=f"{type(e).__name__}: {str(e)[:120]}",
        )


def _send_healthcheck_email(
    host: str,
    port: int,
    user: str,
    password: str,
    recipient: str,
    timeout: float,
) -> None:
    """Blocking SMTP send used via ``asyncio.to_thread``."""
    msg = MIMEText(
        f"This is a Varden healthcheck ping from {_hostname()}.\n"
        "If you received this, SMTP delivery is working.",
        "plain",
        "utf-8",
    )
    msg["Subject"] = f"[Varden Healthcheck] Ping from {_hostname()}"
    msg["From"] = user
    msg["To"] = recipient

    with smtplib.SMTP(host, port, timeout=timeout) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)


async def probe_email() -> CheckResult:
    """Send a real healthcheck email via SMTP."""
    if not _env_bool("EMAIL_ENABLED"):
        return CheckResult(
            name="notifier.email",
            status=CheckStatus.SKIPPED,
            detail="EMAIL_ENABLED=false",
        )

    host = os.getenv("SMTP_HOST", "")
    port_raw = os.getenv("SMTP_PORT", "")
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    recipient = os.getenv("EMAIL_TO", "")

    if not all([host, port_raw, user, password, recipient]):
        return CheckResult(
            name="notifier.email",
            status=CheckStatus.FAIL,
            detail="SMTP env incomplete; see env.email check",
        )
    try:
        port = int(port_raw)
    except ValueError:
        return CheckResult(
            name="notifier.email",
            status=CheckStatus.FAIL,
            detail=f"SMTP_PORT={port_raw!r} is not an integer",
        )

    timeout = _timeout_seconds()
    start = perf_counter()
    try:
        await asyncio.to_thread(
            _send_healthcheck_email,
            host,
            port,
            user,
            password,
            recipient,
            timeout,
        )
        return CheckResult(
            name="notifier.email",
            status=CheckStatus.OK,
            latency_ms=_elapsed_ms(start),
            detail=f"sent to {recipient} via {host}:{port}",
        )
    except smtplib.SMTPAuthenticationError:
        return CheckResult(
            name="notifier.email",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail="SMTP auth rejected",
            remediation="Verify SMTP_USER/SMTP_PASSWORD (use app password for Gmail)",
        )
    except (smtplib.SMTPException, OSError, TimeoutError) as e:
        return CheckResult(
            name="notifier.email",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail=f"{type(e).__name__}: {str(e)[:120]}",
        )
