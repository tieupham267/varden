"""Tests for the daemon startup healthcheck gate (Phase 3).

Tests the pure decision logic in _run_startup_healthcheck, verified by patching
run_healthcheck to return synthetic reports and checking whether sys.exit fires.
"""

from __future__ import annotations

import pytest

from src.health_checks.types import CheckResult, CheckStatus, HealthReport


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_gate_skipped_when_disabled(monkeypatch):
    import main

    monkeypatch.setenv("HEALTHCHECK_ON_STARTUP", "false")

    called = {"v": False}

    async def fake_run(**kwargs):
        called["v"] = True
        return HealthReport(results=())

    monkeypatch.setattr("src.healthcheck.run_healthcheck", fake_run)
    await main._run_startup_healthcheck()
    assert called["v"] is False


async def test_gate_passes_when_all_ok(monkeypatch):
    import main

    monkeypatch.setenv("HEALTHCHECK_ON_STARTUP", "true")
    monkeypatch.setenv("HEALTHCHECK_FAIL_FAST", "true")

    async def fake_run(**kwargs):
        return HealthReport(
            results=(CheckResult(name="x", status=CheckStatus.OK),)
        )

    monkeypatch.setattr("src.healthcheck.run_healthcheck", fake_run)
    # Should not raise SystemExit.
    await main._run_startup_healthcheck()


async def test_gate_tolerates_fail_when_fail_fast_off(monkeypatch):
    import main

    monkeypatch.setenv("HEALTHCHECK_ON_STARTUP", "true")
    monkeypatch.setenv("HEALTHCHECK_FAIL_FAST", "false")

    async def fake_run(**kwargs):
        return HealthReport(
            results=(CheckResult(name="x", status=CheckStatus.FAIL),)
        )

    monkeypatch.setattr("src.healthcheck.run_healthcheck", fake_run)
    # Should NOT exit because FAIL_FAST=false.
    await main._run_startup_healthcheck()


async def test_gate_exits_on_fail_when_fail_fast_on(monkeypatch):
    import main

    monkeypatch.setenv("HEALTHCHECK_ON_STARTUP", "true")
    monkeypatch.setenv("HEALTHCHECK_FAIL_FAST", "true")

    async def fake_run(**kwargs):
        return HealthReport(
            results=(
                CheckResult(name="x", status=CheckStatus.OK),
                CheckResult(name="y", status=CheckStatus.FAIL, detail="down"),
            )
        )

    monkeypatch.setattr("src.healthcheck.run_healthcheck", fake_run)

    with pytest.raises(SystemExit) as exc:
        await main._run_startup_healthcheck()
    assert exc.value.code == 1


async def test_gate_tolerates_warn_with_fail_fast_on(monkeypatch):
    import main

    monkeypatch.setenv("HEALTHCHECK_ON_STARTUP", "true")
    monkeypatch.setenv("HEALTHCHECK_FAIL_FAST", "true")

    async def fake_run(**kwargs):
        return HealthReport(
            results=(CheckResult(name="x", status=CheckStatus.WARN),)
        )

    monkeypatch.setattr("src.healthcheck.run_healthcheck", fake_run)
    # WARN should not trigger exit even with FAIL_FAST=true.
    await main._run_startup_healthcheck()
