"""Profile Generator — build company_profile.yaml from description files via LLM.

Reads unstructured company/asset-inventory documents (Markdown, text, YAML,
JSON, CSV) from an input directory, concatenates them into a prompt, and asks
the configured LLM provider to emit a structured YAML profile matching the
schema consumed by ``src.ai_analyzer``.

Output is written to ``config/company_profile.generated.yaml`` by default —
the active ``company_profile.yaml`` is never overwritten unless ``overwrite``
is explicitly set.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.ai_providers import dispatch

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: tuple[str, ...] = (
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".csv",
)
DEFAULT_INPUT_DIR = Path("config/inputs")
DEFAULT_OUTPUT = Path("config/company_profile.generated.yaml")
MAX_INPUT_CHARS = 100_000

REQUIRED_TOP_KEYS: tuple[str, ...] = ("company", "tech_stack")

SYSTEM_PROMPT = """You are a security-aware assistant that converts unstructured \
company description documents into a structured threat-intelligence profile.

You MUST output ONLY valid YAML matching the exact schema shown.
Do NOT include explanation, commentary, markdown code fences, or text outside the YAML.
Do NOT invent facts unsupported by the input.
You MAY infer reasonable `watched_threat_actors` and `priority_techniques` from the \
company's sector and geography, drawing on public threat-intelligence reporting \
(MITRE ATT&CK, Mandiant, CrowdStrike, public CERT advisories). For every inferred \
threat actor include a short `reason` that cites the sector/geo basis.

Always quote strings that contain colons. Prefer concrete vendor+version names in \
`tech_stack` when the source material supports it."""

SCHEMA_TEMPLATE = """company:
  name: "..."
  sector:
    - "..."
  country: "..."
  size: "small | medium | large | enterprise"

tech_stack:
  operating_systems: []
  hypervisor: []
  network_security: []
  endpoint_security: []
  identity: []
  productivity: []
  databases: []
  web_servers: []
  dev_tools: []
  cloud: []

watched_threat_actors:
  - name: "..."
    alias: "..."        # optional; omit the key if unknown
    reason: "..."

priority_techniques:
  - "T1190"             # MITRE ATT&CK technique IDs

boost_keywords: []
reduce_keywords: []
"""


@dataclass(frozen=True)
class InputBundle:
    files: tuple[tuple[str, str], ...]
    total_chars: int


def collect_inputs(input_dir: Path) -> InputBundle:
    """Read all supported files under *input_dir* into an InputBundle.

    Raises FileNotFoundError if the directory is missing, ValueError if it
    contains no readable input files.
    """
    if not input_dir.exists():
        raise FileNotFoundError(
            f"Input directory not found: {input_dir}. "
            f"Create it and add {', '.join(SUPPORTED_EXTENSIONS)} files "
            "describing your company."
        )

    collected: list[tuple[str, str]] = []
    total = 0
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if path.name.startswith("."):
            continue
        if path.name.lower() == "readme.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Skipping %s: %s", path, exc)
            continue

        rel = path.relative_to(input_dir).as_posix()
        collected.append((rel, text))
        total += len(text)
        if total > MAX_INPUT_CHARS:
            logger.warning(
                "Input char budget exceeded (%d > %d); stopping at %s",
                total,
                MAX_INPUT_CHARS,
                rel,
            )
            break

    if not collected:
        raise ValueError(
            f"No readable input files in {input_dir}. "
            f"Supported extensions: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
    return InputBundle(files=tuple(collected), total_chars=total)


def build_user_prompt(bundle: InputBundle) -> str:
    """Concatenate input files into a single user-facing prompt."""
    sections = [
        f"=== INPUT FILE: {name} ===\n{content.strip()}"
        for name, content in bundle.files
    ]
    joined_inputs = "\n\n".join(sections)
    return (
        "Below is the source material describing the company, its sector, "
        "operations, and asset inventory. Produce a single YAML document "
        "matching the schema.\n\n"
        f"### SCHEMA\n{SCHEMA_TEMPLATE}\n\n"
        f"### SOURCE MATERIAL\n{joined_inputs}\n\n"
        "Now output the YAML profile:"
    )


_FENCE_RE = re.compile(
    r"```(?:ya?ml)?\s*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def _strip_fences(text: str) -> str:
    """Remove an optional ```yaml ... ``` fence. Idempotent on plain YAML."""
    match = _FENCE_RE.search(text)
    if match:
        return match.group("body").strip()
    return text.strip()


def validate_profile(text: str) -> dict:
    """Parse and validate the LLM output, returning the profile dict.

    Raises ValueError on any structural problem.
    """
    stripped = _strip_fences(text)
    try:
        profile = yaml.safe_load(stripped)
    except yaml.YAMLError as exc:
        raise ValueError(f"LLM output is not valid YAML: {exc}") from exc

    if not isinstance(profile, dict):
        raise ValueError(
            "LLM output must be a YAML mapping at the top level; "
            f"got {type(profile).__name__}"
        )

    missing = [key for key in REQUIRED_TOP_KEYS if key not in profile]
    if missing:
        raise ValueError(f"LLM output missing required keys: {missing}")

    company = profile.get("company")
    if not isinstance(company, dict) or not company.get("name"):
        raise ValueError("`company.name` is required")

    tech_stack = profile.get("tech_stack")
    if not isinstance(tech_stack, dict):
        raise ValueError("`tech_stack` must be a mapping of category -> list")

    return profile


async def generate_profile(
    input_dir: Path = DEFAULT_INPUT_DIR, provider: str | None = None
) -> dict:
    """Read *input_dir*, call the LLM, and return a validated profile dict."""
    bundle = collect_inputs(input_dir)
    logger.info(
        "Collected %d input file(s), %d chars total",
        len(bundle.files),
        bundle.total_chars,
    )

    resolved_provider = (provider or os.getenv("AI_PROVIDER", "anthropic")).lower()
    user_prompt = build_user_prompt(bundle)
    logger.info("Calling LLM provider=%s for profile generation", resolved_provider)
    response = await dispatch(resolved_provider, SYSTEM_PROMPT, user_prompt)
    return validate_profile(response)


def save_profile(
    profile: dict, output_path: Path = DEFAULT_OUTPUT, overwrite: bool = False
) -> Path:
    """Write *profile* as YAML to *output_path*. Refuses to overwrite by default."""
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"{output_path} already exists. Use --overwrite to replace it."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(
            profile,
            fh,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    logger.info("Wrote profile to %s", output_path)
    return output_path
