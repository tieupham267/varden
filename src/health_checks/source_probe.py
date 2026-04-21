"""Oksskolten source probe: SQLite readable + schema discoverable, or API reachable."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from time import perf_counter

import httpx

from src.health_checks.types import CheckResult, CheckStatus

logger = logging.getLogger(__name__)


def _timeout_seconds() -> float:
    try:
        return float(os.getenv("HEALTHCHECK_PROBE_TIMEOUT_SECONDS", "10"))
    except ValueError:
        return 10.0


def _elapsed_ms(start: float) -> int:
    return int((perf_counter() - start) * 1000)


def _probe_sqlite_sync(db_path: str) -> tuple[CheckStatus, str, str | None]:
    """Return (status, detail, remediation) after probing the DB file."""
    if not os.path.exists(db_path):
        return (
            CheckStatus.FAIL,
            f"file not found: {db_path}",
            "Mount Oksskolten's data volume at OKSSKOLTEN_DB_PATH",
        )

    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as e:
        return (
            CheckStatus.FAIL,
            f"open failed: {type(e).__name__}: {str(e)[:100]}",
            None,
        )

    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = [row[0] for row in cursor.fetchall()]
        if not tables:
            return (
                CheckStatus.FAIL,
                "database contains no tables",
                "Is OKSSKOLTEN_DB_PATH pointing at the right file?",
            )

        try:
            from src.source import OksskoltenSource

            probe = OksskoltenSource.__new__(OksskoltenSource)
            probe._schema_cache = None  # type: ignore[attr-defined]
            schema = probe._discover_schema(conn)  # type: ignore[attr-defined]
        except Exception as e:
            return (
                CheckStatus.WARN,
                f"schema discovery failed: {type(e).__name__}: {str(e)[:120]}",
                "Oksskolten schema may have changed; update src/source.py mappings",
            )

        return (
            CheckStatus.OK,
            f"tables={len(tables)}, articles table={schema.get('table')!r}",
            None,
        )
    finally:
        conn.close()


async def _probe_sqlite(db_path: str) -> CheckResult:
    start = perf_counter()
    status, detail, remediation = await asyncio.to_thread(
        _probe_sqlite_sync, db_path
    )
    return CheckResult(
        name="source.oksskolten_sqlite",
        status=status,
        latency_ms=_elapsed_ms(start),
        detail=detail,
        remediation=remediation,
    )


async def _probe_api(api_url: str) -> CheckResult:
    timeout = _timeout_seconds()
    start = perf_counter()
    headers = {}
    api_key = os.getenv("OKSSKOLTEN_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for path in ("/health", "/"):
                try:
                    resp = await client.get(
                        f"{api_url.rstrip('/')}{path}", headers=headers
                    )
                except httpx.HTTPError:
                    continue
                if 200 <= resp.status_code < 300:
                    return CheckResult(
                        name="source.oksskolten_api",
                        status=CheckStatus.OK,
                        latency_ms=_elapsed_ms(start),
                        detail=f"{path} HTTP {resp.status_code}",
                    )
        return CheckResult(
            name="source.oksskolten_api",
            status=CheckStatus.WARN,
            latency_ms=_elapsed_ms(start),
            detail="no successful response from /health or /",
        )
    except httpx.TimeoutException:
        return CheckResult(
            name="source.oksskolten_api",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail=f"timeout after {timeout}s",
        )
    except httpx.HTTPError as e:
        return CheckResult(
            name="source.oksskolten_api",
            status=CheckStatus.FAIL,
            latency_ms=_elapsed_ms(start),
            detail=f"{type(e).__name__}: {str(e)[:120]}",
        )


async def probe_oksskolten_source() -> CheckResult:
    mode = os.getenv("OKSSKOLTEN_MODE", "sqlite").lower()
    if mode == "api":
        api_url = os.getenv("OKSSKOLTEN_API_URL", "").strip()
        if not api_url:
            return CheckResult(
                name="source.oksskolten_api",
                status=CheckStatus.FAIL,
                detail="OKSSKOLTEN_API_URL missing",
            )
        return await _probe_api(api_url)

    db_path = os.getenv("OKSSKOLTEN_DB_PATH", "/oksskolten-data/oksskolten.db")
    return await _probe_sqlite(db_path)
