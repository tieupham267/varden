"""Tests for healthcheck primitive types + scrub_secrets."""

from __future__ import annotations

import pytest

from src.health_checks.types import (
    CheckResult,
    CheckStatus,
    HealthReport,
    scrub_secrets,
)


pytestmark = pytest.mark.unit


class TestScrubSecrets:
    def test_scrubs_bearer_token(self):
        out = scrub_secrets("Authorization: Bearer abc123xyz789token")
        assert "abc123xyz789token" not in out
        assert "***redacted***" in out

    def test_scrubs_anthropic_key(self):
        raw = "fail auth with sk-ant-api03-xxxxxxxxxxxxxxxxxxxx"
        out = scrub_secrets(raw)
        assert "sk-ant-api03-xxxxxxxxxxxxxxxxxxxx" not in out

    def test_scrubs_openai_key(self):
        raw = "using sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa"
        out = scrub_secrets(raw)
        assert "sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa" not in out

    def test_scrubs_google_api_key(self):
        raw = "error with key AIzaSy-abcdefghijklmnopqrstuvwxyz"
        out = scrub_secrets(raw)
        assert "AIzaSy-abcdefghijklmnopqrstuvwxyz" not in out

    def test_scrubs_xai_key(self):
        raw = "xai-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        out = scrub_secrets(raw)
        assert raw not in out

    def test_scrubs_groq_key(self):
        raw = "gsk_abcdefghijklmnopqrstuvwxyz1234"
        out = scrub_secrets(raw)
        assert raw not in out

    def test_scrubs_query_param_key(self):
        raw = "GET /models?api_key=super-secret-value HTTP/1.1"
        out = scrub_secrets(raw)
        assert "super-secret-value" not in out
        assert "api_key=" in out

    def test_empty_string_passthrough(self):
        assert scrub_secrets("") == ""

    def test_benign_text_unchanged(self):
        assert scrub_secrets("just a benign message") == "just a benign message"


class TestHealthReport:
    def test_overall_ok_when_all_ok(self):
        report = HealthReport(
            results=(
                CheckResult(name="a", status=CheckStatus.OK),
                CheckResult(name="b", status=CheckStatus.OK),
            )
        )
        assert report.overall_status == CheckStatus.OK
        assert report.exit_code == 0

    def test_overall_warn_when_any_warn_no_fail(self):
        report = HealthReport(
            results=(
                CheckResult(name="a", status=CheckStatus.OK),
                CheckResult(name="b", status=CheckStatus.WARN),
            )
        )
        assert report.overall_status == CheckStatus.WARN
        assert report.exit_code == 2

    def test_overall_fail_when_any_fail(self):
        report = HealthReport(
            results=(
                CheckResult(name="a", status=CheckStatus.OK),
                CheckResult(name="b", status=CheckStatus.WARN),
                CheckResult(name="c", status=CheckStatus.FAIL),
            )
        )
        assert report.overall_status == CheckStatus.FAIL
        assert report.exit_code == 1

    def test_skipped_only_reports_skipped(self):
        report = HealthReport(
            results=(
                CheckResult(name="a", status=CheckStatus.SKIPPED),
                CheckResult(name="b", status=CheckStatus.SKIPPED),
            )
        )
        assert report.overall_status == CheckStatus.SKIPPED
        assert report.exit_code == 0

    def test_to_dict_has_stable_keys(self):
        report = HealthReport(
            results=(
                CheckResult(
                    name="x",
                    status=CheckStatus.OK,
                    latency_ms=42,
                    detail="fine",
                ),
            )
        )
        d = report.to_dict()
        assert d["overall_status"] == "ok"
        assert d["exit_code"] == 0
        assert d["results"][0]["latency_ms"] == 42
        assert "started_at" in d and "finished_at" in d
