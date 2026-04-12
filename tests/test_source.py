"""Tests for src/source.py — SQLite schema discovery and article fetching."""

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.source import OksskoltenSource, _validate_identifier


def _make_source_sqlite(db_path: str) -> OksskoltenSource:
    with patch.dict("os.environ", {"OKSSKOLTEN_MODE": "sqlite", "OKSSKOLTEN_DB_PATH": db_path}):
        return OksskoltenSource()


# ── Schema discovery ────────────────────────────────────────────────


class TestSchemaDiscovery:
    def test_standard_schema(self, oksskolten_db):
        source = _make_source_sqlite(oksskolten_db)
        conn = sqlite3.connect(oksskolten_db)
        schema = source._discover_schema(conn)
        conn.close()

        assert schema["table"] == "articles"
        assert schema["id_col"] == "id"
        assert schema["title_col"] == "title"
        assert schema["url_col"] == "url"
        assert schema["content_col"] == "content"
        assert schema["feeds_table"] == "feeds"
        assert schema["feed_id_col"] == "feed_id"

    def test_caches_schema(self, oksskolten_db):
        source = _make_source_sqlite(oksskolten_db)
        conn = sqlite3.connect(oksskolten_db)
        s1 = source._discover_schema(conn)
        s2 = source._discover_schema(conn)
        conn.close()
        assert s1 is s2  # same object, cached

    def test_no_articles_table_raises(self, tmp_path):
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE other (id INTEGER)")
        conn.commit()

        source = _make_source_sqlite(str(db_path))
        with pytest.raises(RuntimeError, match="Could not find articles table"):
            source._discover_schema(conn)
        conn.close()

    def test_alternative_table_names(self, tmp_path):
        """Should find 'entries' if 'articles' doesn't exist."""
        db_path = tmp_path / "alt.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE entries (id INTEGER PRIMARY KEY, title TEXT, url TEXT, content TEXT)"
        )
        conn.commit()

        source = _make_source_sqlite(str(db_path))
        schema = source._discover_schema(conn)
        conn.close()
        assert schema["table"] == "entries"

    def test_alternative_column_names(self, tmp_path):
        """Should map 'link' → url_col, 'body' → content_col."""
        db_path = tmp_path / "alt_cols.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE articles (article_id INTEGER PRIMARY KEY, title TEXT, link TEXT, body TEXT)"
        )
        conn.commit()

        source = _make_source_sqlite(str(db_path))
        schema = source._discover_schema(conn)
        conn.close()
        assert schema["id_col"] == "article_id"
        assert schema["url_col"] == "link"
        assert schema["content_col"] == "body"


# ── SQLite fetching ─────────────────────────────────────────────────


class TestSQLiteFetch:
    async def test_fetch_all(self, oksskolten_db):
        source = _make_source_sqlite(oksskolten_db)
        articles = await source.fetch_new_articles(since_id=None, limit=50)
        # Article 4 has <50 chars content → skipped
        assert len(articles) == 3

    async def test_fetch_since_id(self, oksskolten_db):
        source = _make_source_sqlite(oksskolten_db)
        articles = await source.fetch_new_articles(since_id=1, limit=50)
        assert len(articles) == 2
        assert all(a["id"] > 1 for a in articles)

    async def test_fetch_respects_limit(self, oksskolten_db):
        source = _make_source_sqlite(oksskolten_db)
        articles = await source.fetch_new_articles(since_id=None, limit=1)
        assert len(articles) == 1

    async def test_skips_short_content(self, oksskolten_db):
        source = _make_source_sqlite(oksskolten_db)
        articles = await source.fetch_new_articles(since_id=3, limit=50)
        assert len(articles) == 0

    async def test_nonexistent_db_returns_empty(self, tmp_path):
        source = _make_source_sqlite(str(tmp_path / "nope.db"))
        assert await source.fetch_new_articles() == []

    async def test_normalized_shape(self, oksskolten_db):
        source = _make_source_sqlite(oksskolten_db)
        articles = await source.fetch_new_articles(since_id=None, limit=1)
        a = articles[0]
        for key in ("id", "title", "url", "content", "published_at", "feed_name", "language"):
            assert key in a, f"Missing key: {key}"

    async def test_feed_name_joined(self, oksskolten_db):
        source = _make_source_sqlite(oksskolten_db)
        articles = await source.fetch_new_articles(since_id=None, limit=50)
        feed_names = {a["feed_name"] for a in articles}
        assert "BleepingComputer" in feed_names
        assert "The Hacker News" in feed_names

    async def test_content_capped_at_10000(self, tmp_path):
        db_path = tmp_path / "big.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE articles "
            "(id INTEGER PRIMARY KEY, title TEXT, url TEXT, content TEXT, published_at TEXT, language TEXT)"
        )
        conn.execute(
            "INSERT INTO articles VALUES (1, 'Big', 'https://ex.com', ?, '2024-01-01', 'en')",
            ("x" * 20000,),
        )
        conn.commit()
        conn.close()

        source = _make_source_sqlite(str(db_path))
        articles = await source.fetch_new_articles()
        assert len(articles[0]["content"]) == 10000


# ── API mode ────────────────────────────────────────────────────────


class TestAPIMode:
    def _make_source_api(self):
        with patch.dict("os.environ", {"OKSSKOLTEN_MODE": "api", "OKSSKOLTEN_API_URL": "http://test:3000"}):
            return OksskoltenSource()

    async def test_fetch_success(self):
        source = self._make_source_api()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": 1, "title": "Test", "url": "https://ex.com", "content": "x" * 100}
        ]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            cls.return_value.__aexit__ = AsyncMock(return_value=False)
            articles = await source.fetch_new_articles()

        assert len(articles) == 1
        assert articles[0]["title"] == "Test"

    async def test_fetch_all_endpoints_fail(self):
        source = self._make_source_api()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.HTTPError("fail"))

        with patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            cls.return_value.__aexit__ = AsyncMock(return_value=False)
            articles = await source.fetch_new_articles()

        assert articles == []


# ── Init ────────────────────────────────────────────────────────────


# ── Identifier validation ──────────────────────────────────────────


class TestIdentifierValidation:
    def test_safe_names_pass(self):
        for name in ("id", "articles", "feed_id", "published_at", "col_123"):
            assert _validate_identifier(name) == name

    def test_none_returns_none(self):
        assert _validate_identifier(None) is None

    def test_rejects_sql_injection(self):
        for bad in ("id; DROP TABLE", "col--comment", "a.b", "1col", ""):
            with pytest.raises(RuntimeError, match="Unsafe SQL identifier"):
                _validate_identifier(bad)

    def test_rejects_spaces(self):
        with pytest.raises(RuntimeError, match="Unsafe SQL identifier"):
            _validate_identifier("col name")

    def test_rejects_parens(self):
        with pytest.raises(RuntimeError, match="Unsafe SQL identifier"):
            _validate_identifier("count()")

    def test_cached_schema_validated(self, oksskolten_db):
        """Verify _discover_schema validates all identifiers in the result."""
        source = _make_source_sqlite(oksskolten_db)
        conn = sqlite3.connect(oksskolten_db)
        schema = source._discover_schema(conn)
        conn.close()
        # All string values in schema must pass validation
        for val in schema.values():
            if isinstance(val, str):
                assert _validate_identifier(val) == val


# ── Init ────────────────────────────────────────────────────────────


class TestInit:
    def test_unknown_mode_raises(self):
        with patch.dict("os.environ", {"OKSSKOLTEN_MODE": "ftp"}):
            with pytest.raises(ValueError, match="Unknown OKSSKOLTEN_MODE"):
                OksskoltenSource()
