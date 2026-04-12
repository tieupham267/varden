"""Oksskolten data source — supports SQLite direct read or REST API.

SQLite mode (recommended):
  - Reads Oksskolten's database directly (read-only)
  - Most reliable: no dependency on API documentation
  - Schema auto-discovery handles version differences
  - Requires mounting Oksskolten's data volume into sidecar container

API mode (fallback):
  - Uses Oksskolten's Fastify REST API
  - Does not require shared volume
  - Endpoints may need adjustment based on Oksskolten version
"""

import asyncio
import logging
import os
import re
import sqlite3
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_identifier(name: str | None) -> str | None:
    """Ensure a SQL identifier contains only safe characters."""
    if name is None:
        return None
    if not _SAFE_IDENTIFIER.match(name):
        raise RuntimeError(f"Unsafe SQL identifier from schema: {name!r}")
    return name


class OksskoltenSource:
    """Abstract source - picks mode from env."""

    def __init__(self):
        self.mode = os.getenv("OKSSKOLTEN_MODE", "sqlite").lower()

        if self.mode == "sqlite":
            self.db_path = os.getenv("OKSSKOLTEN_DB_PATH", "/oksskolten-data/oksskolten.db")
            self._schema_cache = None
        elif self.mode == "api":
            self.api_url = os.getenv("OKSSKOLTEN_API_URL", "http://oksskolten:3000").rstrip("/")
            self.api_key = os.getenv("OKSSKOLTEN_API_KEY", "")
        else:
            raise ValueError(f"Unknown OKSSKOLTEN_MODE: {self.mode}")

        logger.info(f"Oksskolten source initialized in {self.mode} mode")

    async def fetch_new_articles(
        self, since_id: Optional[int] = None, limit: int = 50
    ) -> list[dict]:
        """Fetch articles created after since_id. Returns normalized dicts.

        Normalized article shape:
        {
            "id": int,
            "title": str,
            "url": str,
            "content": str,  # full markdown text
            "published_at": str | None,  # ISO format
            "feed_name": str,
            "language": str | None,
        }
        """
        if self.mode == "sqlite":
            return await asyncio.to_thread(
                self._fetch_from_sqlite, since_id, limit
            )
        else:
            return await self._fetch_from_api(since_id, limit)

    # === SQLite mode ===

    def _discover_schema(self, conn: sqlite3.Connection) -> dict:
        """Auto-discover Oksskolten's schema on first query.

        Oksskolten's schema may change across versions. We probe for the
        articles table and map common column variants to a normalized shape.
        """
        if self._schema_cache:
            return self._schema_cache

        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = [row[0] for row in cursor.fetchall()]
        logger.info(f"Oksskolten tables: {tables}")

        # Find articles table
        article_table = None
        for candidate in ["articles", "article", "entries", "entry", "items"]:
            if candidate in tables:
                article_table = candidate
                break

        if not article_table:
            raise RuntimeError(
                f"Could not find articles table. Available: {tables}. "
                f"Please check Oksskolten's schema and update src/source.py."
            )

        # Get columns
        cursor = conn.execute(f"PRAGMA table_info({article_table})")
        columns = [row[1] for row in cursor.fetchall()]
        logger.info(f"Article columns: {columns}")

        # Map to normalized fields
        def find_col(*candidates):
            for c in candidates:
                if c in columns:
                    return c
            return None

        schema = {
            "table": article_table,
            "id_col": find_col("id", "article_id", "rowid"),
            "title_col": find_col("title"),
            "url_col": find_col("url", "link", "href"),
            "content_col": find_col("content", "body", "markdown", "full_text", "text"),
            "published_col": find_col("published_at", "published", "pub_date", "date"),
            "feed_id_col": find_col("feed_id", "feed"),
            "lang_col": find_col("language", "lang"),
            "created_col": find_col("created_at", "fetched_at", "inserted_at"),
        }

        # Find feeds table for feed name lookup
        for feed_candidate in ["feeds", "feed", "subscriptions"]:
            if feed_candidate in tables:
                schema["feeds_table"] = feed_candidate
                break

        # Validate all discovered identifiers against injection
        for val in schema.values():
            if isinstance(val, str):
                _validate_identifier(val)

        logger.info(f"Resolved schema: {schema}")
        self._schema_cache = schema
        return schema

    def _fetch_from_sqlite(
        self, since_id: Optional[int], limit: int
    ) -> list[dict]:
        """Read articles directly from Oksskolten's SQLite DB (read-only)."""
        if not os.path.exists(self.db_path):
            logger.error(f"Oksskolten DB not found at {self.db_path}")
            return []

        # Open read-only to avoid any accidental writes
        uri = f"file:{self.db_path}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=10)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            logger.error(f"Failed to open Oksskolten DB: {e}")
            return []

        try:
            schema = self._discover_schema(conn)

            # Build query
            cols = [
                f"a.{schema['id_col']} as id",
                f"a.{schema['title_col']} as title",
                f"a.{schema['url_col']} as url",
                f"a.{schema['content_col']} as content",
            ]
            if schema.get("published_col"):
                cols.append(f"a.{schema['published_col']} as published_at")
            if schema.get("lang_col"):
                cols.append(f"a.{schema['lang_col']} as language")

            # Join with feeds table if available
            feed_name_expr = "'Unknown' as feed_name"
            join_clause = ""
            if schema.get("feeds_table") and schema.get("feed_id_col"):
                feed_name_expr = "f.title as feed_name"
                join_clause = (
                    f" LEFT JOIN {schema['feeds_table']} f "
                    f"ON a.{schema['feed_id_col']} = f.id"
                )
            cols.append(feed_name_expr)

            select_clause = ", ".join(cols)
            where_clause = ""
            params: list = []
            if since_id is not None:
                where_clause = f" WHERE a.{schema['id_col']} > ?"
                params.append(since_id)

            order_col = schema.get("id_col", "id")
            query = (
                f"SELECT {select_clause} FROM {schema['table']} a"
                f"{join_clause}{where_clause} "
                f"ORDER BY a.{order_col} ASC LIMIT ?"
            )
            params.append(limit)

            cursor = conn.execute(query, params)
            articles = []
            for row in cursor.fetchall():
                d = dict(row)
                # Skip articles with no content (not yet extracted)
                if not d.get("content") or len(d["content"]) < 50:
                    continue
                articles.append({
                    "id": d["id"],
                    "title": d.get("title", ""),
                    "url": d.get("url", ""),
                    "content": d.get("content", "")[:10000],  # token cap
                    "published_at": d.get("published_at"),
                    "feed_name": d.get("feed_name", "Unknown"),
                    "language": d.get("language"),
                })

            logger.info(f"Fetched {len(articles)} new articles from SQLite")
            return articles

        except sqlite3.Error as e:
            logger.error(f"SQLite query error: {e}")
            return []
        finally:
            conn.close()

    # === API mode ===

    async def _fetch_from_api(
        self, since_id: Optional[int], limit: int
    ) -> list[dict]:
        """Fetch via Oksskolten REST API.

        NOTE: Endpoint paths are based on common Fastify conventions.
        You may need to adjust these based on actual Oksskolten API docs.
        """
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Try common endpoint patterns
        endpoint_candidates = [
            f"/api/articles?limit={limit}",
            f"/api/v1/articles?limit={limit}",
            f"/api/entries?limit={limit}",
        ]
        if since_id is not None:
            endpoint_candidates = [
                f"{ep}&since_id={since_id}" for ep in endpoint_candidates
            ]

        async with httpx.AsyncClient(timeout=30) as client:
            for ep in endpoint_candidates:
                try:
                    resp = await client.get(
                        f"{self.api_url}{ep}", headers=headers
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        items = data if isinstance(data, list) else data.get("articles", data.get("data", []))
                        normalized = []
                        for item in items:
                            if not item.get("content"):
                                continue
                            normalized.append({
                                "id": item.get("id"),
                                "title": item.get("title", ""),
                                "url": item.get("url") or item.get("link", ""),
                                "content": item.get("content", "")[:10000],
                                "published_at": item.get("published_at") or item.get("published"),
                                "feed_name": item.get("feed_name") or item.get("feed", "Unknown"),
                                "language": item.get("language"),
                            })
                        logger.info(f"Fetched {len(normalized)} articles from {ep}")
                        return normalized
                except httpx.HTTPError as e:
                    logger.debug(f"Endpoint {ep} failed: {e}")

        logger.error("No API endpoint worked. Update endpoint paths in src/source.py")
        return []
