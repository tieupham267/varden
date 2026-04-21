"""Tests for notifier channel probes."""

from __future__ import annotations

import smtplib

import httpx
import pytest

from src.health_checks import notifier_probes
from src.health_checks.types import CheckStatus


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _install_mock_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(notifier_probes.httpx, "AsyncClient", _Client)


# ── Telegram ──────────────────────────────────────────────────────────


async def test_telegram_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    result = await notifier_probes.probe_telegram()
    assert result.status == CheckStatus.SKIPPED


async def test_telegram_ok(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/getMe")
        return httpx.Response(
            200,
            json={"ok": True, "result": {"is_bot": True, "username": "varden_bot"}},
        )

    _install_mock_transport(monkeypatch, handler)
    result = await notifier_probes.probe_telegram()
    assert result.status == CheckStatus.OK
    assert "varden_bot" in result.detail


async def test_telegram_auth_rejected(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bad")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    _install_mock_transport(monkeypatch, handler)
    result = await notifier_probes.probe_telegram()
    assert result.status == CheckStatus.FAIL


async def test_telegram_timeout(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")

    def handler(request):
        raise httpx.ConnectTimeout("slow")

    _install_mock_transport(monkeypatch, handler)
    result = await notifier_probes.probe_telegram()
    assert result.status == CheckStatus.FAIL
    assert "timeout" in result.detail.lower()


async def test_telegram_http_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")

    def handler(request):
        raise httpx.ConnectError("no route")

    _install_mock_transport(monkeypatch, handler)
    result = await notifier_probes.probe_telegram()
    assert result.status == CheckStatus.FAIL
    assert "ConnectError" in result.detail


async def test_telegram_5xx_warns(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")

    def handler(request):
        return httpx.Response(502)

    _install_mock_transport(monkeypatch, handler)
    result = await notifier_probes.probe_telegram()
    assert result.status == CheckStatus.WARN


async def test_telegram_bad_json_still_ok(monkeypatch):
    """getMe returns 200 but body is not JSON — still OK, bot_name=?."""
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")

    def handler(request):
        return httpx.Response(200, text="not-json")

    _install_mock_transport(monkeypatch, handler)
    result = await notifier_probes.probe_telegram()
    assert result.status == CheckStatus.OK
    assert "@?" in result.detail


async def test_telegram_missing_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    result = await notifier_probes.probe_telegram()
    assert result.status == CheckStatus.FAIL


# ── Slack ──────────────────────────────────────────────────────────────


async def test_slack_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("SLACK_ENABLED", "false")
    result = await notifier_probes.probe_slack()
    assert result.status == CheckStatus.SKIPPED


async def test_slack_ping_ok(monkeypatch):
    monkeypatch.setenv("SLACK_ENABLED", "true")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")

    received: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        assert request.method == "POST"
        return httpx.Response(200, text="ok")

    _install_mock_transport(monkeypatch, handler)
    result = await notifier_probes.probe_slack()
    assert result.status == CheckStatus.OK
    assert received, "webhook was not called"


async def test_slack_timeout(monkeypatch):
    monkeypatch.setenv("SLACK_ENABLED", "true")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")

    def handler(request):
        raise httpx.ConnectTimeout("slow")

    _install_mock_transport(monkeypatch, handler)
    result = await notifier_probes.probe_slack()
    assert result.status == CheckStatus.FAIL
    assert "timeout" in result.detail.lower()


async def test_slack_http_error(monkeypatch):
    monkeypatch.setenv("SLACK_ENABLED", "true")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")

    def handler(request):
        raise httpx.ConnectError("dns fail")

    _install_mock_transport(monkeypatch, handler)
    result = await notifier_probes.probe_slack()
    assert result.status == CheckStatus.FAIL
    assert "ConnectError" in result.detail


async def test_slack_5xx_warns(monkeypatch):
    monkeypatch.setenv("SLACK_ENABLED", "true")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")

    def handler(request):
        return httpx.Response(502)

    _install_mock_transport(monkeypatch, handler)
    result = await notifier_probes.probe_slack()
    assert result.status == CheckStatus.WARN


async def test_slack_missing_webhook(monkeypatch):
    monkeypatch.setenv("SLACK_ENABLED", "true")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    result = await notifier_probes.probe_slack()
    assert result.status == CheckStatus.FAIL


async def test_slack_webhook_4xx_fails(monkeypatch):
    monkeypatch.setenv("SLACK_ENABLED", "true")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="no_service")

    _install_mock_transport(monkeypatch, handler)
    result = await notifier_probes.probe_slack()
    assert result.status == CheckStatus.FAIL


# ── Email / SMTP ───────────────────────────────────────────────────────


async def test_email_skipped_when_disabled(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "false")
    result = await notifier_probes.probe_email()
    assert result.status == CheckStatus.SKIPPED


async def test_email_sends_successfully(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "from@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("EMAIL_TO", "to@example.com")

    calls = []

    def fake_sender(host, port, user, password, recipient, timeout):
        calls.append((host, port, user, recipient))

    monkeypatch.setattr(
        notifier_probes, "_send_healthcheck_email", fake_sender
    )
    result = await notifier_probes.probe_email()
    assert result.status == CheckStatus.OK
    assert calls == [("smtp.example.com", 587, "from@example.com", "to@example.com")]


async def test_email_auth_error(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "bad")
    monkeypatch.setenv("EMAIL_TO", "to@example.com")

    def fake_sender(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"auth failed")

    monkeypatch.setattr(notifier_probes, "_send_healthcheck_email", fake_sender)
    result = await notifier_probes.probe_email()
    assert result.status == CheckStatus.FAIL
    assert "auth" in result.detail.lower()


async def test_email_missing_env_fields(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("EMAIL_TO", raising=False)
    result = await notifier_probes.probe_email()
    assert result.status == CheckStatus.FAIL
    assert "incomplete" in result.detail.lower()


async def test_email_network_error(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.setenv("EMAIL_TO", "to@example.com")

    def fake_sender(*args, **kwargs):
        raise OSError("name resolution failed")

    monkeypatch.setattr(notifier_probes, "_send_healthcheck_email", fake_sender)
    result = await notifier_probes.probe_email()
    assert result.status == CheckStatus.FAIL
    assert "OSError" in result.detail


async def test_email_smtp_generic_error(monkeypatch):
    import smtplib as _smtp

    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.setenv("EMAIL_TO", "to@example.com")

    def fake_sender(*args, **kwargs):
        raise _smtp.SMTPServerDisconnected("server dropped")

    monkeypatch.setattr(notifier_probes, "_send_healthcheck_email", fake_sender)
    result = await notifier_probes.probe_email()
    assert result.status == CheckStatus.FAIL
    assert "SMTPServerDisconnected" in result.detail


async def test_hostname_helper_never_raises(monkeypatch):
    """_hostname() fallback path when socket.gethostname fails."""
    import socket as sock
    monkeypatch.setattr(sock, "gethostname", lambda: (_ for _ in ()).throw(OSError("nope")))
    name = notifier_probes._hostname()
    assert name == "unknown-host"


async def test_email_bad_port(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "abc")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.setenv("EMAIL_TO", "to@example.com")

    result = await notifier_probes.probe_email()
    assert result.status == CheckStatus.FAIL
    assert "integer" in result.detail
