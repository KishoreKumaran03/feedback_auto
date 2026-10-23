import logging

from database import (
    create_notification,
    get_account_email,
    set_notification_email_status,
)
from email_notification import send_feedback_email


def notify_feedback_outcome(
    credential_id: int,
    category: str,
    title: str,
    message: str,
    feedback_type: int,
    schedule_id: int | None = None,
    scheduled_at: str | None = None,
) -> int:
    notification_id = create_notification(
        credential_id,
        category,
        title,
        message,
        schedule_id=schedule_id,
        email_status="pending",
    )

    try:
        recipient = get_account_email(credential_id)
        if not recipient:
            set_notification_email_status(notification_id, credential_id, "not_configured")
            return notification_id
        send_feedback_email(recipient, category, feedback_type, scheduled_at)
    except Exception as error:
        set_notification_email_status(notification_id, credential_id, "failed")
        logging.warning(
            "Email for notification %s failed (%s).",
            notification_id,
            type(error).__name__,
        )
    else:
        set_notification_email_status(notification_id, credential_id, "sent")

    return notification_id
