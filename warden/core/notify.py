"""Telegram notifications via urllib (no extra dependencies).

Sends a message per backup run with the appropriate status emoji.
If Telegram is not configured, all functions are no-ops.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from warden.core.config import TelegramConfig

# Telegram Bot API endpoint
API_BASE = "https://api.telegram.org/bot{token}/{method}"


def _send_message(
    bot_token: str, chat_id: str, text: str, *, timeout: int = 10
) -> tuple[bool, str]:
    """Send a message. Returns (success, error_or_response)."""
    url = API_BASE.format(token=bot_token, method="sendMessage")
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            return True, "ok"
        return False, f"Telegram error: {data.get('description', 'unknown')}"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:200]
        except Exception:
            pass
        return False, f"HTTP {e.code}: {body}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, f"Network error: {e}"


def send_notification(
    config: TelegramConfig,
    job_name: str,
    status: str,
    summary: str,
    details: str = "",
) -> tuple[bool, str]:
    """Send a Telegram notification for a backup run.

    Args:
        config: Telegram configuration (will no-op if not enabled)
        job_name: Job slug
        status: "success" / "partial" / "failure"
        summary: One-line summary (e.g. "3 destinos OK, 0 skipped")
        details: Optional extra details (errors, skipped reason)

    Returns:
        (success, message) — message is "disabled" if Telegram not configured.
    """
    if not config.enabled or not config.bot_token or not config.chat_id:
        return True, "disabled"

    # Check if this status should be notified
    if status == "success" and not config.notify_on_success:
        return True, "skipped (success notifications disabled)"
    if status == "partial" and not config.notify_on_partial:
        return True, "skipped (partial notifications disabled)"
    if status == "failure" and not config.notify_on_failure:
        return True, "skipped (failure notifications disabled)"

    # Build message
    emoji = {"success": "✅", "partial": "⚠️", "failure": "❌"}.get(status, "ℹ️")
    lines = [f"<b>{emoji} warden</b> • <code>{job_name}</code>"]
    lines.append(summary)
    if details:
        lines.append("")
        lines.append(f"<pre>{details}</pre>"[:3500])  # Telegram limit ~4096

    text = "\n".join(lines)
    return _send_message(config.bot_token, config.chat_id, text)


def send_test_message(config: TelegramConfig) -> tuple[bool, str]:
    """Send a test message to verify configuration."""
    if not config.bot_token or not config.chat_id:
        return False, "Bot token or chat ID is empty"
    text = (
        "<b>🔐 warden</b>\n"
        "<i>Test message — your Telegram configuration is working.</i>"
    )
    return _send_message(config.bot_token, config.chat_id, text)
