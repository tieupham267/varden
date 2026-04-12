"""Tests for src/notifier.py — formatting, escaping, dispatch."""

from unittest.mock import AsyncMock, MagicMock, patch

from src.notifier import (
    _escape_attr,
    _escape_html,
    dispatch_alert,
    format_telegram_message,
    send_email_digest,
    send_slack,
    send_telegram,
)


# ── Escaping ────────────────────────────────────────────────────────


class TestEscaping:
    def test_escape_html_entities(self):
        assert _escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"

    def test_escape_html_passthrough(self):
        assert _escape_html("normal text") == "normal text"

    def test_escape_html_single_quotes(self):
        assert _escape_html("it's a test") == "it&#39;s a test"

    def test_escape_attr_quotes(self):
        assert _escape_attr('a"b') == "a&quot;b"

    def test_escape_attr_ampersand_in_url(self):
        assert _escape_attr("https://ex.com?a=1&b=2") == "https://ex.com?a=1&amp;b=2"

    def test_escape_attr_single_quotes(self):
        assert _escape_attr("it's") == "it&#39;s"

    def test_escape_attr_combined(self):
        assert _escape_attr('<"&>') == "&lt;&quot;&amp;&gt;"


# ── Telegram formatting ────────────────────────────────────────────


class TestTelegramFormat:
    def test_contains_severity_and_score(self, sample_article, sample_analysis):
        msg = format_telegram_message(sample_article, sample_analysis)
        assert "CRITICAL" in msg
        assert "9/10" in msg

    def test_score_bar(self, sample_article, sample_analysis):
        msg = format_telegram_message(sample_article, sample_analysis)
        assert "\u25cf" * 9 + "\u25cb" * 1 in msg  # 9 filled, 1 empty

    def test_includes_cves(self, sample_article, sample_analysis):
        msg = format_telegram_message(sample_article, sample_analysis)
        assert "CVE-2024-12345" in msg

    def test_includes_products(self, sample_article, sample_analysis):
        msg = format_telegram_message(sample_article, sample_analysis)
        assert "VMware ESXi" in msg

    def test_includes_mitre(self, sample_article, sample_analysis):
        msg = format_telegram_message(sample_article, sample_analysis)
        assert "T1190" in msg

    def test_includes_recommendations(self, sample_article, sample_analysis):
        msg = format_telegram_message(sample_article, sample_analysis)
        assert "Patch VMware ESXi" in msg

    def test_url_escaped_in_href(self):
        article = {"title": "T", "url": "https://ex.com?a=1&b=2", "feed_name": "F"}
        analysis = {"severity": "info", "relevance_score": 1, "summary_vi": "s"}
        msg = format_telegram_message(article, analysis)
        assert "a=1&amp;b=2" in msg

    def test_minimal_analysis(self, sample_article):
        analysis = {"severity": "info", "relevance_score": 0, "summary_vi": "Nothing"}
        msg = format_telegram_message(sample_article, analysis)
        assert "INFO" in msg
        assert "0/10" in msg


# ── Send functions (enabled/disabled) ───────────────────────────────


class TestSendTelegram:
    async def test_disabled(self):
        with patch.dict("os.environ", {"TELEGRAM_ENABLED": "false"}):
            assert await send_telegram({}, {}) is False

    async def test_missing_token(self):
        env = {"TELEGRAM_ENABLED": "true", "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "123"}
        with patch.dict("os.environ", env, clear=False):
            assert await send_telegram({}, {"severity": "info"}) is False

    async def test_missing_chat_id(self):
        env = {"TELEGRAM_ENABLED": "true", "TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": ""}
        with patch.dict("os.environ", env, clear=False):
            assert await send_telegram({}, {"severity": "info"}) is False


class TestSendTelegramHTTP:
    """Tests for the actual Telegram HTTP call paths."""

    def _env(self):
        return {
            "TELEGRAM_ENABLED": "true",
            "TELEGRAM_BOT_TOKEN": "tok",
            "TELEGRAM_CHAT_ID": "123",
        }

    async def test_success(self, sample_article, sample_analysis):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.dict("os.environ", self._env(), clear=False), \
             patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            cls.return_value.__aexit__ = AsyncMock(return_value=False)
            assert await send_telegram(sample_article, sample_analysis) is True

        body = mock_client.post.call_args.kwargs["json"]
        assert body["chat_id"] == "123"
        assert body["parse_mode"] == "HTML"

    async def test_api_error(self, sample_article, sample_analysis):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.dict("os.environ", self._env(), clear=False), \
             patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            cls.return_value.__aexit__ = AsyncMock(return_value=False)
            assert await send_telegram(sample_article, sample_analysis) is False

    async def test_network_error(self, sample_article, sample_analysis):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("timeout"))

        with patch.dict("os.environ", self._env(), clear=False), \
             patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            cls.return_value.__aexit__ = AsyncMock(return_value=False)
            assert await send_telegram(sample_article, sample_analysis) is False


class TestSendSlack:
    async def test_disabled(self):
        with patch.dict("os.environ", {"SLACK_ENABLED": "false"}):
            assert await send_slack({}, {}) is False

    async def test_no_webhook(self):
        with patch.dict("os.environ", {"SLACK_ENABLED": "true", "SLACK_WEBHOOK_URL": ""}):
            assert await send_slack({}, {}) is False

    async def test_success(self, sample_article, sample_analysis):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        env = {"SLACK_ENABLED": "true", "SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"}
        with patch.dict("os.environ", env, clear=False), \
             patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            cls.return_value.__aexit__ = AsyncMock(return_value=False)
            assert await send_slack(sample_article, sample_analysis) is True

        payload = mock_client.post.call_args.kwargs["json"]
        assert "attachments" in payload
        assert payload["attachments"][0]["color"] == "#dc3545"  # critical

    async def test_api_error(self, sample_article, sample_analysis):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        env = {"SLACK_ENABLED": "true", "SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"}
        with patch.dict("os.environ", env, clear=False), \
             patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            cls.return_value.__aexit__ = AsyncMock(return_value=False)
            assert await send_slack(sample_article, sample_analysis) is False

    async def test_network_error(self, sample_article, sample_analysis):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("down"))

        env = {"SLACK_ENABLED": "true", "SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"}
        with patch.dict("os.environ", env, clear=False), \
             patch("httpx.AsyncClient") as cls:
            cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            cls.return_value.__aexit__ = AsyncMock(return_value=False)
            assert await send_slack(sample_article, sample_analysis) is False


class TestEmailDigest:
    async def test_disabled(self):
        with patch.dict("os.environ", {"EMAIL_ENABLED": "false"}):
            assert await send_email_digest([]) is False

    async def test_missing_smtp_creds(self):
        env = {"EMAIL_ENABLED": "true", "SMTP_USER": "", "SMTP_PASSWORD": "", "EMAIL_TO": ""}
        with patch.dict("os.environ", env):
            assert await send_email_digest([({}, {})]) is False

    async def test_success(self, sample_article, sample_analysis):
        env = {
            "EMAIL_ENABLED": "true",
            "SMTP_HOST": "smtp.test.com",
            "SMTP_PORT": "587",
            "SMTP_USER": "user@test.com",
            "SMTP_PASSWORD": "pass",
            "EMAIL_TO": "team@test.com",
        }
        items = [
            (sample_article, sample_analysis),
            (
                {"title": "Low", "url": "https://ex.com/2", "feed_name": "Feed"},
                {"severity": "low", "relevance_score": 2, "summary_vi": "Meh", "relevance_reason": "not relevant"},
            ),
        ]

        with patch.dict("os.environ", env), \
             patch("src.notifier.asyncio.to_thread", new_callable=AsyncMock):
            result = await send_email_digest(items)

        assert result is True

    async def test_smtp_failure(self, sample_article, sample_analysis):
        env = {
            "EMAIL_ENABLED": "true",
            "SMTP_HOST": "smtp.test.com",
            "SMTP_PORT": "587",
            "SMTP_USER": "user@test.com",
            "SMTP_PASSWORD": "pass",
            "EMAIL_TO": "team@test.com",
        }
        items = [(sample_article, sample_analysis)]

        with patch.dict("os.environ", env), \
             patch("src.notifier.asyncio.to_thread", new_callable=AsyncMock, side_effect=Exception("SMTP fail")):
            result = await send_email_digest(items)

        assert result is False


# ── dispatch_alert ──────────────────────────────────────────────────


class TestDispatchAlert:
    async def test_both_succeed(self, sample_article, sample_analysis):
        with patch("src.notifier.send_telegram", new_callable=AsyncMock, return_value=True), \
             patch("src.notifier.send_slack", new_callable=AsyncMock, return_value=True):
            channels = await dispatch_alert(sample_article, sample_analysis)
        assert set(channels) == {"telegram", "slack"}

    async def test_partial_failure(self, sample_article, sample_analysis):
        with patch("src.notifier.send_telegram", new_callable=AsyncMock, return_value=True), \
             patch("src.notifier.send_slack", new_callable=AsyncMock, return_value=False):
            channels = await dispatch_alert(sample_article, sample_analysis)
        assert channels == ["telegram"]

    async def test_all_fail(self, sample_article, sample_analysis):
        with patch("src.notifier.send_telegram", new_callable=AsyncMock, return_value=False), \
             patch("src.notifier.send_slack", new_callable=AsyncMock, return_value=False):
            assert await dispatch_alert(sample_article, sample_analysis) == []
