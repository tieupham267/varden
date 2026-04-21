"""Tests for conditional env completeness rules."""

from __future__ import annotations

import pytest

from src.health_checks.env_validator import check_env_completeness
from src.health_checks.types import CheckStatus


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _clear_all_env(monkeypatch):
    for key in [
        "AI_PROVIDER",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "DEEPSEEK_API_KEY",
        "TELEGRAM_ENABLED",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "SLACK_ENABLED",
        "SLACK_WEBHOOK_URL",
        "EMAIL_ENABLED",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "EMAIL_TO",
        "OKSSKOLTEN_MODE",
        "OKSSKOLTEN_API_URL",
    ]:
        monkeypatch.delenv(key, raising=False)


def _by_name(results, name):
    for r in results:
        if r.name == name:
            return r
    raise AssertionError(f"no result named {name}: {[r.name for r in results]}")


async def test_missing_ai_provider_fails(monkeypatch):
    _clear_all_env(monkeypatch)
    results = await check_env_completeness()
    r = _by_name(results, "env.ai_provider")
    assert r.status == CheckStatus.FAIL
    assert "not set" in r.detail


async def test_unknown_ai_provider_fails(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "totally-fake-provider")
    results = await check_env_completeness()
    r = _by_name(results, "env.ai_provider")
    assert r.status == CheckStatus.FAIL
    assert "unknown" in r.detail


async def test_anthropic_requires_api_key(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    results = await check_env_completeness()
    assert _by_name(results, "env.ai_provider").status == CheckStatus.FAIL
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    results = await check_env_completeness()
    assert _by_name(results, "env.ai_provider").status == CheckStatus.OK


async def test_azure_requires_three_keys(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "azure-openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "x")
    results = await check_env_completeness()
    r = _by_name(results, "env.ai_provider")
    assert r.status == CheckStatus.FAIL
    assert "AZURE_OPENAI_ENDPOINT" in r.detail

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    results = await check_env_completeness()
    assert _by_name(results, "env.ai_provider").status == CheckStatus.OK


async def test_ollama_needs_no_api_key(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    results = await check_env_completeness()
    assert _by_name(results, "env.ai_provider").status == CheckStatus.OK


async def test_deepseek_requires_key(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "deepseek")
    results = await check_env_completeness()
    assert _by_name(results, "env.ai_provider").status == CheckStatus.FAIL
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-test")
    results = await check_env_completeness()
    assert _by_name(results, "env.ai_provider").status == CheckStatus.OK


async def test_telegram_skipped_when_disabled(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    results = await check_env_completeness()
    r = _by_name(results, "env.telegram")
    assert r.status == CheckStatus.SKIPPED


async def test_telegram_requires_token_and_chat(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    results = await check_env_completeness()
    r = _by_name(results, "env.telegram")
    assert r.status == CheckStatus.FAIL
    assert "TELEGRAM_BOT_TOKEN" in r.detail

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    results = await check_env_completeness()
    assert _by_name(results, "env.telegram").status == CheckStatus.OK


async def test_slack_requires_webhook_when_enabled(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("SLACK_ENABLED", "true")
    results = await check_env_completeness()
    assert _by_name(results, "env.slack").status == CheckStatus.FAIL


async def test_email_non_numeric_port_fails(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "abc")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    monkeypatch.setenv("EMAIL_TO", "to@example.com")
    results = await check_env_completeness()
    r = _by_name(results, "env.email")
    assert r.status == CheckStatus.FAIL
    assert "integer" in r.detail


async def test_source_api_mode_requires_url(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OKSSKOLTEN_MODE", "api")
    results = await check_env_completeness()
    assert _by_name(results, "env.source").status == CheckStatus.FAIL

    monkeypatch.setenv("OKSSKOLTEN_API_URL", "http://localhost:3000")
    results = await check_env_completeness()
    assert _by_name(results, "env.source").status == CheckStatus.OK


async def test_gemini_requires_key(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    results = await check_env_completeness()
    assert _by_name(results, "env.ai_provider").status == CheckStatus.FAIL
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-fake")
    results = await check_env_completeness()
    assert _by_name(results, "env.ai_provider").status == CheckStatus.OK


async def test_slack_ok_when_webhook_set(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("SLACK_ENABLED", "true")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")
    results = await check_env_completeness()
    assert _by_name(results, "env.slack").status == CheckStatus.OK


async def test_email_missing_fields_fail(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    # Missing the other required fields.
    results = await check_env_completeness()
    r = _by_name(results, "env.email")
    assert r.status == CheckStatus.FAIL
    assert "SMTP_USER" in r.detail


async def test_email_ok_when_all_set(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "from@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("EMAIL_TO", "to@example.com")
    results = await check_env_completeness()
    assert _by_name(results, "env.email").status == CheckStatus.OK


async def test_invalid_source_mode_fails(monkeypatch):
    _clear_all_env(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OKSSKOLTEN_MODE", "junk")
    results = await check_env_completeness()
    assert _by_name(results, "env.source").status == CheckStatus.FAIL
