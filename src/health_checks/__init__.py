"""Healthcheck subsystem for Varden.

Validates environment configuration and live connectivity to external
services (AI provider, Telegram, Slack, SMTP, Oksskolten SQLite).

Entry points:
    - :func:`src.healthcheck.run_healthcheck` orchestrator
    - ``python main.py healthcheck`` CLI
"""
