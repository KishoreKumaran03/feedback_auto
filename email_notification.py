import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo


def _send_email(recipient: str, subject: str, body: str) -> None:
    host = os.environ.get("FEEDBACK_SMTP_HOST")
    username = os.environ.get("FEEDBACK_SMTP_USERNAME")
    password = os.environ.get("FEEDBACK_SMTP_PASSWORD")
    sender = os.environ.get("FEEDBACK_SMTP_FROM", "").strip() or username or ""
    port = int(os.environ.get("FEEDBACK_SMTP_PORT", "").strip() or "587")

    if not all((host, username, password, sender)):
        raise RuntimeError(
            "Configure FEEDBACK_SMTP_HOST, FEEDBACK_SMTP_USERNAME, "
            "FEEDBACK_SMTP_PASSWORD, and optionally FEEDBACK_SMTP_FROM."
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)

    if port == 465:
        with smtplib.SMTP_SSL(
            host, port, timeout=20, context=ssl.create_default_context()
        ) as server:
            server.login(username, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(username, password)
            server.send_message(message)


def send_feedback_email(
    recipient: str,
    category: str,
    feedback_type: int,
    scheduled_at: str | None = None,
) -> None:
    feedback_label = "End Semester" if feedback_type == 0 else "Intermediate"

    if category == "feedback_completed":
        _send_email(
            recipient,
            "Feedback successfully completed",
            f"Your {feedback_label} feedback has been successfully completed and submitted.",
        )
        return

    if category == "feedback_form_not_found":
        if scheduled_at:
            scheduled_date = datetime.fromisoformat(scheduled_at).astimezone(
                ZoneInfo("Asia/Kolkata")
            )
            timing = f"on {scheduled_date:%d %B %Y at %I:%M %p} IST"
        else:
            timing = "at this time"
        _send_email(
            recipient,
            "Intermediate feedback form not found",
            "The Intermediate Feedback form was not available in the portal "
            f"{timing}. No feedback was submitted.\n\n"
            "Please choose a valid feedback availability date and schedule again.",
        )
        return

    if category == "feedback_failed":
        _send_email(
            recipient,
            "Feedback was not completed",
            f"Your {feedback_label} feedback could not be completed. "
            "Please sign in to the app and try again.",
        )
        return

    raise ValueError(f"No email template exists for notification category: {category}")
