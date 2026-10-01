"""Send yourself a Telegram message when something finishes (optional)."""

from __future__ import annotations

from .config import Settings
from .errors import PublishError
from .models import PostResult, label_for
from .telegram_api import TelegramAPI

ICONS = {"published": "✅", "scheduled": "🗓", "draft": "📝", "handoff": "📲", "failed": "❌",
         "skipped": "⏭", "reported": "📊", "submitted": "⏳", "queued": "⏳", "pending": "⏳",
         "running": "⏳", "held": "📝"}


def enabled(settings: Settings) -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_owner_chat_id)


def send(settings: Settings, text: str, session=None) -> bool:
    """Best effort: returns False instead of raising when Telegram is unavailable."""
    if not enabled(settings):
        return False
    try:
        TelegramAPI(settings.telegram_bot_token, settings.telegram_api_base, session).send_message(
            settings.telegram_owner_chat_id, text)
        return True
    except PublishError:
        return False


def result_line(result: PostResult) -> str:
    icon = ICONS.get(result.status, "•")
    detail = result.url or result.error or (result.notes[0] if result.notes else result.status)
    return f"{icon} {label_for(result.platform, result.surface)}: {detail}"


def results_text(results: list[PostResult], header: str | None = None) -> str:
    lines = [header] if header else []
    lines += [result_line(r) for r in results]
    return "\n".join(lines)
