"""Tests for src/profile_generator.py — input collection, validation, LLM flow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from src.profile_generator import (
    DEFAULT_INPUT_DIR,
    DEFAULT_OUTPUT,
    MAX_INPUT_CHARS,
    REQUIRED_TOP_KEYS,
    SUPPORTED_EXTENSIONS,
    InputBundle,
    _strip_fences,
    build_user_prompt,
    collect_inputs,
    generate_profile,
    save_profile,
    validate_profile,
)


VALID_YAML = """\
company:
  name: Test Corp
  sector: [fintech]
  country: Vietnam
  size: medium
tech_stack:
  operating_systems: [Ubuntu 22.04]
  cloud: [AWS]
"""


# ── module-level constants ──────────────────────────────────────────


class TestModuleConstants:
    def test_supported_extensions_includes_markdown_and_structured(self):
        assert ".md" in SUPPORTED_EXTENSIONS
        assert ".yaml" in SUPPORTED_EXTENSIONS
        assert ".json" in SUPPORTED_EXTENSIONS
        assert ".csv" in SUPPORTED_EXTENSIONS

    def test_default_paths_point_at_config_dir(self):
        assert DEFAULT_INPUT_DIR == Path("config/inputs")
        assert DEFAULT_OUTPUT == Path("config/company_profile.generated.yaml")

    def test_required_keys_match_schema(self):
        assert "company" in REQUIRED_TOP_KEYS
        assert "tech_stack" in REQUIRED_TOP_KEYS


# ── _strip_fences ────────────────────────────────────────────────────


class TestStripFences:
    def test_plain_yaml_passthrough(self):
        text = "company:\n  name: X\n"
        assert _strip_fences(text) == text.strip()

    def test_yaml_fence_extracted(self):
        text = "```yaml\ncompany:\n  name: X\n```"
        assert _strip_fences(text) == "company:\n  name: X"

    def test_yml_fence_extracted(self):
        text = "```yml\na: 1\n```"
        assert _strip_fences(text) == "a: 1"

    def test_bare_fence_extracted(self):
        text = "```\nfoo: bar\n```"
        assert _strip_fences(text) == "foo: bar"

    def test_case_insensitive_language_tag(self):
        text = "```YAML\na: 1\n```"
        assert _strip_fences(text) == "a: 1"

    def test_extracts_from_surrounding_prose(self):
        text = "Here's the profile:\n```yaml\na: 1\n```\nHope this helps!"
        assert _strip_fences(text) == "a: 1"


# ── validate_profile ────────────────────────────────────────────────


class TestValidateProfile:
    def test_accepts_minimal_valid_yaml(self):
        profile = validate_profile(VALID_YAML)
        assert profile["company"]["name"] == "Test Corp"
        assert profile["tech_stack"]["cloud"] == ["AWS"]

    def test_accepts_fenced_yaml(self):
        fenced = f"```yaml\n{VALID_YAML}```"
        profile = validate_profile(fenced)
        assert profile["company"]["name"] == "Test Corp"

    def test_rejects_unparseable_yaml(self):
        with pytest.raises(ValueError, match="not valid YAML"):
            validate_profile("company:\n  name: X\n  : invalid")

    def test_rejects_non_mapping_top_level(self):
        with pytest.raises(ValueError, match="top level"):
            validate_profile("- just\n- a\n- list\n")

    def test_rejects_missing_company(self):
        with pytest.raises(ValueError, match="missing required keys"):
            validate_profile("tech_stack: {cloud: []}\n")

    def test_rejects_missing_tech_stack(self):
        with pytest.raises(ValueError, match="missing required keys"):
            validate_profile("company: {name: X, sector: [x], country: Y, size: small}\n")

    def test_rejects_company_without_name(self):
        yaml_text = "company:\n  sector: [x]\ntech_stack: {cloud: []}\n"
        with pytest.raises(ValueError, match="company.name"):
            validate_profile(yaml_text)

    def test_rejects_tech_stack_not_mapping(self):
        yaml_text = "company: {name: X}\ntech_stack:\n  - a\n  - b\n"
        with pytest.raises(ValueError, match="tech_stack"):
            validate_profile(yaml_text)


# ── collect_inputs ──────────────────────────────────────────────────


class TestCollectInputs:
    def test_raises_when_directory_missing(self, tmp_path):
        missing = tmp_path / "does_not_exist"
        with pytest.raises(FileNotFoundError, match="not found"):
            collect_inputs(missing)

    def test_raises_when_directory_empty(self, tmp_path):
        with pytest.raises(ValueError, match="No readable input files"):
            collect_inputs(tmp_path)

    def test_raises_when_only_unsupported_extensions(self, tmp_path):
        (tmp_path / "ignored.exe").write_text("binary")
        (tmp_path / "also.png").write_bytes(b"\x89PNG")
        with pytest.raises(ValueError, match="No readable input files"):
            collect_inputs(tmp_path)

    def test_collects_supported_extensions(self, tmp_path):
        (tmp_path / "a.md").write_text("# A", encoding="utf-8")
        (tmp_path / "b.txt").write_text("B", encoding="utf-8")
        (tmp_path / "c.yaml").write_text("c: 1", encoding="utf-8")
        (tmp_path / "d.json").write_text('{"d": 1}', encoding="utf-8")
        (tmp_path / "e.csv").write_text("x,y\n1,2", encoding="utf-8")

        bundle = collect_inputs(tmp_path)

        assert isinstance(bundle, InputBundle)
        assert len(bundle.files) == 5
        names = {name for name, _ in bundle.files}
        assert names == {"a.md", "b.txt", "c.yaml", "d.json", "e.csv"}

    def test_skips_readme_case_insensitive(self, tmp_path):
        (tmp_path / "README.md").write_text("guide", encoding="utf-8")
        (tmp_path / "real.md").write_text("real content", encoding="utf-8")

        bundle = collect_inputs(tmp_path)
        names = {name for name, _ in bundle.files}
        assert names == {"real.md"}

    def test_skips_hidden_dotfiles(self, tmp_path):
        (tmp_path / ".hidden.md").write_text("hidden", encoding="utf-8")
        (tmp_path / "visible.md").write_text("visible", encoding="utf-8")

        bundle = collect_inputs(tmp_path)
        names = {name for name, _ in bundle.files}
        assert names == {"visible.md"}

    def test_skips_unsupported_extensions(self, tmp_path):
        (tmp_path / "keep.md").write_text("md", encoding="utf-8")
        (tmp_path / "drop.exe").write_text("exe")
        (tmp_path / "drop.log").write_text("log")

        bundle = collect_inputs(tmp_path)
        names = {name for name, _ in bundle.files}
        assert names == {"keep.md"}

    def test_recurses_into_subdirectories(self, tmp_path):
        sub = tmp_path / "deep" / "nested"
        sub.mkdir(parents=True)
        (sub / "buried.md").write_text("deep", encoding="utf-8")
        (tmp_path / "top.md").write_text("top", encoding="utf-8")

        bundle = collect_inputs(tmp_path)
        names = {name for name, _ in bundle.files}
        assert names == {"top.md", "deep/nested/buried.md"}

    def test_stops_at_char_budget(self, tmp_path):
        # Each chunk is slightly over half the budget, so file #2 pushes past
        # MAX_INPUT_CHARS and triggers the break before file #3 is read.
        chunk = "x" * (MAX_INPUT_CHARS // 2 + 5_000)
        (tmp_path / "01.md").write_text(chunk, encoding="utf-8")
        (tmp_path / "02.md").write_text(chunk, encoding="utf-8")
        (tmp_path / "03.md").write_text(chunk, encoding="utf-8")

        bundle = collect_inputs(tmp_path)

        assert len(bundle.files) == 2
        assert bundle.total_chars > MAX_INPUT_CHARS

    def test_sorts_files_deterministically(self, tmp_path):
        (tmp_path / "zzz.md").write_text("z", encoding="utf-8")
        (tmp_path / "aaa.md").write_text("a", encoding="utf-8")
        (tmp_path / "mmm.md").write_text("m", encoding="utf-8")

        bundle = collect_inputs(tmp_path)
        names = [name for name, _ in bundle.files]
        assert names == ["aaa.md", "mmm.md", "zzz.md"]

    def test_skips_undecodable_files_and_continues(self, tmp_path):
        (tmp_path / "good.md").write_text("readable", encoding="utf-8")
        # Stray continuation bytes — strict UTF-8 decoder raises.
        (tmp_path / "bad.md").write_bytes(b"\xff\xfe\xfd\xfc")

        bundle = collect_inputs(tmp_path)
        names = {name for name, _ in bundle.files}
        assert names == {"good.md"}


# ── build_user_prompt ───────────────────────────────────────────────


class TestBuildUserPrompt:
    def test_includes_schema_and_source_markers(self):
        bundle = InputBundle(files=(("a.md", "content A"),), total_chars=9)
        prompt = build_user_prompt(bundle)
        assert "### SCHEMA" in prompt
        assert "### SOURCE MATERIAL" in prompt
        assert "=== INPUT FILE: a.md ===" in prompt
        assert "content A" in prompt

    def test_concatenates_multiple_files_in_order(self):
        bundle = InputBundle(
            files=(("a.md", "AA"), ("b.txt", "BB")), total_chars=4
        )
        prompt = build_user_prompt(bundle)
        assert prompt.index("a.md") < prompt.index("b.txt")


# ── generate_profile (dispatch mocked) ──────────────────────────────


class TestGenerateProfile:
    @pytest.mark.asyncio
    async def test_happy_path(self, tmp_path):
        (tmp_path / "overview.md").write_text(
            "Fintech company in Vietnam using AWS.", encoding="utf-8"
        )
        mock_dispatch = AsyncMock(return_value=VALID_YAML)

        with patch("src.profile_generator.dispatch", mock_dispatch):
            profile = await generate_profile(tmp_path)

        assert profile["company"]["name"] == "Test Corp"
        mock_dispatch.assert_awaited_once()
        args, _ = mock_dispatch.call_args
        # dispatch(provider, system_prompt, user_prompt) — user_prompt must
        # contain the source text so the LLM sees what we actually read.
        assert "Fintech company in Vietnam" in args[2]

    @pytest.mark.asyncio
    async def test_unwraps_fenced_response(self, tmp_path):
        (tmp_path / "in.md").write_text("company notes", encoding="utf-8")
        fenced = f"Sure!\n```yaml\n{VALID_YAML}```\n"
        mock_dispatch = AsyncMock(return_value=fenced)

        with patch("src.profile_generator.dispatch", mock_dispatch):
            profile = await generate_profile(tmp_path)

        assert profile["company"]["name"] == "Test Corp"

    @pytest.mark.asyncio
    async def test_propagates_validation_errors(self, tmp_path):
        (tmp_path / "in.md").write_text("x", encoding="utf-8")
        mock_dispatch = AsyncMock(return_value="just a string, not a mapping")

        with patch("src.profile_generator.dispatch", mock_dispatch):
            with pytest.raises(ValueError):
                await generate_profile(tmp_path)

    @pytest.mark.asyncio
    async def test_explicit_provider_wins_over_env(self, tmp_path, monkeypatch):
        (tmp_path / "in.md").write_text("x", encoding="utf-8")
        monkeypatch.setenv("AI_PROVIDER", "deepseek")
        mock_dispatch = AsyncMock(return_value=VALID_YAML)

        with patch("src.profile_generator.dispatch", mock_dispatch):
            await generate_profile(tmp_path, provider="anthropic")

        args, _ = mock_dispatch.call_args
        assert args[0] == "anthropic"

    @pytest.mark.asyncio
    async def test_falls_back_to_env_and_lowercases(self, tmp_path, monkeypatch):
        (tmp_path / "in.md").write_text("x", encoding="utf-8")
        monkeypatch.setenv("AI_PROVIDER", "DeepSeek")
        mock_dispatch = AsyncMock(return_value=VALID_YAML)

        with patch("src.profile_generator.dispatch", mock_dispatch):
            await generate_profile(tmp_path)

        args, _ = mock_dispatch.call_args
        assert args[0] == "deepseek"

    @pytest.mark.asyncio
    async def test_propagates_missing_input_dir(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            await generate_profile(tmp_path / "missing")


# ── save_profile ────────────────────────────────────────────────────


class TestSaveProfile:
    def test_writes_valid_yaml_file(self, tmp_path):
        profile = {"company": {"name": "X"}, "tech_stack": {"cloud": ["AWS"]}}
        out = tmp_path / "out.yaml"

        path = save_profile(profile, out)

        assert path == out
        roundtrip = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert roundtrip == profile

    def test_refuses_to_overwrite_by_default(self, tmp_path):
        out = tmp_path / "out.yaml"
        out.write_text("existing", encoding="utf-8")

        with pytest.raises(FileExistsError, match="--overwrite"):
            save_profile({"company": {"name": "X"}, "tech_stack": {}}, out)

        assert out.read_text(encoding="utf-8") == "existing"

    def test_overwrites_when_flag_set(self, tmp_path):
        out = tmp_path / "out.yaml"
        out.write_text("existing", encoding="utf-8")
        profile = {"company": {"name": "New"}, "tech_stack": {}}

        save_profile(profile, out, overwrite=True)

        assert yaml.safe_load(out.read_text(encoding="utf-8")) == profile

    def test_creates_missing_parent_directory(self, tmp_path):
        out = tmp_path / "nested" / "deeper" / "out.yaml"
        profile = {"company": {"name": "X"}, "tech_stack": {}}

        save_profile(profile, out)

        assert out.exists()

    def test_preserves_key_order(self, tmp_path):
        profile = {
            "company": {"name": "X"},
            "tech_stack": {"cloud": ["AWS"]},
            "watched_threat_actors": [],
            "priority_techniques": [],
            "boost_keywords": [],
            "reduce_keywords": [],
        }
        out = tmp_path / "out.yaml"

        save_profile(profile, out)

        text = out.read_text(encoding="utf-8")
        # sort_keys=False must keep schema-significant ordering intact.
        assert text.index("company:") < text.index("tech_stack:")
        assert text.index("tech_stack:") < text.index("watched_threat_actors:")

    def test_writes_unicode_without_escaping(self, tmp_path):
        profile = {
            "company": {"name": "Công ty ABC"},
            "tech_stack": {},
            "boost_keywords": ["lừa đảo", "rửa tiền"],
        }
        out = tmp_path / "out.yaml"

        save_profile(profile, out)

        text = out.read_text(encoding="utf-8")
        assert "Công ty ABC" in text
        assert "lừa đảo" in text
