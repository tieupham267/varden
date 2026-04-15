"""Tests for Phase 1 dedup metrics computation."""

import pytest

from src.metrics import (
    CVE_OVERLAP_MIN,
    _cve_overlap,
    _is_agreement,
    _recommend,
    compute_metrics,
    format_report,
)
from src.state import log_shadow_decision, mark_alert_sent, save_analysis


# ─── _cve_overlap ─────────────────────────────────────────────────


def test_cve_overlap_identical():
    assert _cve_overlap(["CVE-1"], ["CVE-1"]) == 1.0


def test_cve_overlap_empty():
    assert _cve_overlap([], ["CVE-1"]) == 0.0
    assert _cve_overlap(["CVE-1"], []) == 0.0
    assert _cve_overlap([], []) == 0.0


def test_cve_overlap_partial():
    # 1 common / min(2,2) = 0.5
    assert _cve_overlap(["CVE-1", "CVE-2"], ["CVE-1", "CVE-3"]) == 0.5


def test_cve_overlap_subset():
    # 1 common / min(1,3) = 1.0 (subset matches fully)
    assert _cve_overlap(["CVE-1"], ["CVE-1", "CVE-2", "CVE-3"]) == 1.0


# ─── _is_agreement ────────────────────────────────────────────────


def _make_row(**kwargs) -> dict:
    defaults = {
        "relevance_score": 8,
        "severity": "high",
        "cves_json": '["CVE-1"]',
        "matched_relevance_score": 8,
        "matched_severity": "high",
        "matched_cves_json": '["CVE-1"]',
    }
    defaults.update(kwargs)
    return defaults


def test_agreement_happy_path():
    assert _is_agreement(_make_row()) is True


def test_agreement_fails_on_severity_mismatch():
    row = _make_row(matched_severity="medium")
    assert _is_agreement(row) is False


def test_agreement_fails_on_relevance_gap():
    row = _make_row(relevance_score=9, matched_relevance_score=5)
    assert _is_agreement(row) is False


def test_agreement_ok_on_small_relevance_gap():
    row = _make_row(relevance_score=9, matched_relevance_score=7)
    assert _is_agreement(row) is True


def test_agreement_fails_on_cve_mismatch():
    row = _make_row(cves_json='["CVE-1"]', matched_cves_json='["CVE-999"]')
    assert _is_agreement(row) is False


def test_agreement_ok_when_both_cves_empty():
    row = _make_row(cves_json="[]", matched_cves_json="[]")
    assert _is_agreement(row) is True


def test_agreement_fails_when_only_one_side_has_cves():
    # One article has CVEs, the other doesn't → overlap = 0, below threshold
    row = _make_row(cves_json='["CVE-1"]', matched_cves_json="[]")
    assert _is_agreement(row) is False


def test_agreement_fails_when_matched_not_analyzed():
    row = _make_row(matched_relevance_score=None, matched_severity=None)
    assert _is_agreement(row) is False


# ─── compute_metrics ──────────────────────────────────────────────


async def test_compute_metrics_empty_db(state_db):
    m = await compute_metrics(days=14)
    assert m.total_logged == 0
    assert m.would_merge_count == 0
    assert m.agreement_rate is None
    assert m.alert_reduction_pct is None
    assert m.audit_precision is None


async def test_compute_metrics_coverage(state_db):
    await log_shadow_decision(
        article_id=1, matched_article_id=None, dice_score=None,
        would_merge=False, matched_already_analyzed=False,
        relevance_score=5, severity="medium", cves=[],
    )
    await log_shadow_decision(
        article_id=2, matched_article_id=10, dice_score=0.8,
        would_merge=False, matched_already_analyzed=False,
        relevance_score=6, severity="high", cves=[],
    )
    await log_shadow_decision(
        article_id=3, matched_article_id=11, dice_score=0.9,
        would_merge=True, matched_already_analyzed=True,
        relevance_score=7, severity="high", cves=["CVE-X"],
        matched_relevance_score=7, matched_severity="high", matched_cves=["CVE-X"],
    )

    m = await compute_metrics(days=14)
    assert m.total_logged == 3
    assert m.with_match == 2
    assert m.would_merge_count == 1
    assert m.agreement_eligible == 1
    assert m.agreement_count == 1
    assert m.agreement_rate == 1.0


async def test_compute_metrics_agreement_rate_partial(state_db):
    # Case 1: consistent pair → counts toward agreement
    await log_shadow_decision(
        article_id=1, matched_article_id=100, dice_score=0.9,
        would_merge=True, matched_already_analyzed=True,
        relevance_score=8, severity="high", cves=["CVE-1"],
        matched_relevance_score=8, matched_severity="high", matched_cves=["CVE-1"],
    )
    # Case 2: severity mismatch → disagreement
    await log_shadow_decision(
        article_id=2, matched_article_id=101, dice_score=0.85,
        would_merge=True, matched_already_analyzed=True,
        relevance_score=7, severity="high", cves=["CVE-2"],
        matched_relevance_score=6, matched_severity="medium", matched_cves=["CVE-2"],
    )

    m = await compute_metrics(days=14)
    assert m.agreement_eligible == 2
    assert m.agreement_count == 1
    assert m.agreement_rate == 0.5


async def test_compute_metrics_alert_reduction(state_db):
    # Article 1 was alerted and is similar to article 100 (which was also alerted)
    await save_analysis(
        {"id": 1, "title": "T1", "url": "u1", "feed_name": "f1"},
        {"relevance_score": 9, "severity": "critical"},
    )
    await mark_alert_sent(1, ["telegram"])
    await save_analysis(
        {"id": 100, "title": "T100", "url": "u100", "feed_name": "f100"},
        {"relevance_score": 8, "severity": "critical"},
    )
    await mark_alert_sent(100, ["telegram"])

    # Article 2 was alerted but no would_merge match → not reducible
    await save_analysis(
        {"id": 2, "title": "T2", "url": "u2", "feed_name": "f2"},
        {"relevance_score": 9, "severity": "critical"},
    )
    await mark_alert_sent(2, ["telegram"])

    await log_shadow_decision(
        article_id=1, matched_article_id=100, dice_score=0.9,
        would_merge=True, matched_already_analyzed=True,
        relevance_score=9, severity="critical", cves=[],
    )
    await log_shadow_decision(
        article_id=2, matched_article_id=None, dice_score=None,
        would_merge=False, matched_already_analyzed=False,
        relevance_score=9, severity="critical", cves=[],
    )

    m = await compute_metrics(days=14)
    assert m.alerts_total == 2  # articles 1 and 2 alerted (matched side isn't in shadow log)
    assert m.alerts_mergeable == 1  # only article 1
    assert m.alert_reduction_pct == 50.0


async def test_compute_metrics_audit(state_db):
    await log_shadow_decision(
        article_id=1, matched_article_id=100, dice_score=0.9,
        would_merge=True, matched_already_analyzed=True,
        relevance_score=8, severity="high", cves=[],
    )
    # Manually set audit labels
    import aiosqlite
    from src import state
    async with aiosqlite.connect(state.STATE_DB) as db:
        await db.execute(
            "UPDATE dedup_shadow_log SET audit_label = 'correct' WHERE article_id = 1"
        )
        await db.commit()

    m = await compute_metrics(days=14)
    assert m.audit_total == 1
    assert m.audit_correct == 1
    assert m.audit_false == 0
    assert m.audit_precision == 1.0


# ─── _recommend ──────────────────────────────────────────────────


def _metrics_stub(**kwargs):
    from src.metrics import DedupMetrics
    defaults = dict(
        days=14, total_logged=100, with_match=80, coverage_pct=80.0,
        would_merge_count=20, agreement_eligible=15, agreement_count=14,
        agreement_rate=0.93,
        alerts_total=50, alerts_mergeable=10, alert_reduction_pct=20.0,
        audit_total=20, audit_correct=19, audit_false=1, audit_precision=0.95,
    )
    defaults.update(kwargs)
    return DedupMetrics(**defaults)


def test_recommend_needs_more_data():
    m = _metrics_stub(would_merge_count=5)
    assert "Keep collecting" in _recommend(m)


def test_recommend_stop_on_low_precision():
    m = _metrics_stub(audit_precision=0.70)
    assert "STOP" in _recommend(m)


def test_recommend_ship_option_a():
    m = _metrics_stub(agreement_rate=0.95, audit_precision=0.96, alert_reduction_pct=20.0)
    assert "Option A" in _recommend(m)


def test_recommend_noop_when_no_noise():
    m = _metrics_stub(agreement_rate=0.95, audit_precision=0.96, alert_reduction_pct=5.0)
    assert "NO-OP" in _recommend(m)


def test_recommend_upgrade_to_c():
    m = _metrics_stub(agreement_rate=0.80, audit_precision=0.90, alert_reduction_pct=25.0)
    assert "Option C" in _recommend(m)


def test_recommend_option_b_weak_signal():
    m = _metrics_stub(agreement_rate=0.60, audit_precision=0.90, alert_reduction_pct=25.0)
    assert "Option B" in _recommend(m)


# ─── format_report ───────────────────────────────────────────────


def test_format_report_empty():
    m = _metrics_stub(
        total_logged=0, with_match=0, coverage_pct=0,
        would_merge_count=0, agreement_rate=None,
        alerts_total=0, alert_reduction_pct=None,
        audit_total=0, audit_correct=0, audit_false=0, audit_precision=None,
    )
    report = format_report(m)
    assert "Dedup Metrics" in report
    assert "No eligible pairs" in report
    assert "No manual labels" in report


def test_format_report_populated():
    m = _metrics_stub()
    report = format_report(m)
    assert "80.0%" in report  # coverage
    assert "93.3%" in report or "93.0%" in report  # agreement rate
