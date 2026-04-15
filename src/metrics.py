"""Dedup metrics computation - Phase 1.

Computes the 3 decision metrics from shadow log data:

- M1 Agreement rate: of would_merge pairs, % where Varden's independent
  analyses of the two articles agree (CVE overlap >=50%, |relevance_diff| <=2,
  same severity). High agreement = Oksskolten similarity is trustworthy.

- M2 Alert reduction potential: of alerts sent, % that could have been
  suppressed because the matched article also had an alert (true noise).

- M3 Audit precision: from manual labels in dedup_shadow_log.audit_label.

See tasks/dedup-semantic-plan.md for decision matrix.
"""

import json
import logging
from dataclasses import asdict, dataclass
from typing import Optional

import aiosqlite

from src import state

logger = logging.getLogger(__name__)

# Agreement thresholds (tune based on observed data)
CVE_OVERLAP_MIN = 0.5
RELEVANCE_DIFF_MAX = 2


@dataclass(frozen=True)
class DedupMetrics:
    days: int
    total_logged: int
    with_match: int
    coverage_pct: float
    would_merge_count: int
    agreement_eligible: int  # would_merge AND both sides analyzed
    agreement_count: int
    agreement_rate: Optional[float]
    alerts_total: int
    alerts_mergeable: int
    alert_reduction_pct: Optional[float]
    audit_total: int
    audit_correct: int
    audit_false: int
    audit_precision: Optional[float]

    def to_dict(self) -> dict:
        return asdict(self)


def _cve_overlap(a: list[str], b: list[str]) -> float:
    """Jaccard-like overlap: |A∩B| / min(|A|,|B|) when both non-empty, else 0."""
    if not a or not b:
        return 0.0
    set_a, set_b = set(a), set(b)
    intersection = len(set_a & set_b)
    denom = min(len(set_a), len(set_b))
    return intersection / denom if denom > 0 else 0.0


def _is_agreement(row: dict) -> bool:
    """Return True if article + matched article analyses are 'consistent'."""
    if row["matched_relevance_score"] is None or row["matched_severity"] is None:
        return False

    # Severity must match
    if (row["severity"] or "").lower() != (row["matched_severity"] or "").lower():
        return False

    # Relevance within tolerance
    rel_diff = abs((row["relevance_score"] or 0) - (row["matched_relevance_score"] or 0))
    if rel_diff > RELEVANCE_DIFF_MAX:
        return False

    # CVE overlap - only enforced when either side has CVEs
    try:
        cves = json.loads(row["cves_json"] or "[]")
    except (ValueError, TypeError):
        cves = []
    try:
        matched_cves = json.loads(row["matched_cves_json"] or "[]")
    except (ValueError, TypeError):
        matched_cves = []

    if cves or matched_cves:
        if _cve_overlap(cves, matched_cves) < CVE_OVERLAP_MIN:
            return False

    return True


async def compute_metrics(days: int = 14) -> DedupMetrics:
    """Compute all metrics from shadow log within the last N days."""
    async with aiosqlite.connect(state.STATE_DB) as db:
        db.row_factory = aiosqlite.Row

        # Shadow log rows in window
        cursor = await db.execute(
            """SELECT * FROM dedup_shadow_log
               WHERE created_at >= datetime('now', ? || ' days')""",
            (f"-{days}",),
        )
        rows = [dict(r) for r in await cursor.fetchall()]

        # Alerts joined for M2
        cursor = await db.execute(
            """SELECT s.article_id, s.would_merge, s.matched_article_id,
                      a.alert_sent AS article_alerted,
                      m.alert_sent AS matched_alerted
               FROM dedup_shadow_log s
               LEFT JOIN analyzed_articles a ON a.oksskolten_id = s.article_id
               LEFT JOIN analyzed_articles m ON m.oksskolten_id = s.matched_article_id
               WHERE s.created_at >= datetime('now', ? || ' days')""",
            (f"-{days}",),
        )
        alert_rows = [dict(r) for r in await cursor.fetchall()]

    total = len(rows)
    with_match = sum(1 for r in rows if r["matched_article_id"] is not None)
    coverage = (with_match / total * 100) if total > 0 else 0.0
    would_merge = [r for r in rows if r["would_merge"] == 1]
    would_merge_count = len(would_merge)

    # M1 Agreement
    eligible = [r for r in would_merge if r["matched_already_analyzed"] == 1]
    agree_count = sum(1 for r in eligible if _is_agreement(r))
    agree_rate = (agree_count / len(eligible)) if eligible else None

    # M2 Alert reduction
    alerts_total = sum(1 for r in alert_rows if (r["article_alerted"] or 0) == 1)
    alerts_mergeable = sum(
        1 for r in alert_rows
        if (r["article_alerted"] or 0) == 1
        and r["would_merge"] == 1
        and (r["matched_alerted"] or 0) == 1
    )
    alert_reduction = (alerts_mergeable / alerts_total) if alerts_total > 0 else None

    # M3 Audit
    audit_total = sum(1 for r in rows if r["audit_label"])
    audit_correct = sum(1 for r in rows if r["audit_label"] == "correct")
    audit_false = sum(1 for r in rows if r["audit_label"] == "false_merge")
    audit_precision = (audit_correct / audit_total) if audit_total > 0 else None

    return DedupMetrics(
        days=days,
        total_logged=total,
        with_match=with_match,
        coverage_pct=coverage,
        would_merge_count=would_merge_count,
        agreement_eligible=len(eligible),
        agreement_count=agree_count,
        agreement_rate=agree_rate,
        alerts_total=alerts_total,
        alerts_mergeable=alerts_mergeable,
        alert_reduction_pct=(alert_reduction * 100) if alert_reduction is not None else None,
        audit_total=audit_total,
        audit_correct=audit_correct,
        audit_false=audit_false,
        audit_precision=audit_precision,
    )


def format_report(m: DedupMetrics) -> str:
    """Human-readable report."""
    lines = []
    lines.append(f"=== Dedup Metrics (last {m.days} days) ===")
    lines.append("")
    lines.append(f"Sample size:           {m.total_logged} articles")
    lines.append(f"With similarity data:  {m.with_match} ({m.coverage_pct:.1f}%)")
    lines.append(f"Would-merge decisions: {m.would_merge_count}")
    lines.append("")
    lines.append("--- M1 Agreement (Varden self-consistency) ---")
    if m.agreement_rate is None:
        lines.append("  No eligible pairs yet (need both articles analyzed)")
    else:
        lines.append(
            f"  {m.agreement_count}/{m.agreement_eligible} = "
            f"{m.agreement_rate*100:.1f}%"
        )
        lines.append(f"  Target: >=90% -> Option A trustworthy")
    lines.append("")
    lines.append("--- M2 Alert reduction potential ---")
    if m.alert_reduction_pct is None:
        lines.append("  No alerts sent in window")
    else:
        lines.append(
            f"  {m.alerts_mergeable}/{m.alerts_total} alerts = "
            f"{m.alert_reduction_pct:.1f}%"
        )
        lines.append(f"  Target: >15% -> worth shipping")
    lines.append("")
    lines.append("--- M3 Audit precision (manual labels) ---")
    if m.audit_precision is None:
        lines.append("  No manual labels yet - run `python main.py audit-dedup`")
    else:
        lines.append(
            f"  {m.audit_correct} correct / {m.audit_false} false / "
            f"{m.audit_total} labeled"
        )
        lines.append(f"  Precision: {m.audit_precision*100:.1f}%")
        lines.append(f"  Target: >=95% (Option A) or >=85% (Option C)")
    lines.append("")
    lines.append(_recommend(m))
    return "\n".join(lines)


def _recommend(m: DedupMetrics) -> str:
    """Map metrics to decision matrix recommendation."""
    if m.would_merge_count < 10:
        return "-> Keep collecting data (need >=10 would-merge pairs for signal)"

    agree = m.agreement_rate
    reduction = (m.alert_reduction_pct or 0) / 100
    precision = m.audit_precision

    if precision is not None and precision < 0.80:
        return "-> STOP: precision too low, investigate false merges before proceeding"

    if agree is None:
        return "-> Need more time - no agreement data yet"

    if agree >= 0.90 and (precision is None or precision >= 0.95):
        if reduction > 0.15:
            return "-> Recommendation: SHIP Option A (alert-level dedup)"
        elif reduction < 0.10:
            return "-> Recommendation: NO-OP (noise is not the real problem)"

    if 0.75 <= agree < 0.90:
        if reduction > 0.15:
            return "-> Recommendation: UPGRADE to Option C (hybrid embedding)"

    if agree < 0.75 and reduction > 0.15:
        return "-> Recommendation: Option B (full embedding) - Oksskolten signal too weak"

    return "-> Mixed signals - review individual metrics above"
