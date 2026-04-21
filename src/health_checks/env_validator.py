"""Environment variable completeness validator.

Conditional rules: we only require keys for features the user has opted
into. For example, ``TELEGRAM_BOT_TOKEN`` is only required when
``TELEGRAM_ENABLED=true``.

This check never touches the network and never logs secret values — it
only reports whether each required key is present or missing.
"""

from __future__ import annotations

import os

from src.ai_providers import (
    ALL_PROVIDERS,
    OPENAI_COMPAT_PRESETS,
)
from src.health_checks.types import CheckResult, CheckStatus


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() == "true"


def _present(name: str) -> bool:
    value = os.getenv(name, "").strip()
    return bool(value)


def _missing_keys(keys: list[str]) -> list[str]:
    return [k for k in keys if not _present(k)]


def _check_ai_provider_env() -> CheckResult:
    """Validate AI_PROVIDER is set and its required keys are present."""
    provider = os.getenv("AI_PROVIDER", "").strip().lower()

    if not provider:
        return CheckResult(
            name="env.ai_provider",
            status=CheckStatus.FAIL,
            detail="AI_PROVIDER is not set",
            remediation=f"Set AI_PROVIDER to one of: {', '.join(ALL_PROVIDERS)}",
        )

    if provider not in ALL_PROVIDERS:
        return CheckResult(
            name="env.ai_provider",
            status=CheckStatus.FAIL,
            detail=f"AI_PROVIDER={provider!r} is unknown",
            remediation=f"Supported: {', '.join(ALL_PROVIDERS)}",
        )

    if provider == "anthropic":
        missing = _missing_keys(["ANTHROPIC_API_KEY"])
    elif provider == "gemini":
        missing = _missing_keys(["GEMINI_API_KEY"])
    elif provider == "azure-openai":
        missing = _missing_keys(
            [
                "AZURE_OPENAI_API_KEY",
                "AZURE_OPENAI_ENDPOINT",
                "AZURE_OPENAI_DEPLOYMENT",
            ]
        )
    else:
        preset = OPENAI_COMPAT_PRESETS[provider]
        if "api_key_default" in preset:
            missing = []
        else:
            missing = _missing_keys([preset["api_key_env"]])

    if missing:
        return CheckResult(
            name="env.ai_provider",
            status=CheckStatus.FAIL,
            detail=f"AI_PROVIDER={provider} missing: {', '.join(missing)}",
            remediation="Set the missing environment variables in .env",
        )

    return CheckResult(
        name="env.ai_provider",
        status=CheckStatus.OK,
        detail=f"AI_PROVIDER={provider} has required env vars",
    )


def _check_telegram_env() -> CheckResult:
    if not _env_bool("TELEGRAM_ENABLED"):
        return CheckResult(
            name="env.telegram",
            status=CheckStatus.SKIPPED,
            detail="TELEGRAM_ENABLED=false",
        )
    missing = _missing_keys(["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"])
    if missing:
        return CheckResult(
            name="env.telegram",
            status=CheckStatus.FAIL,
            detail=f"Telegram enabled but missing: {', '.join(missing)}",
            remediation="Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID",
        )
    return CheckResult(
        name="env.telegram",
        status=CheckStatus.OK,
        detail="Telegram credentials present",
    )


def _check_slack_env() -> CheckResult:
    if not _env_bool("SLACK_ENABLED"):
        return CheckResult(
            name="env.slack",
            status=CheckStatus.SKIPPED,
            detail="SLACK_ENABLED=false",
        )
    missing = _missing_keys(["SLACK_WEBHOOK_URL"])
    if missing:
        return CheckResult(
            name="env.slack",
            status=CheckStatus.FAIL,
            detail="Slack enabled but SLACK_WEBHOOK_URL missing",
            remediation="Set SLACK_WEBHOOK_URL",
        )
    return CheckResult(
        name="env.slack",
        status=CheckStatus.OK,
        detail="Slack webhook URL present",
    )


def _check_email_env() -> CheckResult:
    if not _env_bool("EMAIL_ENABLED"):
        return CheckResult(
            name="env.email",
            status=CheckStatus.SKIPPED,
            detail="EMAIL_ENABLED=false",
        )
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]
    missing = _missing_keys(required)
    if missing:
        return CheckResult(
            name="env.email",
            status=CheckStatus.FAIL,
            detail=f"Email enabled but missing: {', '.join(missing)}",
            remediation="Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_TO",
        )
    port_raw = os.getenv("SMTP_PORT", "")
    try:
        int(port_raw)
    except ValueError:
        return CheckResult(
            name="env.email",
            status=CheckStatus.FAIL,
            detail=f"SMTP_PORT={port_raw!r} is not an integer",
            remediation="Set SMTP_PORT to a numeric port (e.g. 587)",
        )
    return CheckResult(
        name="env.email",
        status=CheckStatus.OK,
        detail="SMTP credentials present",
    )


def _check_source_env() -> CheckResult:
    mode = os.getenv("OKSSKOLTEN_MODE", "sqlite").lower()
    if mode not in ("sqlite", "api"):
        return CheckResult(
            name="env.source",
            status=CheckStatus.FAIL,
            detail=f"OKSSKOLTEN_MODE={mode!r} is invalid",
            remediation="Set OKSSKOLTEN_MODE to 'sqlite' or 'api'",
        )
    if mode == "api" and not _present("OKSSKOLTEN_API_URL"):
        return CheckResult(
            name="env.source",
            status=CheckStatus.FAIL,
            detail="OKSSKOLTEN_MODE=api but OKSSKOLTEN_API_URL missing",
            remediation="Set OKSSKOLTEN_API_URL or switch to sqlite mode",
        )
    return CheckResult(
        name="env.source",
        status=CheckStatus.OK,
        detail=f"Oksskolten source mode={mode}",
    )


async def check_env_completeness() -> list[CheckResult]:
    """Run all env validation rules and return their results."""
    return [
        _check_ai_provider_env(),
        _check_telegram_env(),
        _check_slack_env(),
        _check_email_env(),
        _check_source_env(),
    ]
