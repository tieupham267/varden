"""Tests for AI provider live probes."""

from __future__ import annotations

import httpx
import pytest

from src.health_checks import ai_probes
from src.health_checks.types import CheckStatus


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _install_mock_transport(monkeypatch, handler):
    """Replace httpx.AsyncClient in ai_probes with one using MockTransport."""
    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(ai_probes.httpx, "AsyncClient", _Client)


async def test_anthropic_ok(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.anthropic.com"
        assert "x-api-key" in request.headers
        return httpx.Response(200, json={"data": []})

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.OK
    assert result.name == "ai_provider.anthropic"
    assert result.latency_ms is not None and result.latency_ms >= 0


async def test_anthropic_auth_rejected(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-bad")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.FAIL
    assert "401" in result.detail


async def test_anthropic_missing_key(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.FAIL
    assert "missing" in result.detail.lower()


async def test_openai_compat_deepseek_ok(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-fake")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        assert "Bearer" in request.headers.get("Authorization", "")
        return httpx.Response(200, json={"data": []})

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.OK
    assert result.name == "ai_provider.deepseek"


async def test_ollama_uses_api_tags(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404)

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.OK
    assert "/api/tags" in seen_paths


async def test_ollama_falls_back_to_models(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(404)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(500)

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.OK
    assert any("/models" in p for p in seen_paths)


async def test_gemini_5xx_warns(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-fake")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.WARN


async def test_unknown_provider_skipped(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "totally-fake")
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.SKIPPED


async def test_anthropic_timeout(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    def handler(request):
        raise httpx.ConnectTimeout("too slow")

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.FAIL
    assert "timeout" in result.detail.lower()


async def test_gemini_timeout(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-fake")

    def handler(request):
        raise httpx.ReadTimeout("gemini slow")

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.FAIL
    assert "timeout" in result.detail.lower()


async def test_gemini_missing_key(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.FAIL
    assert "missing" in result.detail.lower()


async def test_gemini_http_error(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-fake")

    def handler(request):
        raise httpx.ConnectError("no route")

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.FAIL
    assert "ConnectError" in result.detail


async def test_azure_missing_keys(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "azure-openai")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.FAIL
    assert "missing" in result.detail.lower()


async def test_azure_ok(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "azure-openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azkey")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://foo.openai.azure.com")

    def handler(request):
        assert request.headers.get("api-key") == "azkey"
        return httpx.Response(200, json={"data": []})

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.OK
    assert result.name == "ai_provider.azure-openai"


async def test_azure_timeout(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "azure-openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://foo.openai.azure.com")

    def handler(request):
        raise httpx.ConnectTimeout("slow")

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.FAIL
    assert "timeout" in result.detail.lower()


async def test_azure_http_error(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "azure-openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://foo.openai.azure.com")

    def handler(request):
        raise httpx.ConnectError("no route")

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.FAIL
    assert "ConnectError" in result.detail


async def test_openai_compat_429_warns(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")

    def handler(request):
        return httpx.Response(429)

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.WARN


async def test_openai_compat_all_endpoints_fail(monkeypatch):
    """Ollama probe where both /api/tags and /models raise errors."""
    monkeypatch.setenv("AI_PROVIDER", "ollama")

    def handler(request):
        raise httpx.ConnectError("unreachable")

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.FAIL


async def test_openai_compat_unreachable_server(monkeypatch):
    """One non-OK status from /models still surfaces as last_status."""
    monkeypatch.setenv("AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")

    def handler(request):
        return httpx.Response(418)  # I'm a teapot — not classified as OK

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.FAIL
    assert "418" in result.detail


async def test_error_detail_length_capped(monkeypatch):
    """Regression: httpx error messages can embed URLs. Detail must be bounded."""
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect failed: " + "x" * 500)

    _install_mock_transport(monkeypatch, handler)
    result = await ai_probes.probe_ai_provider()
    assert result.status == CheckStatus.FAIL
    assert len(result.detail) < 200
