"""Tests for src/ai_analyzer.py — prompt building, JSON parsing, analyze flow."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.ai_analyzer import AIAnalyzer, build_company_context_text, load_company_profile


# ── Helper: create analyzer without hitting real __init__ side-effects ──


def _make_analyzer():
    with patch.object(AIAnalyzer, "__init__", lambda self: None):
        a = AIAnalyzer()
    a.profile = {}
    a.company_context = ""
    a.provider = "anthropic"
    a.system_prompt = "test"
    return a


# ── _parse_response ─────────────────────────────────────────────────


class TestParseResponse:
    def setup_method(self):
        self.analyzer = _make_analyzer()

    def test_valid_json(self):
        r = self.analyzer._parse_response(
            json.dumps({"relevance_score": 8, "severity": "high", "summary_vi": "Ok"})
        )
        assert r["relevance_score"] == 8
        assert r["severity"] == "high"

    def test_markdown_wrapped(self):
        text = '```json\n{"relevance_score": 5, "severity": "medium"}\n```'
        assert self.analyzer._parse_response(text)["relevance_score"] == 5

    def test_extra_text_around_json(self):
        text = 'Here is the result:\n{"relevance_score": 3, "severity": "low"}\nDone.'
        assert self.analyzer._parse_response(text)["relevance_score"] == 3

    def test_invalid_json_returns_none(self):
        assert self.analyzer._parse_response("not json") is None

    def test_empty_string_returns_none(self):
        assert self.analyzer._parse_response("") is None

    def test_clamps_score_above_10(self):
        r = self.analyzer._parse_response('{"relevance_score": 15}')
        assert r["relevance_score"] == 10

    def test_clamps_score_below_0(self):
        r = self.analyzer._parse_response('{"relevance_score": -3}')
        assert r["relevance_score"] == 0

    def test_non_numeric_score_defaults_to_0(self):
        r = self.analyzer._parse_response('{"relevance_score": "high"}')
        assert r["relevance_score"] == 0

    def test_defaults_for_missing_fields(self):
        r = self.analyzer._parse_response('{"relevance_score": 5}')
        assert r["severity"] == "info"
        assert r["summary_vi"] == ""
        assert r["cve_ids"] == []
        assert r["affected_products"] == []
        assert r["threat_actors"] == []
        assert r["mitre_attack"] == []
        assert r["recommendations"] == []
        assert r["relevance_reason"] == ""

    def test_json_extraction_fallback_fails(self):
        """Text with braces but invalid JSON inside should return None."""
        text = 'Here is the result: {not: valid json} done.'
        assert self.analyzer._parse_response(text) is None

    def test_preserves_all_fields(self, sample_analysis):
        text = json.dumps(sample_analysis)
        r = self.analyzer._parse_response(text)
        assert r["cve_ids"] == ["CVE-2024-12345"]
        assert len(r["mitre_attack"]) == 1
        assert len(r["recommendations"]) == 2


# ── build_company_context_text ──────────────────────────────────────


class TestBuildCompanyContext:
    def test_empty_profile(self):
        assert build_company_context_text({}) == "No company profile configured."

    def test_none_profile(self):
        assert build_company_context_text(None) == "No company profile configured."

    def test_full_profile(self, company_profile):
        text = build_company_context_text(company_profile)
        assert "Test Corp" in text
        assert "financial_services" in text
        assert "Vietnam" in text
        assert "VMware ESXi 8" in text
        assert "APT32 (OceanLotus)" in text
        assert "T1190" in text
        assert "banking" in text
        assert "cryptocurrency mining" in text

    def test_partial_profile_company_only(self):
        text = build_company_context_text(
            {"company": {"name": "X Corp", "sector": ["tech"], "country": "US"}}
        )
        assert "X Corp" in text
        assert "tech" in text


# ── AIAnalyzer.analyze ──────────────────────────────────────────────


class TestAnalyze:
    @pytest.fixture
    def analyzer(self, company_profile):
        with patch("src.ai_analyzer.load_company_profile", return_value=company_profile):
            return AIAnalyzer()

    async def test_skips_short_content(self, analyzer):
        assert await analyzer.analyze({"content": "short", "id": 1}) is None

    async def test_calls_dispatch_and_parses(self, analyzer, sample_article):
        resp = json.dumps({"relevance_score": 7, "severity": "high", "summary_vi": "Ok"})
        with patch("src.ai_analyzer.dispatch", new_callable=AsyncMock, return_value=resp):
            r = await analyzer.analyze(sample_article)
        assert r["relevance_score"] == 7

    async def test_returns_none_on_dispatch_error(self, analyzer, sample_article):
        with patch("src.ai_analyzer.dispatch", new_callable=AsyncMock, side_effect=Exception("boom")):
            assert await analyzer.analyze(sample_article) is None

    async def test_returns_none_on_bad_json(self, analyzer, sample_article):
        with patch("src.ai_analyzer.dispatch", new_callable=AsyncMock, return_value="not json"):
            assert await analyzer.analyze(sample_article) is None


# ── load_company_profile ──────────────────────────────────────────


class TestLoadCompanyProfile:
    def test_reads_yaml_file(self, tmp_path):
        profile_path = tmp_path / "profile.yaml"
        profile_path.write_text(
            "company:\n  name: TestCo\n  sector:\n    - tech\n",
            encoding="utf-8",
        )
        with patch("src.ai_analyzer.COMPANY_PROFILE_PATH", str(profile_path)):
            result = load_company_profile()
        assert result["company"]["name"] == "TestCo"

    def test_missing_file_returns_empty(self, tmp_path):
        with patch("src.ai_analyzer.COMPANY_PROFILE_PATH", str(tmp_path / "nope.yaml")):
            assert load_company_profile() == {}

    def test_empty_yaml_returns_empty(self, tmp_path):
        profile_path = tmp_path / "empty.yaml"
        profile_path.write_text("", encoding="utf-8")
        with patch("src.ai_analyzer.COMPANY_PROFILE_PATH", str(profile_path)):
            assert load_company_profile() == {}
