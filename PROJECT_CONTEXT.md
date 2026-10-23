# Feedback automation: project context

This file records how the current project works so later work can continue here
or use it as a reference when building a similar automation. Confirm details
against the code and local machine because schedules, portal markup, and settings
can change.

## What the app does

- `streamlit_app.py` is the Streamlit entry point. Students create or sign into a
  local profile, choose End Semester or Intermediate feedback, submit manually,
  or schedule a run for a date.
- The two feedback type values are `0` for End Semester and `1` for Intermediate.
- The app uses `Asia/Kolkata` for displayed and selected schedule times. New
  schedules are set to 10:00 AM IST; the schedule date defaults to tomorrow.
- Scheduled times are stored in UTC. The schedule table converts them back to
  IST for display.
- Local development uses SQLite (`feedback_jobs.sqlite3` by default;
  `FEEDBACK_DATABASE_PATH` can override the path). Streamlit Community Cloud
  uses Supabase Postgres through `FEEDBACK_DATABASE_URL` and
  `FEEDBACK_DATABASE_MODE=postgres`; its tables and indexes initialize at app
  startup. `database.py` retains the SQLite backend for the existing local app.
- In Postgres mode, job claiming uses row locks with `SKIP LOCKED`; SQLite uses
  `BEGIN IMMEDIATE`. The worker still uses a local file lock for duplicate
  processes on one machine.
- Saved portal credentials and optional email addresses are encrypted with
  Fernet. `FEEDBACK_ENCRYPTION_KEY` is required; keep it private and back it up.
  Roll numbers also have a keyed HMAC lookup value for account matching.

## Main flow and files

- `streamlit_app.py`: sign-in and profile creation, profile email editing,
  notification display, manual feedback submission, schedule creation, and the
  schedule/status table. It calls database and notification helpers rather than
  implementing those details in the UI.
- `automation.py`: Selenium browser automation for the eCampus StudZone portal.
  It runs Chrome headless, signs in, opens Feedback, then performs the selected
  form. It always closes the browser in a `finally` block. It expects the portal
  selectors and page structure currently encoded in this file.
- `database.py`: schema creation/migrations, encrypted credential and email
  storage, authentication, UTC schedule storage, due-job claiming, job results,
  and account-scoped notifications. A DB-API adapter selects SQLite locally or
  Postgres when the URL is configured. The schedule status can be `pending`,
  `running`, `completed`, or `failed`; `result` describes the outcome.
- `worker.py`: one-shot scheduled worker. It gets due jobs in a loop, atomically
  claims them, loads that account's decrypted credentials, runs automation,
  records the outcome, and creates in-app/email notifications. A cross-platform
  file lock prevents two worker copies from processing jobs at once.
- `notification_delivery.py`: writes the in-app notification first, then tries
  to email the account's saved address. Email delivery failure does not undo the
  in-app notice or the feedback outcome.
- `email_notification.py`: SMTP configuration and message templates.
- `README.md`: user-facing setup and operational notes.
- `settings.py`: loads the known top-level Streamlit secrets into environment
  variables so the database and SMTP modules can share configuration with the
  GitHub Actions worker.
- `migrate_sqlite_to_postgres.py`: optional, idempotent copy of local encrypted
  database rows to configured Postgres; it has not been run. Keep the same
  encryption key when migrating.
- `.streamlit/secrets.example.toml`: safe template only; real `secrets.toml` is
  ignored by Git.
- `.github/workflows/run-feedback.yml`: GitHub Actions worker at 10:00 AM IST
  (`04:30 UTC`) plus manual dispatch. It expects repository secrets for the
  database URL, encryption key, and optional SMTP settings.

## Feedback outcomes

- A completed automation run sets the schedule status to `completed` and result
  to `completed`, and creates a `feedback_completed` notification.
- If the Intermediate form is absent, `automation.py` raises
  `FeedbackUnavailableError`. The worker records status `failed`, result
  `form_not_found`, and sends a `feedback_form_not_found` notification. This
  means no form was available; it is distinct from a Selenium/login/portal or
  submission error.
- Other exceptions set status `failed`, result `failed`, and create a
  `feedback_failed` notification. Worker logs include the exception type and a
  shortened message.
- A saved scheduled row's status is the overall completion state; its result
  explains the reason. For example, `failed` plus `form_not_found` is a missing
  form, while `failed` plus `failed` is a general automation error.
- The app displays notifications by the signed-in credential account. The
  schedule table shows each job, type, scheduled IST time, status, and result.

## Email behavior

- Email is optional. Add or update the email address in the profile. Email
  addresses are encrypted in SQLite.
- Set these Windows User environment variables for the Windows account that
  runs both Streamlit and Task Scheduler:
  - `FEEDBACK_SMTP_HOST`
  - `FEEDBACK_SMTP_USERNAME`
  - `FEEDBACK_SMTP_PASSWORD`
  - `FEEDBACK_SMTP_FROM` (optional; defaults to SMTP username)
  - `FEEDBACK_SMTP_PORT` (optional; defaults to 587)
- Port 465 uses SMTP over SSL; other configured ports use STARTTLS. Gmail
  typically uses `smtp.gmail.com`, port 587, and an app password if required.
- Notification email status is stored separately as `sent`, `failed`,
  `not_configured`, or other database-defined status. Missing SMTP config or an
  email send failure leaves the in-app notification intact.
- Templates exist for successful feedback, general failure, and missing
  Intermediate form. Missing-form email includes the scheduled time converted to
  IST when available.
- Do not put SMTP passwords, portal passwords, or encryption keys in code or
  documentation. Restart long-running app/task processes after changing
  environment variables so they receive the new values.

## Schedule and worker hosting

- The Streamlit UI sets newly created schedules to **10:00 AM IST**.
- On the project machine, the daily Windows task is named
  `FeedbackAuto355PM` (the name is historical; it has not been renamed). Its
  trigger was changed to 10:00 AM IST on 2026-10-18. It starts the virtual
  environment's Python executable with `worker.py`, using this project as the
  working directory. It runs once and exits; `StartWhenAvailable` is enabled.
- For Community Cloud, the GitHub Actions workflow is the scheduler because
  Streamlit Community Cloud apps hibernate when idle. Configure Supabase Postgres
  and secrets in both Streamlit Cloud and GitHub Actions. The cloud migration
  script is available but must be run by the user after setting the same Fernet
  key and database URL; do not run it without explicit instruction.
- The worker checks all schedules due at or before its run time, so late or
  missed schedules can be picked up by a later invocation.
- Previously saved jobs retain their original scheduled timestamps when the
  daily trigger or default app time changes. Check the schedule table/database
  if a date-specific job appears to have an older time.
- When the task starts Python directly, the process closes at completion and
  its console output is not currently persisted to a log file. Task Scheduler
  History / Event Viewer can show task/action start and completion if history is
  enabled, but not the worker's full stdout/stderr. Application outcomes remain
  available in SQLite and in the app's schedule/notification views.
- `worker.py` writes Python logging to its console only. For detailed historical
  diagnostics, use Windows Task Scheduler output redirection locally; in cloud,
  inspect the GitHub Actions run log. Avoid storing secrets in logs.
- `automation.py` supports Chromium and chromedriver overrides via
  `FEEDBACK_CHROME_BINARY` and `FEEDBACK_CHROMEDRIVER`; defaults search common
  Chromium/Chrome executable names and then use Selenium's driver management.
  A real cloud run is needed to confirm the eCampus portal accepts the hosted
  network/browser.

## Community Cloud configuration

- Set these Streamlit Cloud secrets: `FEEDBACK_DATABASE_MODE="postgres"`,
  `FEEDBACK_DATABASE_URL`, and `FEEDBACK_ENCRYPTION_KEY`; SMTP settings are
  optional. App code maps top-level secrets to environment variables in
  `settings.py`.
- Set the database URL, encryption key, and any SMTP settings as GitHub Actions
  repository secrets for the worker.
- Use `.streamlit/secrets.example.toml` as the local template; never commit real
  secrets or `.streamlit/secrets.toml`.
- `requirements.txt` pins the runtime libraries. `packages.txt` contains Debian
  Chromium and chromedriver packages used by the Community Cloud app.
- `FEEDBACK_DATABASE_MODE=postgres` with a missing URL fails fast rather than
  silently creating an ephemeral SQLite database.
- The local SQLite database is gitignored. Migration to Postgres is opt-in and
  copies encrypted values without exposing them; source and destination need the
  same key to keep credentials usable.
- App profile sign-in is separate from Streamlit Cloud viewer authentication.
  Restrict the deployed app if it is intended only for a known group.

## Reuse for another automation

The reusable pattern is: Streamlit profile/UI -> encrypted account data in
SQLite -> UTC schedule rows -> daily/periodic one-shot worker -> atomic claim and
lock -> browser/API automation -> durable outcome -> in-app notification and
optional SMTP email. Keep the automation-specific form interaction separate
from scheduling, persistence, and notifications. Define explicit error types
for expected business outcomes (such as a form not yet being available) and
reserve the generic failure status for unexpected errors.

For a new project, adapt rather than blindly copy: account data and consent,
external service selectors/APIs, job types, timezone, schedule trigger,
notification templates, and all environment variable names/values need review.
