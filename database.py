import os
import sqlite3
import hashlib
import hmac
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def _database_path() -> Path:
    return Path(os.environ.get("FEEDBACK_DATABASE_PATH", "feedback_jobs.sqlite3"))


class _PostgresConnection:
    """Small DB-API adapter so the same queries work with SQLite and Postgres."""

    def __init__(self, url: str):
        import psycopg

        self._psycopg = psycopg
        self._connection = psycopg.connect(url, connect_timeout=10)

    def execute(self, query: str, parameters=()):
        if query.strip().upper() == "BEGIN IMMEDIATE":
            query = "BEGIN"
        else:
            query = query.replace("?", "%s")
        try:
            return self._connection.execute(query, parameters)
        except self._psycopg.errors.UniqueViolation as error:
            raise sqlite3.IntegrityError(str(error)) from error

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        if exception_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()
        return False

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


def _connect(database_path: str | Path | None = None):
    if database_path is None:
        database_url = os.environ.get("FEEDBACK_DATABASE_URL", "").strip()
        database_mode = os.environ.get("FEEDBACK_DATABASE_MODE", "sqlite").lower()
        if database_url:
            if database_url.startswith("postgres://"):
                database_url = "postgresql://" + database_url.removeprefix("postgres://")
            return _PostgresConnection(database_url)
        if database_mode == "postgres":
            raise RuntimeError(
                "FEEDBACK_DATABASE_MODE is postgres, but FEEDBACK_DATABASE_URL is missing."
            )
        database_path = _database_path()
    return sqlite3.connect(database_path)


def _insert_id(connection, query: str, parameters=()) -> int:
    if isinstance(connection, _PostgresConnection):
        return int(connection.execute(query + " RETURNING id", parameters).fetchone()[0])
    return int(connection.execute(query, parameters).lastrowid)


def _uses_postgres(database_path: str | Path | None = None) -> bool:
    return database_path is None and bool(os.environ.get("FEEDBACK_DATABASE_URL", "").strip())


def _initialize_postgres() -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id BIGSERIAL PRIMARY KEY,
                roll_number_encrypted BYTEA NOT NULL,
                password_encrypted BYTEA NOT NULL,
                feedback_type INTEGER NOT NULL CHECK (feedback_type IN (0, 1)),
                scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'completed', 'failed')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_credentials (
                id BIGSERIAL PRIMARY KEY,
                roll_number_lookup TEXT NOT NULL UNIQUE,
                roll_number_encrypted BYTEA NOT NULL,
                password_encrypted BYTEA NOT NULL,
                email_encrypted BYTEA,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_schedules (
                id BIGSERIAL PRIMARY KEY,
                credential_id BIGINT NOT NULL REFERENCES saved_credentials(id),
                feedback_type INTEGER NOT NULL CHECK (feedback_type IN (0, 1)),
                scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'completed', 'failed')),
                result TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id BIGSERIAL PRIMARY KEY,
                credential_id BIGINT NOT NULL REFERENCES saved_credentials(id),
                schedule_id BIGINT REFERENCES feedback_schedules(id) ON DELETE SET NULL,
                category TEXT NOT NULL CHECK (
                    category IN (
                        'feedback_completed', 'feedback_failed',
                        'feedback_form_not_found', 'schedule_created'
                    )
                ),
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                email_status TEXT NOT NULL DEFAULT 'not_requested' CHECK (
                    email_status IN ('not_requested', 'pending', 'sent', 'failed', 'not_configured')
                ),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                read_at TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_schedules_due "
            "ON feedback_schedules(status, scheduled_at)"
        )
        connection.execute(
            "ALTER TABLE saved_credentials ADD COLUMN IF NOT EXISTS email_encrypted BYTEA"
        )
        connection.execute(
            "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS email_status TEXT "
            "NOT NULL DEFAULT 'not_configured'"
        )
        connection.execute(
            "ALTER TABLE feedback_schedules ADD COLUMN IF NOT EXISTS result TEXT "
            "NOT NULL DEFAULT 'pending'"
        )


def _cipher() -> Fernet:
    key = os.environ.get("FEEDBACK_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("Set FEEDBACK_ENCRYPTION_KEY to a Fernet key before storing credentials.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise RuntimeError("FEEDBACK_ENCRYPTION_KEY is not a valid Fernet key.") from error


def validate_encryption_key() -> None:
    _cipher()


def _roll_number_lookup(roll_number: str) -> str:
    key = os.environ.get("FEEDBACK_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("Set FEEDBACK_ENCRYPTION_KEY to a Fernet key before storing credentials.")
    return hmac.new(
        key.encode("ascii"),
        roll_number.strip().casefold().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def initialize_database(database_path: str | Path | None = None) -> None:
    if database_path is None and os.environ.get("FEEDBACK_DATABASE_URL", "").strip():
        _initialize_postgres()
        return
    if database_path is None and os.environ.get("FEEDBACK_DATABASE_MODE", "sqlite").lower() == "postgres":
        _connect()  # Raise a clear configuration error before creating a local database.
    path = Path(database_path) if database_path is not None else _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    compact_database = False

    with closing(_connect(path)) as connection, connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roll_number_encrypted BLOB NOT NULL,
                password_encrypted BLOB NOT NULL,
                feedback_type INTEGER NOT NULL CHECK (feedback_type IN (0, 1)),
                scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'completed', 'failed')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roll_number_lookup TEXT NOT NULL UNIQUE,
                roll_number_encrypted BLOB NOT NULL,
                password_encrypted BLOB NOT NULL,
                email_encrypted BLOB,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                credential_id INTEGER NOT NULL,
                feedback_type INTEGER NOT NULL CHECK (feedback_type IN (0, 1)),
                scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'completed', 'failed')),
                result TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (credential_id) REFERENCES saved_credentials(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                credential_id INTEGER NOT NULL,
                schedule_id INTEGER,
                category TEXT NOT NULL CHECK (
                    category IN (
                        'feedback_completed',
                        'feedback_failed',
                        'feedback_form_not_found',
                        'schedule_created'
                    )
                ),
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                email_status TEXT NOT NULL DEFAULT 'not_requested' CHECK (
                    email_status IN ('not_requested', 'pending', 'sent', 'failed', 'not_configured')
                ),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                read_at TEXT,
                FOREIGN KEY (credential_id) REFERENCES saved_credentials(id),
                FOREIGN KEY (schedule_id) REFERENCES feedback_schedules(id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feedback_schedules_due
            ON feedback_schedules(status, scheduled_at)
            """
        )
        credential_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(saved_credentials)")
        }
        if "email_encrypted" not in credential_columns:
            connection.execute(
                "ALTER TABLE saved_credentials ADD COLUMN email_encrypted BLOB"
            )
        notification_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(notifications)")
        }
        if "email_status" not in notification_columns:
            connection.execute(
                """
                ALTER TABLE notifications ADD COLUMN email_status TEXT NOT NULL
                DEFAULT 'not_configured'
                CHECK (email_status IN ('not_requested', 'pending', 'sent', 'failed', 'not_configured'))
                """
            )
        schedule_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(feedback_schedules)")
        }
        if "notification_status" in schedule_columns:
            connection.execute(
                "ALTER TABLE feedback_schedules DROP COLUMN notification_status"
            )
            compact_database = True
        if "result" not in schedule_columns:
            connection.execute(
                "ALTER TABLE feedback_schedules ADD COLUMN result TEXT NOT NULL DEFAULT 'pending'"
            )
    if compact_database:
        with closing(_connect(path)) as connection:
            connection.execute("VACUUM")


def save_credentials(
    roll_number: str,
    password: str,
    database_path: str | Path | None = None,
    email: str | None = None,
) -> int:
    normalized_roll_number = roll_number.strip()
    if not normalized_roll_number or not password:
        raise ValueError("Roll number and password are required.")

    cipher = _cipher()
    roll_number_lookup = _roll_number_lookup(normalized_roll_number)
    encrypted_roll_number = cipher.encrypt(normalized_roll_number.encode("utf-8"))
    encrypted_password = cipher.encrypt(password.encode("utf-8"))
    encrypted_email = _encrypt_email(email) if email else None
    path = database_path
    initialize_database(path)

    try:
        with closing(_connect(path)) as connection, connection:
            return _insert_id(
                connection,
                """
                INSERT INTO saved_credentials (
                    roll_number_lookup, roll_number_encrypted, password_encrypted,
                    email_encrypted
                ) VALUES (?, ?, ?, ?)
                """,
                (roll_number_lookup, encrypted_roll_number, encrypted_password, encrypted_email),
            )
    except sqlite3.IntegrityError as error:
        raise ValueError("A profile already exists for this roll number. Sign in instead.") from error


def _encrypt_email(email: str) -> bytes:
    normalized_email = email.strip()
    if parseaddr(normalized_email)[1] != normalized_email or "@" not in normalized_email:
        raise ValueError("Enter a valid email address.")
    return _cipher().encrypt(normalized_email.encode("utf-8"))


def get_account_email(
    credential_id: int, database_path: str | Path | None = None
) -> str | None:
    path = database_path
    with closing(_connect(path)) as connection:
        row = connection.execute(
            "SELECT email_encrypted FROM saved_credentials WHERE id = ?",
            (credential_id,),
        ).fetchone()
    if row is None:
        raise LookupError("Saved account was not found.")
    if row[0] is None:
        return None
    try:
        return _cipher().decrypt(bytes(row[0])).decode("utf-8")
    except InvalidToken as error:
        raise RuntimeError("Could not decrypt email; check the encryption key.") from error


def save_account_email(
    credential_id: int, email: str, database_path: str | Path | None = None
) -> None:
    encrypted_email = _encrypt_email(email)
    path = database_path
    with closing(_connect(path)) as connection, connection:
        cursor = connection.execute(
            "UPDATE saved_credentials SET email_encrypted = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (encrypted_email, credential_id),
        )
        if cursor.rowcount != 1:
            raise LookupError("Saved account was not found.")



def authenticate_saved_account(
    roll_number: str,
    password: str,
    database_path: str | Path | None = None,
) -> tuple[int, str] | None:
    cipher = _cipher()
    roll_number_lookup = _roll_number_lookup(roll_number)
    path = database_path

    with closing(_connect(path)) as connection:
        row = connection.execute(
            """
            SELECT id, roll_number_encrypted, password_encrypted
            FROM saved_credentials
            WHERE roll_number_lookup = ?
            """,
            (roll_number_lookup,),
        ).fetchone()

    if row is None:
        return None

    try:
        saved_roll_number = cipher.decrypt(bytes(row[1])).decode("utf-8")
        saved_password = cipher.decrypt(bytes(row[2])).decode("utf-8")
    except InvalidToken as error:
        raise RuntimeError("Could not decrypt credentials; check the encryption key.") from error
    if not hmac.compare_digest(saved_password, password):
        return None
    return int(row[0]), saved_roll_number


def load_saved_credentials_by_id(
    credential_id: int, database_path: str | Path | None = None
) -> tuple[str, str]:
    cipher = _cipher()
    path = database_path

    with closing(_connect(path)) as connection:
        row = connection.execute(
            """
            SELECT roll_number_encrypted, password_encrypted
            FROM saved_credentials
            WHERE id = ?
            """,
            (credential_id,),
        ).fetchone()

    if row is None:
        raise LookupError("Saved credentials were not found.")
    try:
        return (
            cipher.decrypt(bytes(row[0])).decode("utf-8"),
            cipher.decrypt(bytes(row[1])).decode("utf-8"),
        )
    except InvalidToken as error:
        raise RuntimeError("Could not decrypt credentials; check the encryption key.") from error


def create_feedback_schedule(
    credential_id: int,
    feedback_type: int,
    scheduled_at: datetime,
    database_path: str | Path | None = None,
) -> int:
    if feedback_type not in (0, 1):
        raise ValueError("feedback_type must be 0 (end-semester) or 1 (intermediate).")
    if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
        raise ValueError("scheduled_at must include a timezone.")
    if scheduled_at <= datetime.now(scheduled_at.tzinfo):
        raise ValueError("Choose a future date and time.")

    path = database_path
    initialize_database(path)
    with closing(_connect(path)) as connection, connection:
        return _insert_id(
            connection,
            """
            INSERT INTO feedback_schedules (credential_id, feedback_type, scheduled_at)
            VALUES (?, ?, ?)
            """,
            (
                credential_id,
                feedback_type,
                scheduled_at.astimezone(timezone.utc).isoformat(),
            ),
        )


def claim_next_due_schedule(
    now: datetime | None = None, database_path: str | Path | None = None
) -> tuple[int, int, int, str] | None:
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("now must include a timezone.")
    path = database_path

    with closing(_connect(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        select_query = """
            SELECT id, credential_id, feedback_type, scheduled_at
            FROM feedback_schedules
            WHERE status = 'pending' AND scheduled_at <= ?
            ORDER BY scheduled_at, id
            LIMIT 1
            """
        if _uses_postgres(path):
            select_query += " FOR UPDATE SKIP LOCKED"
        row = connection.execute(
            select_query,
            (current_time.astimezone(timezone.utc).isoformat(),),
        ).fetchone()
        if row is None:
            connection.rollback()
            return None

        connection.execute(
            "UPDATE feedback_schedules SET status = 'running' WHERE id = ?",
            (row[0],),
        )
        connection.commit()
        return int(row[0]), int(row[1]), int(row[2]), str(row[3])


def finish_feedback_schedule(
    schedule_id: int,
    status: str,
    database_path: str | Path | None = None,
    result: str | None = None,
) -> None:
    if status not in ("completed", "failed"):
        raise ValueError("status must be 'completed' or 'failed'.")
    path = database_path
    with closing(_connect(path)) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE feedback_schedules
            SET status = ?, result = ?
            WHERE id = ? AND status = 'running'
            """,
            (status, result or ("submitted" if status == "completed" else "failed"), schedule_id),
        )
        if cursor.rowcount != 1:
            raise LookupError(f"Running schedule {schedule_id} was not found.")


def create_notification(
    credential_id: int,
    category: str,
    title: str,
    message: str,
    schedule_id: int | None = None,
    database_path: str | Path | None = None,
    email_status: str = "not_requested",
) -> int:
    valid_categories = {
        "feedback_completed",
        "feedback_failed",
        "feedback_form_not_found",
        "schedule_created",
    }
    if category not in valid_categories:
        raise ValueError("Unsupported notification category.")
    if email_status not in ("not_requested", "pending", "sent", "failed", "not_configured"):
        raise ValueError("Unsupported email notification status.")
    path = database_path
    with closing(_connect(path)) as connection, connection:
        return _insert_id(
            connection,
            """
            INSERT INTO notifications (
                credential_id, schedule_id, category, title, message, email_status
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (credential_id, schedule_id, category, title, message, email_status),
        )


def set_notification_email_status(
    notification_id: int,
    credential_id: int,
    status: str,
    database_path: str | Path | None = None,
) -> None:
    if status not in ("pending", "sent", "failed", "not_configured"):
        raise ValueError("Unsupported email notification status.")
    path = database_path
    with closing(_connect(path)) as connection, connection:
        cursor = connection.execute(
            "UPDATE notifications SET email_status = ? WHERE id = ? AND credential_id = ?",
            (status, notification_id, credential_id),
        )
        if cursor.rowcount != 1:
            raise LookupError("Notification was not found for this account.")


def list_notifications(
    credential_id: int,
    limit: int = 30,
    database_path: str | Path | None = None,
) -> list[tuple[int, str, str, str, str, str | None, str]]:
    path = database_path
    with closing(_connect(path)) as connection:
        rows = connection.execute(
            """
            SELECT id, category, title, message, created_at, read_at, email_status
            FROM notifications
            WHERE credential_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (credential_id, limit),
        ).fetchall()
    return [
        (
            int(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5]) if row[5] is not None else None,
            str(row[6]),
        )
        for row in rows
    ]


def count_unread_notifications(
    credential_id: int, database_path: str | Path | None = None
) -> int:
    path = database_path
    with closing(_connect(path)) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM notifications WHERE credential_id = ? AND read_at IS NULL",
            (credential_id,),
        ).fetchone()
    return int(row[0])


def mark_notifications_read(
    credential_id: int, database_path: str | Path | None = None
) -> int:
    path = database_path
    read_at = datetime.now(timezone.utc).isoformat()
    with closing(_connect(path)) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE notifications SET read_at = ?
            WHERE credential_id = ? AND read_at IS NULL
            """,
            (read_at, credential_id),
        )
        return cursor.rowcount


def list_feedback_schedules(
    credential_id: int,
    limit: int = 20,
    database_path: str | Path | None = None,
) -> list[tuple[int, int, str, str, str]]:
    path = database_path
    with closing(_connect(path)) as connection:
        rows = connection.execute(
            """
            SELECT id, feedback_type, scheduled_at, status, result
            FROM feedback_schedules
            WHERE credential_id = ?
            ORDER BY scheduled_at DESC, id DESC
            LIMIT ?
            """,
            (credential_id, limit),
        ).fetchall()
    return [
        (int(row[0]), int(row[1]), str(row[2]), str(row[3]), str(row[4]))
        for row in rows
    ]


def save_scheduled_job(
    roll_number: str,
    password: str,
    feedback_type: int,
    scheduled_at: datetime,
    database_path: str | Path | None = None,
) -> int:
    if feedback_type not in (0, 1):
        raise ValueError("feedback_type must be 0 (end-semester) or 1 (intermediate).")
    if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
        raise ValueError("scheduled_at must include a timezone.")

    cipher = _cipher()
    encrypted_roll_number = cipher.encrypt(roll_number.encode("utf-8"))
    encrypted_password = cipher.encrypt(password.encode("utf-8"))
    scheduled_at_utc = scheduled_at.astimezone(timezone.utc).isoformat()
    path = database_path
    initialize_database(path)

    with closing(_connect(path)) as connection, connection:
        return _insert_id(
            connection,
            """
            INSERT INTO scheduled_jobs (
                roll_number_encrypted, password_encrypted, feedback_type, scheduled_at
            ) VALUES (?, ?, ?, ?)
            """,
            (encrypted_roll_number, encrypted_password, feedback_type, scheduled_at_utc),
        )


def load_job_credentials(
    job_id: int, database_path: str | Path | None = None
) -> tuple[str, str]:
    cipher = _cipher()
    path = database_path

    with closing(_connect(path)) as connection:
        row = connection.execute(
            """
            SELECT roll_number_encrypted, password_encrypted
            FROM scheduled_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()

    if row is None:
        raise LookupError(f"Scheduled job {job_id} was not found.")

    try:
        roll_number = cipher.decrypt(bytes(row[0])).decode("utf-8")
        password = cipher.decrypt(bytes(row[1])).decode("utf-8")
    except InvalidToken as error:
        raise RuntimeError("Could not decrypt credentials; check the encryption key.") from error
    return roll_number, password
