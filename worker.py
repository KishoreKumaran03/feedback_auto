import logging
import os
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from automation import FeedbackUnavailableError, run_feedback
from database import (
    claim_next_due_schedule,
    finish_feedback_schedule,
    initialize_database,
    load_saved_credentials_by_id,
)
from notification_delivery import notify_feedback_outcome


def _acquire_worker_lock():
    lock_path = Path(
        os.environ.get(
            "FEEDBACK_WORKER_LOCK_PATH",
            Path(__file__).with_name(".feedback_worker.lock"),
        )
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    try:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        if os.name == "nt":
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    return lock_file


def run_due_schedules_once() -> int:
    processed = 0
    while schedule := claim_next_due_schedule():
        schedule_id, credential_id, feedback_type, scheduled_at = schedule
        try:
            roll_number, password = load_saved_credentials_by_id(credential_id)
            run_feedback(feedback_type, roll_number, password)
        except FeedbackUnavailableError:
            finish_feedback_schedule(
                schedule_id, "failed", result="form_not_found"
            )
            logging.info(
                "Intermediate feedback form was not found for job %s.", schedule_id
            )
            notify_feedback_outcome(
                credential_id,
                "feedback_form_not_found",
                "Intermediate feedback form not found",
                f"No intermediate feedback form was available on "
                f"{scheduled_at}. Choose a valid availability date and schedule again.",
                feedback_type,
                schedule_id=schedule_id,
                scheduled_at=scheduled_at,
            )
        except Exception as error:
            finish_feedback_schedule(schedule_id, "failed", result="failed")
            logging.error(
                "Scheduled feedback job %s failed (%s): %s",
                schedule_id,
                type(error).__name__,
                str(error).replace("\n", " ")[:240],
            )
            notify_feedback_outcome(
                credential_id,
                "feedback_failed",
                "Feedback was not completed",
                "Automation could not complete this feedback. Check the selected feedback type and try again.",
                feedback_type,
                schedule_id=schedule_id,
                scheduled_at=scheduled_at,
            )
        else:
            finish_feedback_schedule(schedule_id, "completed")
            logging.info("Scheduled feedback job %s completed.", schedule_id)
            notify_feedback_outcome(
                credential_id,
                "feedback_completed",
                "Feedback completed",
                "Your feedback has been successfully completed and submitted.",
                feedback_type,
                schedule_id=schedule_id,
                scheduled_at=scheduled_at,
            )
        processed += 1
    return processed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    lock_file = _acquire_worker_lock()
    if lock_file is None:
        logging.warning("Another feedback worker is already running; this instance will exit.")
        return
    try:
        initialize_database()
        logging.info("Checking for feedback schedules due at 10:00 AM IST.")
        processed = run_due_schedules_once()
        logging.info("10:00 AM check finished; processed %s due job(s).", processed)
    finally:
        lock_file.close()


if __name__ == "__main__":
    main()
