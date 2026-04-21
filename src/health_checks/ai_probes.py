"""Live probes against AI provider APIs (read-only /models endpoints).

We never call a generate/chat endpoint — that would cost tokens. Instead we
hit each provider's catalog endpoint and interpret HTTP status:

    2xx → OK
    401 / 403 → FAIL (auth)
    408 / 429 / timeout → WARN (transient)
    5xx → WARN (provider-side)
    other → FAIL

All error details pass through :func:`scrub_secrets` via the orchestrator,
but we also avoid copying response bodies into ``detail`` to reduce leak risk.
"""

from __future__ import annotations

import logging
import os
from time import perf_counter

import httpx

from src.ai_providers import ALL_PROVIDERS, OPENAI_COMPAT_PRESETS
from src.health_checks.types import CheckResult, CheckStatus

logger = logging.getLogger(__name__)


def _timeout_seconds() -> float:
    try:
        return float(os.getenv("HEALTHCHECK_PROBE_TIMEOUT_SECONDS", "10"))
    except ValueError:
        return 10.0


def _classify_status(code: int) -> tuple[CheckStatus, str]:
    if 200 <= code < 300:
        return CheckStatus.OK, f"HTTP {code}"
    if code in (401, 403):
        return CheckStatus.FAIL, f"HTTP {code} (auth rejected)"
    if code in (408, 429):
        return CheckStatus.WARN, f"HTTP {code} (throttled or timeout)"
    if 500 <= code < 600:
        return CheckStatus.WARN, f"HTTP {code} (provider-side)"
    return CheckStatus.FAIL, f"HTTP {code}"


def _elapsed_ms(start: float) -> int:
    return int((perf_counter() - start) * 1000)


async def _probe_anthropic(timeout: float) -> CheckResult:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return CheckResult(
            name="ai_provider.anthropic",
            status=CheckStatus.FAIL,
            detail="ANTHROPIC_API_KEY missing",
        )
    start = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
        status, detail = _classify_status(resp.status_code)
        return CheckResult(
            name="ai_provider.anthropic",
            status=status,
            latency_ms=_elapsed_ms(start),
            detail=detail,
        )
    except httpx.TimeoutException:
        return CheckResult(
            name="ai_provider.anthropic",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail=f"timeout after {timeout}s",
        )
    except httpx.HTTPError as e:
        return CheckResult(
            name="ai_provider.anthropic",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail=f"{type(e).__name__}: {str(e)[:120]}",
        )


async def _probe_gemini(timeout: float) -> CheckResult:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return CheckResult(
            name="ai_provider.gemini",
            status=CheckStatus.FAIL,
            detail="GEMINI_API_KEY missing",
        )
    base_url = os.getenv(
        "GEMINI_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
    ).rstrip("/")
    start = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{base_url}/models",
                headers={"x-goog-api-key": api_key},
            )
        status, detail = _classify_status(resp.status_code)
        return CheckResult(
            name="ai_provider.gemini",
            status=status,
            latency_ms=_elapsed_ms(start),
            detail=detail,
        )
    except httpx.TimeoutException:
        return CheckResult(
            name="ai_provider.gemini",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail=f"timeout after {timeout}s",
        )
    except httpx.HTTPError as e:
        return CheckResult(
            name="ai_provider.gemini",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail=f"{type(e).__name__}: {str(e)[:120]}",
        )


async def _probe_azure(timeout: float) -> CheckResult:
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    if not api_key or not endpoint:
        return CheckResult(
            name="ai_provider.azure-openai",
            status=CheckStatus.FAIL,
            detail="AZURE_OPENAI_API_KEY or AZURE_OPENAI_ENDPOINT missing",
        )
    start = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{endpoint}/openai/deployments",
                params={"api-version": api_version},
                headers={"api-key": api_key},
            )
        status, detail = _classify_status(resp.status_code)
        return CheckResult(
            name="ai_provider.azure-openai",
            status=status,
            latency_ms=_elapsed_ms(start),
            detail=detail,
        )
    except httpx.TimeoutException:
        return CheckResult(
            name="ai_provider.azure-openai",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail=f"timeout after {timeout}s",
        )
    except httpx.HTTPError as e:
        return CheckResult(
            name="ai_provider.azure-openai",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail=f"{type(e).__name__}: {str(e)[:120]}",
        )


async def _probe_openai_compatible(provider: str, timeout: float) -> CheckResult:
    preset = OPENAI_COMPAT_PRESETS[provider]
    base_url = os.getenv(preset["base_url_env"], preset["base_url_default"]).rstrip("/")
    api_key = os.getenv(preset["api_key_env"], preset.get("api_key_default", ""))

    endpoints: list[str] = []
    if provider == "ollama":
        root = base_url
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        endpoints = [f"{root}/api/tags", f"{base_url}/models"]
    else:
        endpoints = [f"{base_url}/models"]

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    start = perf_counter()
    last_detail = "no endpoint reachable"
    last_status = CheckStatus.FAIL
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for url in endpoints:
                try:
                    resp = await client.get(url, headers=headers)
                except (httpx.TimeoutException, httpx.HTTPError) as e:
                    last_detail = f"{type(e).__name__}: {str(e)[:120]}"
                    continue
                status, detail = _classify_status(resp.status_code)
                if status == CheckStatus.OK:
                    return CheckResult(
                        name=f"ai_provider.{provider}",
                        status=status,
                        latency_ms=_elapsed_ms(start),
                        detail=detail,
                    )
                last_status, last_detail = status, detail
    except httpx.HTTPError as e:
        last_detail = f"{type(e).__name__}: {str(e)[:120]}"
        last_status = CheckStatus.FAIL

    return CheckResult(
        name=f"ai_provider.{provider}",
        status=last_status,
        latency_ms=_elapsed_ms(start),
        detail=last_detail,
    )


async def probe_ai_provider() -> CheckResult:
    """Route to the right probe for the currently configured provider."""
    provider = os.getenv("AI_PROVIDER", "").strip().lower()
    timeout = _timeout_seconds()

    if not provider or provider not in ALL_PROVIDERS:
        return CheckResult(
            name="ai_provider",
            status=CheckStatus.SKIPPED,
            detail=f"AI_PROVIDER={provider!r} invalid; see env.ai_provider check",
        )

    if provider == "anthropic":
        return await _probe_anthropic(timeout)
    if provider == "gemini":
        return await _probe_gemini(timeout)
    if provider == "azure-openai":
        return await _probe_azure(timeout)
    if provider in OPENAI_COMPAT_PRESETS:
        return await _probe_openai_compatible(provider, timeout)

    return CheckResult(
        name="ai_provider",
        status=CheckStatus.SKIPPED,
        detail=f"no probe implemented for {provider}",
    )
