# Feedback automation

Streamlit app for End Semester and Intermediate feedback. It stores account
credentials and a required email address, allows manual or scheduled runs,
records outcomes, and sends email notifications when SMTP is configured.

Roll numbers, portal passwords, and email addresses are stored as plain text in
the database (no encryption). Anyone with database access can read them, so keep
the database URL private and limit who can open the Supabase project.

## Local development

The app uses SQLite locally unless `FEEDBACK_DATABASE_URL` is configured.
Install dependencies and run the app with:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

## Deploy to Streamlit Community Cloud

The deployed app needs a hosted database because the Community Cloud app
filesystem is not durable storage. This project supports Supabase Postgres and
continues to use SQLite for local development. Scheduled feedback is handled by
GitHub Actions; it cannot depend on a Streamlit app staying awake.

### 1. Create Supabase storage

Create a Supabase project and copy its Postgres connection string. Use a
connection pooler URL if the project/network requires it, and include
`sslmode=require`. The app creates its tables and indexes on first start.

### 2. Configure secrets

For local Streamlit development, copy
`.streamlit/secrets.example.toml` to `.streamlit/secrets.toml` and fill in the
real values. The secrets file is ignored by Git. In Community Cloud, paste the
same keys and values into the app's **Settings → Secrets** panel:

- `FEEDBACK_DATABASE_MODE = "postgres"`
- `FEEDBACK_DATABASE_URL = "..."` (Supabase connection string)
- Optional SMTP values: `FEEDBACK_SMTP_HOST`, `FEEDBACK_SMTP_PORT`,
  `FEEDBACK_SMTP_USERNAME`, `FEEDBACK_SMTP_PASSWORD`, and `FEEDBACK_SMTP_FROM`

Never add real credentials, database URLs, or `.streamlit/secrets.toml` to the
repository.

### 3. Deploy the Streamlit app

Push the project to a GitHub repository, then create a Streamlit Community Cloud
app using `streamlit_app.py` as the entry point. Add the secrets above before
using the app. Streamlit Community Cloud installs Python packages from
`requirements.txt` and Linux packages from `packages.txt`.

### 4. Enable scheduled feedback

The included `.github/workflows/run-feedback.yml` runs the worker daily at
12:37 AM IST (19:07 UTC the previous day) and supports manual runs from GitHub
Actions. Add these
repository **Actions secrets**:

- `FEEDBACK_DATABASE_URL`
- Optional SMTP settings matching the app secrets

The workflow serializes runs and processes all due jobs from Supabase. Keep it
enabled on the repository's default branch. The local Windows task can remain
for local use; avoid running both schedulers against separate databases because
they would not see each other's schedules.
GitHub can start scheduled workflows later than their cron minute; the worker
processes every job due by the time it starts.

The `.github/workflows/keep-streamlit-awake.yml` workflow requests the deployed
Streamlit URL every hour to reduce Community Cloud inactivity hibernation. It
also supports manual dispatch. This is a best-effort keep-alive; hosting
platform policies can still suspend or restart the app.

## Notifications and email

Notifications are stored per account and remain available in the app when email
is unconfigured or delivery fails. SMTP port 465 uses SSL; other ports use
STARTTLS. Gmail commonly uses `smtp.gmail.com`, port `587`, and an app password
where required. Never use a student's portal password as the SMTP password.

The schedule list distinguishes a failed job (`status = failed`) from its
specific result (for example, `form_not_found`). The worker's task log records
run details in GitHub Actions; the app keeps durable job outcomes and
notifications in Postgres.

## Deployment notes

- Community Cloud apps may sleep after inactivity. GitHub Actions supplies the
  daily worker independently of the app being open.
- The deployed Selenium worker and manual-submit button run in Linux with
  headless Chromium. The project includes Chromium packages for Community Cloud;
  GitHub-hosted Ubuntu runners include Chrome and ChromeDriver.
- The eCampus portal may respond differently to cloud-hosted browser traffic or
  reject cloud network addresses. A deployment cannot confirm portal access
  without a real cloud run; inspect the Streamlit and GitHub Actions logs after
  setup.
- The app's profile sign-in is application-level, not a gate on who can open a
  public Community Cloud URL. Restrict app access or deploy privately if it
  should only be available to a defined group.
