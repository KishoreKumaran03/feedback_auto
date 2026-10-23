"""Copy an existing local SQLite database to the configured Postgres database."""

import os
import sqlite3
from pathlib import Path

import psycopg

from database import initialize_database


TABLE_COLUMNS = {
    "saved_credentials": (
        "id, roll_number_lookup, roll_number_encrypted, password_encrypted, "
        "email_encrypted, created_at, updated_at"
    ),
    "feedback_schedules": (
        "id, credential_id, feedback_type, scheduled_at, status, result, created_at"
    ),
    "notifications": (
        "id, credential_id, schedule_id, category, title, message, email_status, "
        "created_at, read_at"
    ),
    "scheduled_jobs": (
        "id, roll_number_encrypted, password_encrypted, feedback_type, scheduled_at, "
        "status, created_at"
    ),
}


def main() -> None:
    source_path = Path(os.environ.get("FEEDBACK_SOURCE_SQLITE_PATH", "feedback_jobs.sqlite3"))
    database_url = os.environ.get("FEEDBACK_DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("Set FEEDBACK_DATABASE_URL to the destination Postgres connection string.")
    if not os.environ.get("FEEDBACK_ENCRYPTION_KEY"):
        raise SystemExit("Set FEEDBACK_ENCRYPTION_KEY to the same key used by the local app.")
    if not source_path.is_file():
        raise SystemExit(f"SQLite source database was not found: {source_path}")

    initialize_database()
    source = sqlite3.connect(source_path)
    destination = psycopg.connect(database_url)
    try:
        with source, destination:
            scheduled_ids = {
                int(row[0])
                for row in source.execute("SELECT id FROM feedback_schedules").fetchall()
            }
            for table, columns in TABLE_COLUMNS.items():
                rows = source.execute(f"SELECT {columns} FROM {table}").fetchall()
                if table == "notifications":
                    rows = [
                        (*row[:2], row[2] if row[2] in scheduled_ids else None, *row[3:])
                        for row in rows
                    ]
                placeholders = ", ".join("%s" for _ in columns.split(","))
                destination.cursor().executemany(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
                    "ON CONFLICT (id) DO NOTHING",
                    rows,
                )

            for table in TABLE_COLUMNS:
                destination.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, 'id')::regclass, "
                    "GREATEST(COALESCE((SELECT MAX(id) FROM " + table + "), 1), 1), "
                    "EXISTS (SELECT 1 FROM " + table + "))",
                    (table,),
                )
        print("Local SQLite records copied to Postgres. Existing IDs are preserved.")
        print("Keep the same FEEDBACK_ENCRYPTION_KEY configured in Streamlit Cloud and GitHub Actions.")
    finally:
        source.close()
        destination.close()


if __name__ == "__main__":
    main()
