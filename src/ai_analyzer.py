"""AI Analyzer - supports Anthropic, DeepSeek, and OpenAI-compatible APIs.

Loads company profile and builds a context-aware prompt that returns
structured JSON with relevance_score, severity, summary, MITRE mapping,
and actionable SOC recommendations.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

import yaml

from src.ai_providers import ALL_PROVIDERS, dispatch

logger = logging.getLogger(__name__)

COMPANY_PROFILE_PATH = os.getenv(
    "COMPANY_PROFILE_PATH", "config/company_profile.yaml"
)


def load_company_profile() -> dict:
    """Load company profile from YAML."""
    path = Path(COMPANY_PROFILE_PATH)
    if not path.exists():
        logger.warning(f"Company profile not found: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_company_context_text(profile: dict) -> str:
    """Build a concise company context string for the AI prompt."""
    if not profile:
        return "No company profile configured."

    parts = []
    company = profile.get("company", {})
    if company:
        parts.append(
            f"Company: {company.get('name', 'N/A')} "
            f"| Sector: {', '.join(company.get('sector', []))} "
            f"| Country: {company.get('country', 'N/A')}"
        )

    tech = profile.get("tech_stack", {})
    if tech:
        tech_lines = []
        for category, items in tech.items():
            if items:
                tech_lines.append(f"  - {category}: {', '.join(items)}")
        if tech_lines:
            parts.append("Tech stack in use:\n" + "\n".join(tech_lines))

    actors = profile.get("watched_threat_actors", [])
    if actors:
        actor_names = []
        for a in actors:
            name = a.get("name", "")
            alias = a.get("alias", "")
            actor_names.append(f"{name}" + (f" ({alias})" if alias else ""))
        parts.append(f"Watched threat actors: {', '.join(actor_names)}")

    techniques = profile.get("priority_techniques", [])
    if techniques:
        parts.append(f"Priority MITRE techniques: {', '.join(techniques)}")

    boost = profile.get("boost_keywords", [])
    if boost:
        parts.append(f"Boost relevance for keywords: {', '.join(boost)}")

    reduce = profile.get("reduce_keywords", [])
    if reduce:
        parts.append(f"Reduce relevance for keywords: {', '.join(reduce)}")

    return "\n\n".join(parts)


SYSTEM_PROMPT_TEMPLATE = """Bạn là một AI Security Analyst chuyên phân tích threat intelligence cho một công ty cụ thể.

COMPANY CONTEXT:
{company_context}

NHIỆM VỤ:
Đọc bài viết bảo mật và đánh giá MỨC ĐỘ LIÊN QUAN đến công ty trên.

QUY TẮC:
1. relevance_score (0-10):
   - 9-10: Trực tiếp ảnh hưởng tech stack của công ty, cần hành động ngay
   - 7-8: Có liên quan rõ ràng (sản phẩm/vendor đang dùng, threat actor đang theo dõi)
   - 5-6: Liên quan gián tiếp (sector, geography, hoặc technique ưu tiên)
   - 3-4: Tin tức chung trong ngành, cần theo dõi
   - 0-2: Không liên quan

2. severity (dựa trên threat itself, không phụ thuộc relevance):
   - critical: 0-day đang bị khai thác tích cực
   - high: PoC exploit, vuln nghiêm trọng, breach lớn
   - medium: Advisory quan trọng, threat đáng chú ý
   - low: Tham khảo, không khẩn cấp
   - info: Tin tổng hợp, ý kiến

3. summary_vi: Tóm tắt tiếng Việt 2-4 câu, tập trung vào impact với công ty.

4. relevance_reason: Giải thích NGẮN (1-2 câu) tại sao cho điểm đó. Phải đề cập cụ thể tech/actor nếu match.

5. mitre_attack: Chỉ map khi bài viết mô tả rõ ràng technique. Format: [{{"tactic": "TA00xx - Name", "technique": "Txxxx - Name"}}]

6. recommendations: 1-3 hành động cụ thể cho SOC team của công ty (không chung chung).

7. cve_ids, affected_products, threat_actors: extract nếu có.

LUÔN trả về JSON hợp lệ, KHÔNG wrap trong markdown code block."""


USER_PROMPT_TEMPLATE = """Phân tích bài viết sau:

**Nguồn:** {feed_name}
**Tiêu đề:** {title}
**URL:** {url}
**Ngày:** {published_at}
**Ngôn ngữ:** {language}

**Nội dung:**
{content}

---
Trả về JSON:
{{
  "relevance_score": 0-10,
  "relevance_reason": "Tại sao cho điểm này, reference cụ thể tech/actor",
  "severity": "critical|high|medium|low|info",
  "summary_vi": "Tóm tắt tiếng Việt 2-4 câu",
  "cve_ids": ["CVE-YYYY-NNNNN"],
  "affected_products": ["product1"],
  "threat_actors": ["actor1"],
  "mitre_attack": [{{"tactic": "TA00xx - Name", "technique": "Txxxx - Name"}}],
  "recommendations": ["action 1", "action 2"]
}}"""


class AIAnalyzer:
    """Multi-provider AI analyzer.

    Supports 13+ providers via src/ai_providers. Set AI_PROVIDER env var to
    one of: {providers}.
    """.format(providers=", ".join(ALL_PROVIDERS))

    def __init__(self):
        self.profile = load_company_profile()
        self.company_context = build_company_context_text(self.profile)
        self.provider = os.getenv("AI_PROVIDER", "anthropic").lower()
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            company_context=self.company_context
        )
        logger.info(f"AI Analyzer initialized: provider={self.provider}")
        logger.debug(f"Company context:\n{self.company_context}")

    async def analyze(self, article: dict) -> Optional[dict]:
        """Analyze a single article, return structured analysis."""
        content = article.get("content", "")
        if len(content) < 50:
            return None

        user_prompt = USER_PROMPT_TEMPLATE.format(
            feed_name=article.get("feed_name", "Unknown"),
            title=article.get("title", ""),
            url=article.get("url", ""),
            published_at=article.get("published_at", "Unknown"),
            language=article.get("language", "en"),
            content=content[:8000],
        )

        try:
            response_text = await dispatch(
                self.provider, self.system_prompt, user_prompt
            )
            return self._parse_response(response_text)

        except Exception as e:
            logger.error(f"AI analysis failed for article {article.get('id')}: {e}")
            return None

    def _parse_response(self, text: str) -> Optional[dict]:
        """Parse AI JSON response, handling markdown wrapping gracefully."""
        text = text.strip()

        # Strip markdown code blocks if present
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    result = json.loads(text[start:end])
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse AI response: {text[:200]}")
                    return None
            else:
                return None

        # Ensure all expected fields present with sensible defaults
        result.setdefault("relevance_score", 0)
        result.setdefault("relevance_reason", "")
        result.setdefault("severity", "info")
        result.setdefault("summary_vi", "")
        result.setdefault("cve_ids", [])
        result.setdefault("affected_products", [])
        result.setdefault("threat_actors", [])
        result.setdefault("mitre_attack", [])
        result.setdefault("recommendations", [])

        # Clamp relevance score
        try:
            score = int(result["relevance_score"])
            result["relevance_score"] = max(0, min(10, score))
        except (ValueError, TypeError):
            result["relevance_score"] = 0

        return result
