import os


STREAMLIT_SECRET_NAMES = (
    "FEEDBACK_DATABASE_MODE",
    "FEEDBACK_DATABASE_URL",
    "FEEDBACK_ENCRYPTION_KEY",
    "FEEDBACK_SMTP_HOST",
    "FEEDBACK_SMTP_PORT",
    "FEEDBACK_SMTP_USERNAME",
    "FEEDBACK_SMTP_PASSWORD",
    "FEEDBACK_SMTP_FROM",
)


def load_streamlit_secrets(secrets) -> None:
    """Expose top-level Streamlit secrets to existing environment-based modules."""
    for name in STREAMLIT_SECRET_NAMES:
        if name in os.environ:
            continue
        try:
            value = secrets.get(name)
        except Exception:
            value = None
        if value is not None:
            os.environ[name] = str(value)
