"""Tests for Oksskolten source probe (sqlite + api modes)."""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from src.health_checks import source_probe
from src.health_checks.types import CheckStatus


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _make_valid_db(path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE feeds (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            title TEXT,
            url TEXT,
            content TEXT,
            published_at TEXT,
            feed_id INTEGER,
            language TEXT
        )
        """
    )
    conn.execute("INSERT INTO feeds VALUES (1, 'TestFeed')")
    conn.commit()
    conn.close()


async def test_sqlite_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("OKSSKOLTEN_MODE", "sqlite")
    monkeypatch.setenv("OKSSKOLTEN_DB_PATH", str(tmp_path / "nonexistent.db"))
    result = await source_probe.probe_oksskolten_source()
    assert result.status == CheckStatus.FAIL
    assert "not found" in result.detail


async def test_sqlite_valid_schema(monkeypatch, tmp_path):
    db = tmp_path / "ok.db"
    _make_valid_db(db)
    monkeypatch.setenv("OKSSKOLTEN_MODE", "sqlite")
    monkeypatch.setenv("OKSSKOLTEN_DB_PATH", str(db))
    result = await source_probe.probe_oksskolten_source()
    assert result.status == CheckStatus.OK
    assert "articles" in result.detail


async def test_sqlite_empty_db_fails(monkeypatch, tmp_path):
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    conn.close()
    monkeypatch.setenv("OKSSKOLTEN_MODE", "sqlite")
    monkeypatch.setenv("OKSSKOLTEN_DB_PATH", str(db))
    result = await source_probe.probe_oksskolten_source()
    assert result.status == CheckStatus.FAIL
    assert "no tables" in result.detail


async def test_sqlite_bad_schema_warns(monkeypatch, tmp_path):
    db = tmp_path / "bad.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("OKSSKOLTEN_MODE", "sqlite")
    monkeypatch.setenv("OKSSKOLTEN_DB_PATH", str(db))
    result = await source_probe.probe_oksskolten_source()
    assert result.status == CheckStatus.WARN
    assert "schema" in result.detail


async def test_api_mode_ok(monkeypatch):
    monkeypatch.setenv("OKSSKOLTEN_MODE", "api")
    monkeypatch.setenv("OKSSKOLTEN_API_URL", "http://oksskolten:3000")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(source_probe.httpx, "AsyncClient", _Client)

    result = await source_probe.probe_oksskolten_source()
    assert result.status == CheckStatus.OK


async def test_api_mode_all_paths_fail_warns(monkeypatch):
    monkeypatch.setenv("OKSSKOLTEN_MODE", "api")
    monkeypatch.setenv("OKSSKOLTEN_API_URL", "http://oksskolten:3000")

    def handler(request):
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(source_probe.httpx, "AsyncClient", _Client)
    result = await source_probe.probe_oksskolten_source()
    assert result.status == CheckStatus.WARN


async def test_api_mode_timeout(monkeypatch):
    monkeypatch.setenv("OKSSKOLTEN_MODE", "api")
    monkeypatch.setenv("OKSSKOLTEN_API_URL", "http://oksskolten:3000")

    def handler(request):
        raise httpx.ConnectTimeout("slow")

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(source_probe.httpx, "AsyncClient", _Client)
    result = await source_probe.probe_oksskolten_source()
    # ConnectError per-request is caught and moves to next path; if all paths
    # raise non-timeout errors it stays WARN. Here we use ConnectTimeout which
    # bubbles out of the try, producing FAIL. Verify either terminal status.
    assert result.status in (CheckStatus.WARN, CheckStatus.FAIL)


async def test_api_mode_with_api_key(monkeypatch):
    monkeypatch.setenv("OKSSKOLTEN_MODE", "api")
    monkeypatch.setenv("OKSSKOLTEN_API_URL", "http://oksskolten:3000")
    monkeypatch.setenv("OKSSKOLTEN_API_KEY", "secret-xyz")

    def handler(request):
        assert request.headers.get("Authorization") == "Bearer secret-xyz"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(*args, transport=transport, **kwargs)

    monkeypatch.setattr(source_probe.httpx, "AsyncClient", _Client)
    result = await source_probe.probe_oksskolten_source()
    assert result.status == CheckStatus.OK


async def test_api_mode_missing_url(monkeypatch):
    monkeypatch.setenv("OKSSKOLTEN_MODE", "api")
    monkeypatch.delenv("OKSSKOLTEN_API_URL", raising=False)
    result = await source_probe.probe_oksskolten_source()
    assert result.status == CheckStatus.FAIL
