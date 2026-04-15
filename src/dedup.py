"""Semantic dedup shadow logging — Phase 0.

Queries Oksskolten's `article_similarities` table (populated by its own
title-similarity detection using bigram Dice coefficient + MeiliSearch candidates).

Phase 0 is observability-only: logs what WOULD happen if Option A were active,
without changing pipeline behavior. See tasks/dedup-semantic-plan.md for rationale.

Oksskolten similarity schema (from migrations/0004_article_similarities.sql):
    article_id    INTEGER    -- newer article
    similar_to_id INTEGER    -- older article it resembles
    score         REAL       -- Dice coefficient on titles (0.0-1.0)
    created_at    TEXT
"""

import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Query threshold — higher than Oksskolten's own 0.4 to reduce false positives
MIN_DICE_SCORE = float(os.getenv("DEDUP_MIN_SCORE", "0.6"))


@dataclass(frozen=True)
class SimilarityMatch:
    matched_article_id: int
    dice_score: float


def _oksskolten_db_path() -> str:
    return os.getenv("OKSSKOLTEN_DB_PATH", "/oksskolten-data/oksskolten.db")


def _query_similarities_sync(
    article_id: int, min_score: float
) -> list[SimilarityMatch]:
    """Read article_similarities from Oksskolten DB (read-only)."""
    db_path = _oksskolten_db_path()
    if not os.path.exists(db_path):
        logger.debug(f"Oksskolten DB not found at {db_path}, skipping similarity query")
        return []

    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as e:
        logger.warning(f"Failed to open Oksskolten DB for similarity: {e}")
        return []

    try:
        # Check if table exists (graceful degrade for older Oksskolten versions)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='article_similarities'"
        )
        if not cursor.fetchone():
            logger.debug("Oksskolten has no article_similarities table yet")
            return []

        # Oksskolten stores bidirectionally? Check both sides.
        # Based on similarity.ts: only inserts (newer_id, older_id, score),
        # so if our article is the NEWER one, query by article_id;
        # if it was targeted by a newer one later, query by similar_to_id.
        cursor = conn.execute(
            """SELECT similar_to_id AS matched, score
               FROM article_similarities
               WHERE article_id = ? AND score >= ?
               UNION
               SELECT article_id AS matched, score
               FROM article_similarities
               WHERE similar_to_id = ? AND score >= ?
               ORDER BY score DESC""",
            (article_id, min_score, article_id, min_score),
        )
        matches = [
            SimilarityMatch(matched_article_id=row[0], dice_score=row[1])
            for row in cursor.fetchall()
        ]
        return matches

    except sqlite3.Error as e:
        logger.warning(f"Similarity query failed for article {article_id}: {e}")
        return []
    finally:
        conn.close()


async def query_oksskolten_similarities(
    article_id: int, min_score: float = MIN_DICE_SCORE
) -> list[SimilarityMatch]:
    """Fetch similarity candidates from Oksskolten DB (async wrapper)."""
    return await asyncio.to_thread(_query_similarities_sync, article_id, min_score)


def _extract_cves(analysis: dict) -> list[str]:
    """Normalize CVE list from analysis for storage."""
    cves = analysis.get("cve_ids") or analysis.get("cves") or []
    if not isinstance(cves, list):
        return []
    return [str(c).strip().upper() for c in cves if c]


async def record_shadow_decision(article: dict, analysis: dict) -> None:
    """Compute + log the shadow dedup decision for one article.

    Safe wrapper — any exception is logged, never raised (not critical path).
    """
    # Local import to avoid circular (state imports dedup conceptually)
    from src.state import get_analysis, log_shadow_decision

    try:
        article_id = article["id"]
        matches = await query_oksskolten_similarities(article_id)

        if not matches:
            # No similarity data — still log that we checked (match=None)
            await log_shadow_decision(
                article_id=article_id,
                matched_article_id=None,
                dice_score=None,
                would_merge=False,
                matched_already_analyzed=False,
                relevance_score=analysis.get("relevance_score", 0),
                severity=analysis.get("severity", "info"),
                cves=_extract_cves(analysis),
            )
            return

        # Top candidate by score
        best = matches[0]
        prior_analysis = await get_analysis(best.matched_article_id)
        matched_already_analyzed = prior_analysis is not None
        # would_merge: we'd merge if the matched article has prior analysis
        #              (meaning this new article is "second occurrence" of cluster)
        would_merge = matched_already_analyzed

        matched_cves = None
        matched_relevance = None
        matched_severity = None
        if prior_analysis:
            import json
            try:
                prior_data = json.loads(prior_analysis.get("analysis_json", "{}"))
                matched_cves = _extract_cves(prior_data)
            except (ValueError, TypeError):
                matched_cves = []
            matched_relevance = prior_analysis.get("relevance_score")
            matched_severity = prior_analysis.get("severity")

        await log_shadow_decision(
            article_id=article_id,
            matched_article_id=best.matched_article_id,
            dice_score=best.dice_score,
            would_merge=would_merge,
            matched_already_analyzed=matched_already_analyzed,
            relevance_score=analysis.get("relevance_score", 0),
            severity=analysis.get("severity", "info"),
            cves=_extract_cves(analysis),
            matched_relevance_score=matched_relevance,
            matched_severity=matched_severity,
            matched_cves=matched_cves,
        )

        if would_merge:
            logger.info(
                f"  [shadow] Article #{article_id} would merge with "
                f"#{best.matched_article_id} (dice={best.dice_score:.2f})"
            )

    except Exception as e:
        logger.warning(f"Shadow dedup logging failed for article {article.get('id')}: {e}")
