"""Coverage tests for src/healthcheck.py (orchestrator, scrubber, formatters)."""

from __future__ import annotations

import json
import sys

import pytest

from src import healthcheck
from src.health_checks.types import CheckResult, CheckStatus, HealthReport


pytestmark = pytest.mark.unit


# ── _scrub_result ──────────────────────────────────────────────────────


def test_scrub_result_returns_same_object_when_clean():
    original = CheckResult(
        name="clean", status=CheckStatus.OK, detail="all good", remediation=None
    )
    scrubbed = healthcheck._scrub_result(original)
    assert scrubbed is original


def test_scrub_result_redacts_detail():
    dirty = CheckResult(
        name="dirty",
        status=CheckStatus.FAIL,
        detail="leaked token sk-ant-api03-aaaaaaaaaaaaaaaaaaaa",
    )
    scrubbed = healthcheck._scrub_result(dirty)
    assert scrubbed is not dirty
    assert "sk-ant-api03-aaaaaaaaaaaaaaaaaaaa" not in scrubbed.detail
    assert scrubbed.name == "dirty"
    assert scrubbed.status == CheckStatus.FAIL


def test_scrub_result_redacts_remediation():
    dirty = CheckResult(
        name="x",
        status=CheckStatus.FAIL,
        detail="ok",
        remediation="try `curl -H 'Authorization: Bearer abc123xyz789' https://api`",
    )
    scrubbed = healthcheck._scrub_result(dirty)
    assert "abc123xyz789" not in (scrubbed.remediation or "")


# ── _ansi_color ────────────────────────────────────────────────────────


def test_ansi_color_enabled_returns_codes():
    start, end = healthcheck._ansi_color(CheckStatus.OK, enabled=True)
    assert start.startswith("\033[")
    assert end == "\033[0m"


def test_ansi_color_disabled_returns_empty():
    start, end = healthcheck._ansi_color(CheckStatus.FAIL, enabled=False)
    assert start == "" and end == ""


# ── format_report_text ─────────────────────────────────────────────────


def test_format_report_text_includes_remediation_for_fail():
    report = HealthReport(
        results=(
            CheckResult(
                name="svc",
                status=CheckStatus.FAIL,
                detail="down",
                remediation="restart the service",
            ),
        )
    )
    text = healthcheck.format_report_text(report, color=False)
    assert "restart the service" in text
    assert "→" in text


def test_format_report_text_no_remediation_for_ok():
    report = HealthReport(
        results=(
            CheckResult(
                name="svc",
                status=CheckStatus.OK,
                detail="fine",
                remediation="ignored",
            ),
        )
    )
    text = healthcheck.format_report_text(report, color=False)
    assert "ignored" not in text


def test_format_report_text_uses_isatty_default(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    report = HealthReport(
        results=(CheckResult(name="x", status=CheckStatus.OK, detail="ok"),)
    )
    text = healthcheck.format_report_text(report)
    assert "\033[" not in text


def test_format_report_text_shows_latency():
    report = HealthReport(
        results=(
            CheckResult(
                name="x", status=CheckStatus.OK, latency_ms=42, detail="fast"
            ),
        )
    )
    text = healthcheck.format_report_text(report, color=False)
    assert "42ms" in text


# ── format_report_json ─────────────────────────────────────────────────


def test_format_report_json_is_valid_json():
    report = HealthReport(
        results=(CheckResult(name="x", status=CheckStatus.OK, detail="ok"),)
    )
    out = healthcheck.format_report_json(report)
    parsed = json.loads(out)
    assert parsed["overall_status"] == "ok"
    assert parsed["results"][0]["name"] == "x"


# ── run_healthcheck ────────────────────────────────────────────────────


async def test_run_healthcheck_live_false_skips_probes(monkeypatch):
    """With live=False, _collect_live_probes must not be called."""
    monkeypatch.setenv("AI_PROVIDER", "ollama")

    called = {"v": False}

    async def never_call():
        called["v"] = True
        return []

    monkeypatch.setattr(healthcheck, "_collect_live_probes", never_call)
    report = await healthcheck.run_healthcheck(live=False)
    assert called["v"] is False
    assert all(r.name.startswith("env.") for r in report.results)


async def test_run_healthcheck_live_true_calls_probes(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")

    async def fake_probes():
        return [CheckResult(name="fake.probe", status=CheckStatus.OK, detail="fp")]

    monkeypatch.setattr(healthcheck, "_collect_live_probes", fake_probes)
    report = await healthcheck.run_healthcheck(live=True)
    names = [r.name for r in report.results]
    assert "fake.probe" in names


async def test_run_healthcheck_only_filter_drops_unmatched(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")

    async def fake_probes():
        return [
            CheckResult(name="ai_provider.x", status=CheckStatus.OK),
            CheckResult(name="notifier.telegram", status=CheckStatus.OK),
        ]

    monkeypatch.setattr(healthcheck, "_collect_live_probes", fake_probes)
    report = await healthcheck.run_healthcheck(
        live=True, only=frozenset({"notifier"})
    )
    names = [r.name for r in report.results]
    assert names == ["notifier.telegram"]


async def test_run_healthcheck_scrubs_secrets_in_results(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")

    async def fake_probes():
        return [
            CheckResult(
                name="dirty.probe",
                status=CheckStatus.FAIL,
                detail="key leaked: sk-ant-api03-aaaaaaaaaaaaaaaaaaaa",
            )
        ]

    monkeypatch.setattr(healthcheck, "_collect_live_probes", fake_probes)
    report = await healthcheck.run_healthcheck(live=True)
    dirty = next(r for r in report.results if r.name == "dirty.probe")
    assert "sk-ant-api03" not in dirty.detail


# ── _collect_live_probes exception path ────────────────────────────────


async def test_collect_live_probes_wraps_exception(monkeypatch):
    """If a probe raises unexpectedly, it becomes a FAIL CheckResult."""
    import src.health_checks.ai_probes as ai_mod
    import src.health_checks.notifier_probes as ntf_mod
    import src.health_checks.source_probe as src_mod

    async def boom():
        raise RuntimeError("unexpected explosion")

    async def ok():
        return CheckResult(name="ok.probe", status=CheckStatus.OK)

    monkeypatch.setattr(ai_mod, "probe_ai_provider", boom)
    monkeypatch.setattr(ntf_mod, "probe_telegram", ok)
    monkeypatch.setattr(ntf_mod, "probe_slack", ok)
    monkeypatch.setattr(ntf_mod, "probe_email", ok)
    monkeypatch.setattr(src_mod, "probe_oksskolten_source", ok)

    results = await healthcheck._collect_live_probes()
    failed = [r for r in results if r.status == CheckStatus.FAIL]
    assert any("RuntimeError" in r.detail for r in failed)


# ── feature flag helpers ───────────────────────────────────────────────


def test_on_startup_defaults_true(monkeypatch):
    monkeypatch.delenv("HEALTHCHECK_ON_STARTUP", raising=False)
    assert healthcheck.healthcheck_on_startup_enabled() is True


def test_on_startup_false_honored(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_ON_STARTUP", "false")
    assert healthcheck.healthcheck_on_startup_enabled() is False


def test_fail_fast_defaults_false(monkeypatch):
    monkeypatch.delenv("HEALTHCHECK_FAIL_FAST", raising=False)
    assert healthcheck.healthcheck_fail_fast_enabled() is False


def test_fail_fast_true_honored(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_FAIL_FAST", "true")
    assert healthcheck.healthcheck_fail_fast_enabled() is True
