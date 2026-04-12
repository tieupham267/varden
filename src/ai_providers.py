"""AI Provider implementations — supports 13+ LLM providers.

Native implementations (unique API format):
- anthropic: Anthropic Claude
- gemini: Google Gemini
- azure-openai: Azure OpenAI Service

OpenAI-compatible presets (same API, different base_url):
- openai: OpenAI (GPT-4o, GPT-4o-mini...)
- deepseek: DeepSeek
- mistral: Mistral AI
- groq: Groq (fast inference)
- together: Together AI
- fireworks: Fireworks AI
- xai: xAI (Grok)
- openrouter: OpenRouter (multi-model gateway)
- ollama: Ollama (local, self-hosted)
- openai-compatible: Any OpenAI-compatible API (catch-all)
"""

import logging
import os

import anthropic
import httpx

logger = logging.getLogger(__name__)


# ── OpenAI-compatible presets ────────────────────────────────────────
# Each preset maps a provider name to its default base_url, api_key env,
# and model. Users can override any value via env vars.

OPENAI_COMPAT_PRESETS: dict[str, dict] = {
    "openai": {
        "base_url_env": "OPENAI_BASE_URL",
        "base_url_default": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "model_default": "gpt-4o-mini",
    },
    "deepseek": {
        "base_url_env": "DEEPSEEK_BASE_URL",
        "base_url_default": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "model_default": "deepseek-chat",
    },
    "mistral": {
        "base_url_env": "MISTRAL_BASE_URL",
        "base_url_default": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
        "model_env": "MISTRAL_MODEL",
        "model_default": "mistral-large-latest",
    },
    "groq": {
        "base_url_env": "GROQ_BASE_URL",
        "base_url_default": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "model_env": "GROQ_MODEL",
        "model_default": "llama-3.3-70b-versatile",
    },
    "together": {
        "base_url_env": "TOGETHER_BASE_URL",
        "base_url_default": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
        "model_env": "TOGETHER_MODEL",
        "model_default": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    },
    "fireworks": {
        "base_url_env": "FIREWORKS_BASE_URL",
        "base_url_default": "https://api.fireworks.ai/inference/v1",
        "api_key_env": "FIREWORKS_API_KEY",
        "model_env": "FIREWORKS_MODEL",
        "model_default": "accounts/fireworks/models/llama-v3p3-70b-instruct",
    },
    "xai": {
        "base_url_env": "XAI_BASE_URL",
        "base_url_default": "https://api.x.ai/v1",
        "api_key_env": "XAI_API_KEY",
        "model_env": "XAI_MODEL",
        "model_default": "grok-3-mini",
    },
    "openrouter": {
        "base_url_env": "OPENROUTER_BASE_URL",
        "base_url_default": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "model_env": "OPENROUTER_MODEL",
        "model_default": "anthropic/claude-sonnet-4",
    },
    "ollama": {
        "base_url_env": "OLLAMA_BASE_URL",
        "base_url_default": "http://localhost:11434/v1",
        "api_key_env": "OLLAMA_API_KEY",
        "api_key_default": "ollama",
        "model_env": "OLLAMA_MODEL",
        "model_default": "llama3.3",
        "json_mode": False,
    },
    "openai-compatible": {
        "base_url_env": "OPENAI_BASE_URL",
        "base_url_default": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "model_default": "gpt-4o-mini",
    },
}

ALL_PROVIDERS = (
    ["anthropic", "gemini", "azure-openai"]
    + list(OPENAI_COMPAT_PRESETS.keys())
)


def _resolve_openai_config(provider: str) -> tuple[str, str, str]:
    """Return (base_url, api_key, model) for an OpenAI-compatible provider."""
    preset = OPENAI_COMPAT_PRESETS.get(
        provider, OPENAI_COMPAT_PRESETS["openai-compatible"]
    )
    base_url = os.getenv(preset["base_url_env"], preset["base_url_default"])
    api_key = os.getenv(preset["api_key_env"], preset.get("api_key_default", ""))
    model = os.getenv(preset["model_env"], preset["model_default"])
    return base_url, api_key, model


# ── Provider call functions ──────────────────────────────────────────


async def call_anthropic(system_prompt: str, user_prompt: str) -> str:
    """Call Anthropic Claude API (native SDK)."""
    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    message = await client.messages.create(
        model=model,
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return "".join(
        block.text for block in message.content if hasattr(block, "text")
    )


async def call_openai_compatible(
    system_prompt: str, user_prompt: str, provider: str = "openai-compatible"
) -> str:
    """Call any OpenAI-compatible API (covers 10 providers)."""
    base_url, api_key, model = _resolve_openai_config(provider)

    preset = OPENAI_COMPAT_PRESETS.get(provider, OPENAI_COMPAT_PRESETS["openai-compatible"])
    body = {
        "model": model,
        "max_tokens": 1500,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if preset.get("json_mode", True):
        body["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def call_gemini(system_prompt: str, user_prompt: str) -> str:
    """Call Google Gemini API (REST, no SDK dependency)."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    base_url = os.getenv(
        "GEMINI_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
    )

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/models/{model}:generateContent",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            json={
                "systemInstruction": {
                    "parts": [{"text": system_prompt}],
                },
                "contents": [
                    {"role": "user", "parts": [{"text": user_prompt}]},
                ],
                "generationConfig": {
                    "maxOutputTokens": 1500,
                    "responseMimeType": "application/json",
                },
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


async def call_azure_openai(system_prompt: str, user_prompt: str) -> str:
    """Call Azure OpenAI Service API."""
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

    url = (
        f"{endpoint}/openai/deployments/{deployment}/chat/completions"
    )

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url,
            params={"api-version": api_version},
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "max_tokens": 1500,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def dispatch(
    provider: str, system_prompt: str, user_prompt: str
) -> str:
    """Route to the correct provider call function.

    Raises ValueError for unknown providers.
    """
    if provider == "anthropic":
        return await call_anthropic(system_prompt, user_prompt)
    if provider == "gemini":
        return await call_gemini(system_prompt, user_prompt)
    if provider == "azure-openai":
        return await call_azure_openai(system_prompt, user_prompt)
    if provider in OPENAI_COMPAT_PRESETS:
        return await call_openai_compatible(system_prompt, user_prompt, provider)
    raise ValueError(
        f"Unknown AI_PROVIDER: '{provider}'. "
        f"Supported: {', '.join(ALL_PROVIDERS)}"
    )
