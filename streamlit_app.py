import streamlit as st
from datetime import datetime, time as datetime_time, timedelta
from zoneinfo import ZoneInfo

from automation import FeedbackUnavailableError, run_feedback
from database import (
    authenticate_saved_account,
    count_unread_notifications,
    create_feedback_schedule,
    create_notification,
    get_account_email,
    initialize_database,
    list_feedback_schedules,
    list_notifications,
    load_saved_credentials_by_id,
    mark_notifications_read,
    save_account_email,
    save_credentials,
    validate_encryption_key,
)
from notification_delivery import notify_feedback_outcome
from settings import load_streamlit_secrets


LOCAL_TIMEZONE = ZoneInfo("Asia/Kolkata")
st.set_page_config(page_title="Feedback Automation", page_icon="📝", layout="centered")


@st.cache_resource
def _initialize_storage() -> None:
    validate_encryption_key()
    initialize_database()

try:
    app_secrets = st.secrets
except Exception:
    app_secrets = {}
load_streamlit_secrets(app_secrets)
try:
    _initialize_storage()
except RuntimeError as error:
    st.error(f"Configuration error: {error}")
    st.stop()
except Exception:
    st.error("Could not connect to the configured database. Check its URL and availability.")
    st.stop()


def _sign_in() -> None:
    roll_number = st.session_state.get("login_roll_number", "").strip()
    password = st.session_state.pop("login_password", "")
    account = authenticate_saved_account(roll_number, password)
    if account is None:
        st.session_state["auth_error"] = "Roll number or password was not recognized."
        return
    st.session_state["credential_id"], st.session_state["roll_number"] = account
    st.session_state.pop("auth_error", None)


def _create_profile() -> None:
    roll_number = st.session_state.get("new_roll_number", "").strip()
    password = st.session_state.pop("new_password", "")
    email = st.session_state.get("new_email", "").strip() or None
    if not st.session_state.get("store_consent", False):
        st.session_state["auth_error"] = "Consent is required to save encrypted credentials."
        return
    try:
        credential_id = save_credentials(roll_number, password, email=email)
    except Exception as error:
        st.session_state["auth_error"] = str(error)
        return
    st.session_state["credential_id"] = credential_id
    st.session_state["roll_number"] = roll_number
    st.session_state.pop("auth_error", None)


if "credential_id" not in st.session_state:
    st.title("📝 Feedback Automation")
    st.caption("Sign in to view your feedback status and notifications.")
    with st.form("sign_in_form"):
        st.text_input("Roll Number", placeholder="e.g., 23z309", key="login_roll_number")
        st.text_input("Password", type="password", key="login_password")
        st.form_submit_button("Sign in", type="primary", on_click=_sign_in)

    with st.expander("Create a local profile"):
        st.caption("Your roll number and password are encrypted and used for eCampus automation.")
        with st.form("create_profile_form"):
            st.text_input("Roll Number", key="new_roll_number")
            st.text_input("Password", type="password", key="new_password")
            st.text_input(
                "Email notifications (optional)",
                placeholder="student@example.edu",
                key="new_email",
            )
            st.checkbox(
                "I consent to saving these credentials and optional email encrypted.",
                key="store_consent",
            )
            st.form_submit_button("Create profile", on_click=_create_profile)

    if error_message := st.session_state.get("auth_error"):
        st.error(error_message)
    st.stop()


credential_id = st.session_state["credential_id"]
roll_number = st.session_state["roll_number"]
account_email = get_account_email(credential_id)
notifications = list_notifications(credential_id)
unread_count = count_unread_notifications(credential_id)
feedback_notifications = [
    notification
    for notification in notifications
    if notification[1]
    in {"feedback_completed", "feedback_failed", "feedback_form_not_found"}
]

status_columns = st.columns([6, 1, 1])
with status_columns[0]:
    with st.container(border=True):
        if feedback_notifications:
            latest = feedback_notifications[0]
            if latest[1] == "feedback_completed":
                st.success("Your feedback has been successfully completed.")
            elif latest[1] == "feedback_form_not_found":
                st.warning("Feedback was not completed: no intermediate form was found.")
            elif latest[1] == "feedback_failed":
                st.error("Your feedback has not been successfully completed.")
            else:
                st.info(latest[3])
        else:
            st.info("No feedback activity yet.")

with status_columns[1]:
    with st.popover(f"🔔 {unread_count}"):
        st.subheader("Notifications")
        if notifications:
            for _, _, title, message, created_at, read_at, email_status in notifications:
                st.markdown(f"**{title}**")
                st.write(message)
                email_label = {
                    "sent": "Email sent",
                    "failed": "Email failed",
                    "not_configured": "Email not configured",
                    "pending": "Email pending",
                    "not_requested": "In-app only",
                }[email_status]
                st.caption(
                    f"{created_at} UTC · {email_label}"
                    + (" · Read" if read_at else " · New")
                )
                st.divider()
        else:
            st.caption("No notifications yet.")
        if unread_count and st.button("Mark all as read", key="mark_notifications_read"):
            mark_notifications_read(credential_id)
            st.rerun()

with status_columns[2]:
    with st.popover(f"👤 {roll_number}"):
        st.caption("Signed in as")
        st.markdown(f"**{roll_number}**")
        with st.form(f"email_settings_{credential_id}"):
            email_value = st.text_input(
                "Email notifications",
                value=account_email or "",
                placeholder="student@example.edu",
            )
            if st.form_submit_button("Save email"):
                try:
                    save_account_email(credential_id, email_value)
                except Exception as error:
                    st.error(str(error))
                else:
                    st.success("Email address saved encrypted.")
        if st.button("Sign out", key="sign_out"):
            st.session_state.pop("credential_id", None)
            st.session_state.pop("roll_number", None)
            st.rerun()

st.title("Feedback Automation")
feedback_type = st.selectbox(
    "Feedback Type",
    options=[("End Semester Feedback", 0), ("Intermediate Feedback", 1)],
    format_func=lambda option: option[0],
)

if st.button("🚀 Submit Feedback", type="primary"):
    roll_number, password = load_saved_credentials_by_id(credential_id)
    try:
        run_feedback(feedback_type[1], roll_number, password)
    except FeedbackUnavailableError:
        notify_feedback_outcome(
            credential_id,
            "feedback_form_not_found",
            "Intermediate feedback form not found",
            "No intermediate feedback form was available. Choose a valid availability date and schedule again.",
            feedback_type[1],
        )
    except Exception:
        notify_feedback_outcome(
            credential_id,
            "feedback_failed",
            "Feedback was not completed",
            "Automation could not complete this feedback. Check the feedback type and try again.",
            feedback_type[1],
        )
    else:
        notify_feedback_outcome(
            credential_id,
            "feedback_completed",
            "Feedback completed",
            "Your feedback has been successfully completed and submitted.",
            feedback_type[1],
        )
    st.rerun()

st.divider()
st.subheader("Schedule Feedback")
schedule_date = st.date_input(
    "Feedback availability date (runs at 10:00 AM IST)",
    value=datetime.now(LOCAL_TIMEZONE).date() + timedelta(days=1),
    min_value=datetime.now(LOCAL_TIMEZONE).date(),
)
scheduled_at = datetime.combine(schedule_date, datetime_time(10, 0), tzinfo=LOCAL_TIMEZONE)

if st.button("🗓️ Schedule Feedback"):
    try:
        schedule_id = create_feedback_schedule(
            credential_id, feedback_type[1], scheduled_at
        )
    except ValueError as error:
        st.error(str(error))
    else:
        create_notification(
            credential_id,
            "schedule_created",
            "Feedback scheduled",
            f"Your feedback is scheduled for {scheduled_at:%d %B %Y at %I:%M %p} IST.",
            schedule_id,
        )
        st.rerun()

schedule_rows = list_feedback_schedules(credential_id)
if schedule_rows:
    st.dataframe(
        [
            {
                "Job": job_id,
                "Feedback": "End Semester" if kind == 0 else "Intermediate",
                "Scheduled (IST)": datetime.fromisoformat(scheduled_at_utc)
                .astimezone(LOCAL_TIMEZONE)
                .strftime("%Y-%m-%d %H:%M"),
                "Status": status,
                "Result": result,
            }
            for job_id, kind, scheduled_at_utc, status, result in schedule_rows
        ],
        hide_index=True,
        width="stretch",
    )
