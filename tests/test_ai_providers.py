"""Tests for src/ai_providers.py — config resolution, routing, json_mode flag."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ai_providers import (
    ALL_PROVIDERS,
    OPENAI_COMPAT_PRESETS,
    _resolve_openai_config,
    call_anthropic,
    call_azure_openai,
    call_gemini,
    call_openai_compatible,
    dispatch,
)


# ── Config resolution ───────────────────────────────────────────────


class TestResolveConfig:
    def test_openai_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            base_url, _, model = _resolve_openai_config("openai")
        assert base_url == "https://api.openai.com/v1"
        assert model == "gpt-4o-mini"

    def test_deepseek_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            base_url, _, model = _resolve_openai_config("deepseek")
        assert "deepseek" in base_url
        assert model == "deepseek-chat"

    def test_env_override(self):
        env = {
            "GROQ_BASE_URL": "https://custom.groq",
            "GROQ_API_KEY": "sk-test",
            "GROQ_MODEL": "llama-custom",
        }
        with patch.dict("os.environ", env):
            base_url, api_key, model = _resolve_openai_config("groq")
        assert base_url == "https://custom.groq"
        assert api_key == "sk-test"
        assert model == "llama-custom"

    def test_ollama_default_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            _, api_key, _ = _resolve_openai_config("ollama")
        assert api_key == "ollama"

    def test_unknown_provider_uses_compatible(self):
        with patch.dict("os.environ", {}, clear=True):
            base_url, _, _ = _resolve_openai_config("unknown")
        assert base_url == "https://api.openai.com/v1"


# ── Dispatch routing ────────────────────────────────────────────────


class TestDispatch:
    async def test_anthropic(self):
        with patch("src.ai_providers.call_anthropic", new_callable=AsyncMock, return_value="ok") as m:
            r = await dispatch("anthropic", "sys", "usr")
        m.assert_called_once_with("sys", "usr")
        assert r == "ok"

    async def test_gemini(self):
        with patch("src.ai_providers.call_gemini", new_callable=AsyncMock, return_value="ok") as m:
            await dispatch("gemini", "sys", "usr")
        m.assert_called_once()

    async def test_azure_openai(self):
        with patch("src.ai_providers.call_azure_openai", new_callable=AsyncMock, return_value="ok") as m:
            await dispatch("azure-openai", "sys", "usr")
        m.assert_called_once()

    async def test_openai_compat_providers(self):
        for provider in ("openai", "deepseek", "groq", "mistral", "together", "ollama"):
            with patch("src.ai_providers.call_openai_compatible", new_callable=AsyncMock, return_value="{}") as m:
                await dispatch(provider, "sys", "usr")
            m.assert_called_once_with("sys", "usr", provider)

    async def test_unknown_raises_valueerror(self):
        with pytest.raises(ValueError, match="Unknown AI_PROVIDER"):
            await dispatch("nonexistent", "sys", "usr")


# ── json_mode flag ──────────────────────────────────────────────────


class TestJsonMode:
    def test_ollama_json_mode_false(self):
        assert OPENAI_COMPAT_PRESETS["ollama"]["json_mode"] is False

    def test_others_default_true(self):
        for name, preset in OPENAI_COMPAT_PRESETS.items():
            if name == "ollama":
                continue
            assert preset.get("json_mode", True) is True, f"{name} should default to json_mode=True"


# ── ALL_PROVIDERS list ──────────────────────────────────────────────


class TestProviderList:
    def test_contains_native_providers(self):
        for p in ("anthropic", "gemini", "azure-openai"):
            assert p in ALL_PROVIDERS

    def test_contains_compat_providers(self):
        for p in ("openai", "deepseek", "groq", "ollama", "openrouter"):
            assert p in ALL_PROVIDERS

    def test_no_duplicates(self):
        assert len(ALL_PROVIDERS) == len(set(ALL_PROVIDERS))


# ── Actual provider call functions ─────────────────────────────────


class TestCallAnthropic:
    async def test_success(self):
        mock_block = MagicMock()
        mock_block.text = '{"score": 5}'

        mock_message = MagicMock()
        mock_message.content = [mock_block]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch("src.ai_providers.anthropic.AsyncAnthropic", return_value=mock_client), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = await call_anthropic("system", "user")

        assert result == '{"score": 5}'
        mock_client.messages.create.assert_called_once()

    async def test_multi_block_response(self):
        block1 = MagicMock()
        block1.text = "hello "
        block2 = MagicMock()
        block2.text = "world"
        # Block without text attribute
        block3 = MagicMock(spec=[])

        mock_message = MagicMock()
        mock_message.content = [block1, block2, block3]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        with patch("src.ai_providers.anthropic.AsyncAnthropic", return_value=mock_client), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}):
            result = await call_anthropic("s", "u")

        assert result == "hello world"


class TestCallOpenAICompatible:
    async def test_success_with_json_mode(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}}]
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as cls, \
             patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=False):
            cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await call_openai_compatible("sys", "usr", "openai")

        assert result == '{"ok": true}'
        body = mock_client.post.call_args.kwargs["json"]
        assert body["response_format"] == {"type": "json_object"}

    async def test_ollama_no_json_mode(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "text"}}]
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as cls, \
             patch.dict("os.environ", {}, clear=True):
            cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await call_openai_compatible("sys", "usr", "ollama")

        body = mock_client.post.call_args.kwargs["json"]
        assert "response_format" not in body


class TestCallGemini:
    async def test_success(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"a": 1}'}]}}]
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as cls, \
             patch.dict("os.environ", {"GEMINI_API_KEY": "gem-key"}):
            cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await call_gemini("sys", "usr")

        assert result == '{"a": 1}'

    async def test_api_key_in_header_not_params(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}]
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as cls, \
             patch.dict("os.environ", {"GEMINI_API_KEY": "secret"}):
            cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            cls.return_value.__aexit__ = AsyncMock(return_value=False)
            await call_gemini("sys", "usr")

        call_kwargs = mock_client.post.call_args.kwargs
        assert call_kwargs["headers"]["x-goog-api-key"] == "secret"
        assert "params" not in call_kwargs


class TestCallAzureOpenAI:
    async def test_success(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"data": true}'}}]
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        env = {
            "AZURE_OPENAI_API_KEY": "az-key",
            "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com",
            "AZURE_OPENAI_DEPLOYMENT": "gpt4",
        }
        with patch("httpx.AsyncClient") as cls, \
             patch.dict("os.environ", env):
            cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await call_azure_openai("sys", "usr")

        assert result == '{"data": true}'
        url = mock_client.post.call_args.args[0]
        assert "gpt4" in url
        assert "test.openai.azure.com" in url
