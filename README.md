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
