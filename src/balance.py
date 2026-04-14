"""AI Provider balance checking.

Checks remaining credits/balance for providers that expose a billing API.
Currently supported: DeepSeek.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


async def check_balance(provider: str) -> Optional[dict]:
    """Check balance for the given provider.

    Returns dict with:
        {
            "provider": str,
            "currency": str,
            "balance": float,
            "is_available": bool,
        }
    Or None if the provider doesn't support balance checking.
    """
    checkers = {
        "deepseek": _check_deepseek,
    }
    checker = checkers.get(provider)
    if not checker:
        return None
    try:
        return await checker()
    except Exception as e:
        logger.error(f"Balance check failed for {provider}: {e}")
        return None


async def _check_deepseek() -> dict:
    """Check DeepSeek balance via GET /user/balance."""
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    # Balance endpoint is at the root, not under /v1
    balance_url = base_url.replace("/v1", "") + "/user/balance"

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            balance_url,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()

    is_available = data.get("is_available", False)
    balance_infos = data.get("balance_infos", [])

    if not balance_infos:
        return {
            "provider": "deepseek",
            "currency": "USD",
            "balance": 0.0,
            "is_available": is_available,
        }

    info = balance_infos[0]
    return {
        "provider": "deepseek",
        "currency": info.get("currency", "CNY"),
        "balance": float(info.get("total_balance", 0)),
        "is_available": is_available,
    }


async def send_balance_alert(balance_info: dict) -> None:
    """Send low-balance alert via Telegram."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return

    currency = balance_info["currency"]
    balance = balance_info["balance"]
    provider = balance_info["provider"]
    threshold = float(os.getenv("BALANCE_ALERT_THRESHOLD", "2"))

    text = (
        f"⚠️ <b>[LOW BALANCE]</b> {provider.upper()}\n\n"
        f"💰 Remaining: <b>{balance:.2f} {currency}</b>\n"
        f"⚡ Threshold: {threshold:.2f} {currency}\n\n"
        f"Top up to avoid analysis interruptions."
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
            if resp.status_code == 200:
                logger.info(f"Balance alert sent: {balance:.2f} {currency}")
            else:
                logger.warning(f"Balance alert failed: {resp.status_code}")
    except Exception as e:
        logger.error(f"Balance alert send error: {e}")


async def check_and_alert(provider: str) -> Optional[dict]:
    """Check balance and send alert if below threshold.

    Returns balance info dict, or None if not supported.
    """
    info = await check_balance(provider)
    if not info:
        return None

    threshold = float(os.getenv("BALANCE_ALERT_THRESHOLD", "2"))
    if info["balance"] < threshold:
        logger.warning(
            f"Low balance: {info['balance']:.2f} {info['currency']} "
            f"(threshold: {threshold:.2f})"
        )
        await send_balance_alert(info)
    else:
        logger.info(
            f"Balance OK: {info['balance']:.2f} {info['currency']}"
        )

    return info
