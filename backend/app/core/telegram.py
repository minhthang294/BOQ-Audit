import logging
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import get_settings

logger = logging.getLogger("boq-audit.telegram")


def notify_pdf_upload(job_code: str, project_name: str, filename: str, customer: str) -> None:
    settings = get_settings()
    token = settings.telegram_bot_token.strip()
    chat_id = settings.telegram_chat_id.strip()
    if not token or not chat_id:
        return

    message = (
        "New customer PDF upload\n"
        f"Job: {job_code}\n"
        f"Project: {project_name}\n"
        f"Customer: {customer}\n"
        f"File: {filename}"
    )
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=urlencode({"chat_id": chat_id, "text": message}).encode(),
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            if response.status != 200:
                logger.warning("telegram_upload_notification_failed job_code=%s status=%s", job_code, response.status)
    except Exception as exc:
        # urllib errors can contain the request URL and bot token. Never log the exception.
        logger.warning("telegram_upload_notification_failed job_code=%s error_type=%s", job_code, type(exc).__name__)
