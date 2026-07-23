# Smart Schools SMS

A professional multi-school School Management System for school administrators, teachers, students, and a system administrator.

## Main Features

- System admin dashboard for all registered schools
- School registration and first-login profile setup
- School crest, motto, address, phone, email, academic year, and term
- School admin portal for students, teachers, classes, and subjects
- Unique student usernames and passwords
- Teacher portal for entering and updating scores
- Student portal for viewing and printing report sheets for parents
- Attendance records for each term
- Fee tracking with amount due, paid, and balance
- School notices and announcements
- Timetable management
- Library/resource catalogue
- Printable soft-copy results with school branding
- Local SQLite database for offline/local school deployment
- PostgreSQL-ready database layer for online hosting

## Run Locally

```bash
pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:5000`.

On Windows, you can also double-click:

```text
start-local.bat
```

Optional first-run system administrator (set this environment variable before the first start):

```text
BOOTSTRAP_ADMIN_PASSWORD=replace-with-a-strong-temporary-password
```

The username is `admin`; the user must change the bootstrap password at first login. No known default account is created when this variable is absent.

## School Workflow

1. A school registers from the home page.
2. The school admin logs in.
3. The school completes its profile with crest, motto, contact information, academic year, and term.
4. The school admin creates classes, subjects, teacher accounts, and student accounts.
5. Teachers enter student scores.
6. Students log in to view and print their results.
7. The school admin can also manage attendance, fees, notices, timetable, and library resources.

## Project Folder

```text
run.py                    Main Smart Schools SMS application
config.py                 Environment and app settings
requirements.txt          Python packages
start-local.bat           One-click local school startup for Windows
Procfile                  Hosting start command
render.yaml               Render hosting configuration
.env.example              Environment variable template
uploads/                  School crest uploads
docs/USER_GUIDE.md        Admin, teacher, and student guide
docs/LOCAL_SCHOOL_SETUP.md Local network setup guide
docs/HOSTING_ONLINE.md    Internet hosting guide
```

## Hosting Online

For internet hosting, use a platform such as Render, Railway, Fly.io, DigitalOcean, or a VPS.

Recommended production changes:

- Set a strong `SECRET_KEY` in environment variables.
- Set `FLASK_DEBUG=0`.
- Use PostgreSQL instead of SQLite for many schools and many users by setting `DATABASE_URL`.
- Put uploaded crests on persistent storage.
- Use HTTPS.
- Change the default system admin password.

Simple hosting checklist:

1. Create a GitHub repository and push this project.
2. Create a new web service on Render, Railway, Fly.io, or a VPS.
3. Set the start command to `gunicorn run:app`.
4. Add environment variables for `SECRET_KEY`, `FLASK_DEBUG=0`, and `PORT`.
5. Use PostgreSQL before allowing many schools to use the system at the same time.
6. Test registration, login, score entry, and student report printing after deployment.

Example environment variables:

```env
SECRET_KEY=replace-with-a-long-random-secret
FLASK_DEBUG=0
PORT=5000
DATABASE_URL=postgresql://username:password@host:5432/database_name
    BOOTSTRAP_ADMIN_PASSWORD=replace-with-a-strong-temporary-password
SESSION_COOKIE_SECURE=1
SESSION_LIFETIME_MINUTES=480
```

## Local School Version

For a local-only deployment, install Python on the school computer, run `python run.py`, and access the system on the local network using the computer's IP address and port `5000`.

Example:

```text
http://192.168.1.20:5000
```

The school computer should stay on while teachers and students are using the system.

## Student report payments, data import, and password reset

- Students pay the configured `STUDENT_REPORT_FEE` (default GH₵10) for the current academic year and term before report HTML/PDF access. Paystack is verified server-side; configure the Paystack webhook as `APP_URL/payments/paystack/unified-webhook` so both parent and student payments are handled.
- School administrators can open **Students → Import Students or Teachers**, upload CSV or XLSX, review validation errors, choose **skip** or **update** for duplicates, commit valid rows, and view the saved import report.
- Administrator password resets use Resend. Verify the sender domain in Resend, set `RESEND_API_KEY`, `EMAIL_FROM`, and the public HTTPS `APP_URL`. Reset links expire after `PASSWORD_RESET_EXPIRY_MINUTES` and are single-use. `EMAIL_FROM` must be a sender on the exact verified domain, for example `Smart Schools SMS <reset@mail.yourschool.com>`. The testing sender `onboarding@resend.dev` can only send to the email address that owns the Resend account and returns HTTP 403 for other administrators.

Never commit real Paystack or Resend keys. Add them only in the host environment. New database tables are created safely at application startup by SQLAlchemy: `student_report_payments`, `password_reset_tokens`, and `bulk_import_jobs`.

### Import columns

Student required columns: `full_name, username, admission_no, class`. Optional: `email, phone, guardian_name, guardian_email, guardian_phone, password`. The class must already exist.

Teacher required columns: `full_name, username`. Optional: `email, phone, password`.

### Manual test checklist

1. Sign in as a student with an email (or guardian email), open My Results, complete a GH₵10 Paystack test payment, and verify HTML/PDF access unlocks only for the current term.
2. Confirm an unpaid student cannot see scores on the dashboard, report page, or PDF endpoint.
3. Confirm an existing paid parent can still open the linked child's report.
4. Import a CSV/XLSX with valid, invalid, and duplicate student/teacher rows; preview it, test skip/update, and check the final report.
5. Request an admin reset by email, use the link once, confirm the new password works, then confirm reuse and expiry are rejected.
