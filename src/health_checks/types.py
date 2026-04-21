"""Immutable data types for healthcheck reports.

All check functions return :class:`CheckResult`. Orchestrator aggregates them
into :class:`HealthReport` which carries overall status and a shell exit code.

:func:`scrub_secrets` is the single place responsible for redacting API keys
and bearer tokens out of error messages before they reach logs or CLI output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class CheckStatus(str, Enum):
    """Outcome of a single healthcheck."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-or-v1-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),
    re.compile(r"xai-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"gsk_[A-Za-z0-9_\-]{20,}"),
    re.compile(
        r"(api[_-]?key|authorization|x-goog-api-key)=([^\s&\"']+)",
        re.IGNORECASE,
    ),
)

_REDACTED = "***redacted***"


def scrub_secrets(text: str) -> str:
    """Redact common secret shapes from a free-form text string.

    Covers Bearer tokens, Anthropic/OpenAI/OpenRouter sk-* keys, Google AIza*
    keys, xAI, Groq, and generic api_key=... query params. Always safe to call
    on user-facing strings before logging.
    """
    if not text:
        return text
    scrubbed = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            scrubbed = pattern.sub(
                lambda m: f"{m.group(1)}={_REDACTED}", scrubbed
            )
        else:
            scrubbed = pattern.sub(_REDACTED, scrubbed)
    return scrubbed


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single check.

    Attributes:
        name: Stable identifier, e.g. ``"ai_provider.ollama"``.
        status: OK / WARN / FAIL / SKIPPED.
        latency_ms: Wall time for this check. ``None`` for env-only checks.
        detail: Short human-readable message. Always passed through
            :func:`scrub_secrets` by the orchestrator.
        remediation: Optional hint telling the user how to fix the issue.
    """

    name: str
    status: CheckStatus
    latency_ms: int | None = None
    detail: str = ""
    remediation: str | None = None


@dataclass(frozen=True)
class HealthReport:
    """Aggregated healthcheck report.

    Exit code mapping:
        - 0 when all checks are OK or SKIPPED.
        - 2 when at least one WARN and no FAIL.
        - 1 when at least one FAIL.
    """

    results: tuple[CheckResult, ...]
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    finished_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def overall_status(self) -> CheckStatus:
        statuses = {r.status for r in self.results}
        if CheckStatus.FAIL in statuses:
            return CheckStatus.FAIL
        if CheckStatus.WARN in statuses:
            return CheckStatus.WARN
        if statuses == {CheckStatus.SKIPPED}:
            return CheckStatus.SKIPPED
        return CheckStatus.OK

    @property
    def exit_code(self) -> int:
        overall = self.overall_status
        if overall == CheckStatus.FAIL:
            return 1
        if overall == CheckStatus.WARN:
            return 2
        return 0

    @property
    def duration_ms(self) -> int:
        delta = self.finished_at - self.started_at
        return int(delta.total_seconds() * 1000)

    def to_dict(self) -> dict:
        return {
            "overall_status": self.overall_status.value,
            "exit_code": self.exit_code,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "results": [
                {
                    "name": r.name,
                    "status": r.status.value,
                    "latency_ms": r.latency_ms,
                    "detail": r.detail,
                    "remediation": r.remediation,
                }
                for r in self.results
            ],
        }
