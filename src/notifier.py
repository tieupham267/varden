"""Notification Service - Telegram, Slack, Email.

Messages prominently display relevance_score (the main innovation of the sidecar)
alongside severity, so analysts see "why should I care about this" immediately.
"""

import asyncio
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

logger = logging.getLogger(__name__)

SEVERITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
    "info": "⚪",
}

SEVERITY_COLOR = {
    "critical": "#dc3545",
    "high": "#fd7e14",
    "medium": "#ffc107",
    "low": "#0d6efd",
    "info": "#6c757d",
}


def format_telegram_message(article: dict, analysis: dict) -> str:
    """Format HTML message for Telegram."""
    sev = analysis.get("severity", "info")
    emoji = SEVERITY_EMOJI.get(sev, "⚪")
    score = analysis.get("relevance_score", 0)

    # Score indicator bar
    filled = "●" * score
    empty = "○" * (10 - score)
    score_bar = f"{filled}{empty}"

    lines = [
        f"{emoji} <b>[{sev.upper()}]</b> Relevance: <b>{score}/10</b> {score_bar}",
        "",
        f"<b>{_escape_html(article.get('title', ''))}</b>",
        "",
        f"📝 {_escape_html(analysis.get('summary_vi', ''))}",
        "",
        f"🎯 <i>{_escape_html(analysis.get('relevance_reason', ''))}</i>",
    ]

    # CVEs
    cves = analysis.get("cve_ids", [])
    if cves:
        lines.append(f"\n🔖 CVE: {', '.join(cves[:5])}")

    # Affected products (critical for showing company relevance)
    products = analysis.get("affected_products", [])
    if products:
        lines.append(f"📦 Sản phẩm: {', '.join(products[:5])}")

    # Threat actors
    actors = analysis.get("threat_actors", [])
    if actors:
        lines.append(f"👥 Actors: {', '.join(actors[:3])}")

    # MITRE techniques
    mitre = analysis.get("mitre_attack", [])
    if mitre:
        techs = [m.get("technique", "") for m in mitre[:3] if m.get("technique")]
        if techs:
            lines.append(f"⚔️ MITRE: {', '.join(techs)}")

    # Recommendations
    recs = analysis.get("recommendations", [])
    if recs:
        lines.append("\n💡 <b>Khuyến nghị:</b>")
        for r in recs[:3]:
            lines.append(f"  • {_escape_html(r)}")

    lines.append(f"\n📰 {_escape_html(article.get('feed_name', ''))}")
    lines.append(f'🔗 <a href="{_escape_attr(article.get("url", ""))}">Đọc bài gốc</a>')

    return "\n".join(lines)


def _escape_html(text: str) -> str:
    """HTML escape for Telegram and email output."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&#39;")
    )


def _escape_attr(text: str) -> str:
    """Escape for HTML attribute context (href, etc.)."""
    return _escape_html(text).replace('"', '&quot;')


async def send_telegram(article: dict, analysis: dict) -> bool:
    """Send alert via Telegram Bot API."""
    if os.getenv("TELEGRAM_ENABLED", "false").lower() != "true":
        return False

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        logger.warning("Telegram credentials missing")
        return False

    text = format_telegram_message(article, analysis)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
            )
            if resp.status_code == 200:
                logger.info(f"Telegram alert sent: {article.get('title', '')[:50]}")
                return True
            logger.warning(f"Telegram error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")

    return False


async def send_slack(article: dict, analysis: dict) -> bool:
    """Send alert via Slack webhook."""
    if os.getenv("SLACK_ENABLED", "false").lower() != "true":
        return False

    webhook = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook:
        return False

    sev = analysis.get("severity", "info")
    score = analysis.get("relevance_score", 0)
    color = SEVERITY_COLOR.get(sev, "#6c757d")

    # Build Slack blocks
    header_text = (
        f"{SEVERITY_EMOJI.get(sev, '')} [{sev.upper()}] "
        f"Relevance {score}/10 — {article.get('title', '')[:120]}"
    )

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header_text[:150]},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Tóm tắt:* {analysis.get('summary_vi', '')}\n\n"
                       f"*Lý do liên quan:* _{analysis.get('relevance_reason', '')}_",
            },
        },
    ]

    # Fields row: products + CVEs + MITRE
    fields = []
    if analysis.get("affected_products"):
        fields.append({
            "type": "mrkdwn",
            "text": f"*Products:*\n{', '.join(analysis['affected_products'][:5])}",
        })
    if analysis.get("cve_ids"):
        fields.append({
            "type": "mrkdwn",
            "text": f"*CVE:*\n{', '.join(analysis['cve_ids'][:5])}",
        })
    if fields:
        blocks.append({"type": "section", "fields": fields})

    # Recommendations
    recs = analysis.get("recommendations", [])
    if recs:
        rec_text = "\n".join(f"• {r}" for r in recs[:3])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Khuyến nghị:*\n{rec_text}",
            },
        })

    # Context footer
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"📰 {article.get('feed_name', '')} | "
                       f"<{article.get('url', '')}|Đọc bài gốc>",
            }
        ],
    })

    payload = {"attachments": [{"color": color, "blocks": blocks}]}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook, json=payload)
            if resp.status_code == 200:
                logger.info(f"Slack alert sent: {article.get('title', '')[:50]}")
                return True
            logger.warning(f"Slack error {resp.status_code}")
    except Exception as e:
        logger.error(f"Slack send failed: {e}")

    return False


async def send_email_digest(articles_with_analysis: list[tuple[dict, dict]]) -> bool:
    """Send email digest with multiple articles (used for daily summary)."""
    if os.getenv("EMAIL_ENABLED", "false").lower() != "true":
        return False

    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    email_to = os.getenv("EMAIL_TO", "")

    if not all([smtp_user, smtp_pass, email_to]):
        return False

    # Sort by relevance score desc
    sorted_items = sorted(
        articles_with_analysis,
        key=lambda x: x[1].get("relevance_score", 0),
        reverse=True,
    )

    html_parts = [
        "<html><body style='font-family:Arial,sans-serif;max-width:700px;margin:0 auto'>",
        "<h2>🛡️ Daily Threat Intel Digest</h2>",
        f"<p>Tổng: {len(sorted_items)} bài có liên quan</p><hr>",
    ]

    for article, analysis in sorted_items[:30]:
        sev = analysis.get("severity", "info")
        color = SEVERITY_COLOR.get(sev, "#6c757d")
        score = analysis.get("relevance_score", 0)

        html_parts.append(
            f'<div style="border-left:4px solid {color};padding:10px 14px;margin:12px 0;background:#f8f9fa">'
            f'<div style="font-size:12px;color:#666">'
            f'<b>[{sev.upper()}]</b> Relevance: <b>{score}/10</b> · {article.get("feed_name", "")}'
            f'</div>'
            f'<h3 style="margin:4px 0"><a href="{_escape_attr(article.get("url", ""))}">{_escape_html(article.get("title", ""))}</a></h3>'
            f'<p style="margin:4px 0">{_escape_html(analysis.get("summary_vi", ""))}</p>'
            f'<p style="margin:4px 0;font-style:italic;color:#555">{_escape_html(analysis.get("relevance_reason", ""))}</p>'
            f'</div>'
        )

    html_parts.append("</body></html>")
    html_content = "\n".join(html_parts)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Threat Intel] Daily Digest - {len(sorted_items)} relevant articles"
    msg["From"] = smtp_user
    msg["To"] = email_to
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    def _blocking_send():
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

    try:
        await asyncio.to_thread(_blocking_send)
        logger.info(f"Email digest sent: {len(sorted_items)} articles")
        return True
    except Exception as e:
        logger.error(f"Email digest failed: {e}")
        return False


async def dispatch_alert(article: dict, analysis: dict) -> list[str]:
    """Send alert to all enabled channels in parallel. Returns list of channels that succeeded."""
    results = await asyncio.gather(
        send_telegram(article, analysis),
        send_slack(article, analysis),
    )
    channels = ["telegram", "slack"]
    return [ch for ch, ok in zip(channels, results) if ok]
