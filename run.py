from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path
from uuid import uuid4

from flask import Flask, flash, redirect, render_template_string, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from config import Config


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "smart_schools_sms.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config.from_object(Config)
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def execute(query, params=()):
    with db() as conn:
        conn.execute(query, params)
        conn.commit()


def one(query, params=()):
    with db() as conn:
        return conn.execute(query, params).fetchone()


def all_rows(query, params=()):
    with db() as conn:
        return conn.execute(query, params).fetchall()


def init_db():
    schema = """
    CREATE TABLE IF NOT EXISTS schools (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        motto TEXT DEFAULT '',
        crest TEXT DEFAULT '',
        address TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        email TEXT DEFAULT '',
        academic_year TEXT DEFAULT '',
        term TEXT DEFAULT '',
        onboarded INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER,
        role TEXT NOT NULL,
        full_name TEXT NOT NULL,
        username TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        email TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        UNIQUE(school_id, username)
    );
    CREATE TABLE IF NOT EXISTS classes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        teacher_id INTEGER,
        UNIQUE(school_id, name)
    );
    CREATE TABLE IF NOT EXISTS subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        code TEXT DEFAULT '',
        teacher_id INTEGER,
        UNIQUE(school_id, name)
    );
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        class_id INTEGER,
        admission_no TEXT NOT NULL,
        guardian_name TEXT DEFAULT '',
        guardian_phone TEXT DEFAULT '',
        UNIQUE(school_id, admission_no)
    );
    CREATE TABLE IF NOT EXISTS scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        subject_id INTEGER NOT NULL,
        class_score REAL DEFAULT 0,
        exam_score REAL DEFAULT 0,
        remarks TEXT DEFAULT '',
        term TEXT DEFAULT '',
        academic_year TEXT DEFAULT '',
        teacher_id INTEGER,
        updated_at TEXT NOT NULL,
        UNIQUE(student_id, subject_id, term, academic_year)
    );
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        present_days INTEGER DEFAULT 0,
        total_days INTEGER DEFAULT 0,
        term TEXT DEFAULT '',
        academic_year TEXT DEFAULT '',
        UNIQUE(student_id, term, academic_year)
    );
    CREATE TABLE IF NOT EXISTS fees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        amount_due REAL DEFAULT 0,
        amount_paid REAL DEFAULT 0,
        term TEXT DEFAULT '',
        academic_year TEXT DEFAULT '',
        UNIQUE(student_id, term, academic_year)
    );
    CREATE TABLE IF NOT EXISTS announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        audience TEXT DEFAULT 'all',
        created_by INTEGER,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS timetable (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        class_id INTEGER,
        subject_id INTEGER,
        teacher_id INTEGER,
        day TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        room TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS library_resources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        school_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        category TEXT DEFAULT '',
        location TEXT DEFAULT '',
        copies INTEGER DEFAULT 1,
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    """
    with db() as conn:
        conn.executescript(schema)
        conn.commit()
    if not one("SELECT id FROM users WHERE role='system_admin' LIMIT 1"):
        execute(
            "INSERT INTO users (school_id, role, full_name, username, password_hash, created_at) VALUES (NULL, 'system_admin', 'System Administrator', 'admin', ?, ?)",
            (generate_password_hash("admin123"), datetime.utcnow().isoformat()),
        )


def current_user():
    if "user_id" not in session:
        return None
    return one("SELECT * FROM users WHERE id=?", (session["user_id"],))


def current_school():
    user = current_user()
    if not user or not user["school_id"]:
        return None
    return one("SELECT * FROM schools WHERE id=?", (user["school_id"],))


def login_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for("login"))
            if roles and user["role"] not in roles:
                flash("You do not have permission to open that page.", "error")
                return redirect(url_for("dashboard"))
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def school_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        school = current_school()
        if not school:
            return redirect(url_for("dashboard"))
        if not school["onboarded"] and request.endpoint != "onboarding":
            return redirect(url_for("onboarding"))
        return fn(*args, **kwargs)

    return wrapper


def save_crest(file_storage):
    if not file_storage or not file_storage.filename:
        return ""
    filename = secure_filename(file_storage.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        flash("Please upload a PNG, JPG, JPEG, or WEBP crest.", "error")
        return ""
    new_name = f"{uuid4().hex}{suffix}"
    file_storage.save(UPLOAD_DIR / new_name)
    return new_name


def role_label(role):
    return {
        "system_admin": "System Admin",
        "school_admin": "School Admin",
        "teacher": "Teacher",
        "student": "Student",
    }.get(role, role.title())


@app.context_processor
def inject_helpers():
    return {
        "user": current_user(),
        "school": current_school(),
        "role_label": role_label,
        "now": datetime.now(),
    }


BASE_HTML = """
{% macro field(label, name, type='text', value='', placeholder='') -%}
<label>{{ label }}<input name="{{ name }}" type="{{ type }}" value="{{ value }}" placeholder="{{ placeholder }}"></label>
{%- endmacro %}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title or 'Smart Schools SMS' }}</title>
  <style>
    :root{--ink:#152033;--muted:#667085;--line:#dbe4f0;--bg:#f5f8fc;--paper:#fff;--blue:#0b57d0;--navy:#062653;--cyan:#03b5d8;--green:#118a45;--gold:#f6b73c;--red:#d92d20;--shadow:0 16px 38px rgba(15,35,73,.12)}
    *{box-sizing:border-box}body{margin:0;font-family:Inter,Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--ink)}a{text-decoration:none;color:inherit}.wrap{max-width:1180px;margin:0 auto;padding:0 22px}
    .topbar{background:linear-gradient(90deg,var(--navy),#071a38);color:#fff;position:sticky;top:0;z-index:4;box-shadow:0 8px 24px rgba(0,0,0,.16)}.nav{min-height:74px;display:flex;align-items:center;justify-content:space-between;gap:18px}.brand{display:flex;align-items:center;gap:12px;font-weight:800}.crest{width:44px;height:44px;border-radius:8px;object-fit:cover;background:#fff;padding:3px}.crest-fallback{width:44px;height:44px;border-radius:8px;background:linear-gradient(135deg,var(--gold),var(--cyan));display:grid;place-items:center;font-weight:900;color:#062653}
    .navlinks{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.navlinks a,.btn{border:0;border-radius:8px;padding:10px 14px;font-weight:700;cursor:pointer;display:inline-flex;align-items:center;gap:8px}.navlinks a{color:#dce9ff}.navlinks a:hover{background:rgba(255,255,255,.12);color:#fff}.btn{background:var(--blue);color:#fff}.btn.green{background:var(--green)}.btn.ghost{background:#edf4ff;color:var(--blue)}
    .hero{min-height:520px;background:linear-gradient(90deg,rgba(6,38,83,.9),rgba(6,38,83,.55)),url('https://images.unsplash.com/photo-1580582932707-520aed937b7b?auto=format&fit=crop&w=1800&q=80') center/cover;color:white;display:flex;align-items:center}.hero-grid{display:grid;grid-template-columns:minmax(0,1fr) 430px;gap:36px;align-items:center}.hero h1{font-size:52px;line-height:1;margin:0 0 14px}.hero p{font-size:19px;line-height:1.55;max-width:640px;color:#e9f3ff}.module-card{background:white;color:var(--ink);border-radius:8px;padding:24px;box-shadow:var(--shadow);display:grid;grid-template-columns:repeat(2,1fr);gap:14px}.module{border:1px solid var(--line);border-radius:8px;padding:16px}.module strong{display:block;margin-bottom:6px}
    main{padding:28px 0 60px}.grid{display:grid;gap:18px}.grid.cols-4{grid-template-columns:repeat(4,1fr)}.grid.cols-3{grid-template-columns:repeat(3,1fr)}.grid.cols-2{grid-template-columns:repeat(2,1fr)}.card{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:20px;box-shadow:0 8px 22px rgba(15,35,73,.07)}.card h2,.card h3{margin-top:0}.stat{display:flex;justify-content:space-between;align-items:center}.stat b{font-size:28px}.muted{color:var(--muted)}.badge{display:inline-block;border-radius:999px;padding:5px 10px;background:#eaf2ff;color:var(--blue);font-weight:800;font-size:12px}
    form{display:grid;gap:14px}label{font-weight:700;color:#344054;font-size:13px}input,select,textarea{width:100%;margin-top:6px;border:1px solid var(--line);border-radius:8px;padding:12px;background:white;color:var(--ink)}textarea{min-height:90px}table{width:100%;border-collapse:collapse;background:white;border-radius:8px;overflow:hidden}th,td{padding:12px;border-bottom:1px solid var(--line);text-align:left;font-size:14px}th{background:#eef5ff;color:#173763}
    .layout{display:grid;grid-template-columns:230px 1fr;gap:22px}.side{background:#062653;color:white;border-radius:8px;padding:18px;height:max-content;position:sticky;top:94px}.side a{display:block;padding:11px;border-radius:8px;color:#dce9ff}.side a:hover{background:rgba(255,255,255,.12);color:#fff}.flash{padding:12px 14px;border-radius:8px;margin-bottom:14px}.flash.success{background:#e8f7ef;color:#075d2f}.flash.error{background:#ffefed;color:#9c1f14}.login-shell{min-height:calc(100vh - 74px);display:grid;place-items:center;background:linear-gradient(135deg,#eef6ff,#f9fbff)}.login-card{width:min(440px,92vw)}.report-head{display:flex;align-items:center;justify-content:space-between;border-bottom:3px solid var(--navy);padding-bottom:16px;margin-bottom:16px}.report-title{text-align:center}
    @media(max-width:900px){.hero-grid,.layout,.grid.cols-4,.grid.cols-3,.grid.cols-2{grid-template-columns:1fr}.hero h1{font-size:38px}.module-card{grid-template-columns:1fr}.nav{height:auto;padding:14px 0;align-items:flex-start}.side{position:static}.navlinks{justify-content:flex-end}}@media print{.topbar,.side,.no-print,.btn{display:none!important}body{background:white}.wrap,main{max-width:none;padding:0}.layout{display:block}.card{box-shadow:none;border:0}}
  </style>
</head>
<body>
  <header class="topbar no-print"><div class="wrap nav"><a class="brand" href="{{ url_for('index') }}">{% if school and school['crest'] %}<img class="crest" src="{{ url_for('uploads', filename=school['crest']) }}" alt="crest">{% else %}<span class="crest-fallback">SMS</span>{% endif %}<span><span style="display:block">Smart Schools SMS</span><small>{{ school['name'] if school else 'School Management System' }}</small></span></a><nav class="navlinks">{% if user %}<a href="{{ url_for('dashboard') }}">Dashboard</a><a href="{{ url_for('logout') }}">Logout</a>{% else %}<a href="{{ url_for('index') }}">Home</a><a href="{{ url_for('register_school') }}">Register School</a><a class="btn" href="{{ url_for('login') }}">Login</a>{% endif %}</nav></div></header>
  {% block body %}{% endblock %}
</body>
</html>
"""


def render(page, **context):
    return render_template_string(BASE_HTML.replace("{% block body %}{% endblock %}", page), **context)


SIDEBAR = """
<aside class="side no-print">
  <strong>{{ role_label(user['role']) }}</strong>
  <p class="muted" style="color:#bcd0ec">{{ user['full_name'] }}</p>
  <a href="{{ url_for('dashboard') }}">Dashboard</a>
  {% if user['role'] == 'system_admin' %}<a href="{{ url_for('schools') }}">Schools</a>{% endif %}
  {% if user['role'] in ['school_admin','teacher'] %}<a href="{{ url_for('students') }}">Students</a><a href="{{ url_for('teachers') }}">Teachers</a><a href="{{ url_for('classes_subjects') }}">Classes & Subjects</a><a href="{{ url_for('scores') }}">Scores</a>{% endif %}
  {% if user['role'] == 'school_admin' %}<a href="{{ url_for('attendance') }}">Attendance</a><a href="{{ url_for('fees') }}">Fees</a>{% endif %}
  {% if user['role'] in ['school_admin','teacher','student'] %}<a href="{{ url_for('announcements') }}">Notices</a><a href="{{ url_for('timetable') }}">Timetable</a><a href="{{ url_for('library') }}">Library</a>{% endif %}
  {% if user['role'] == 'school_admin' %}<a href="{{ url_for('onboarding') }}">School Profile</a>{% endif %}
  {% if user['role'] == 'student' %}<a href="{{ url_for('student_results') }}">My Results</a>{% endif %}
</aside>
"""


@app.route("/uploads/<filename>")
def uploads(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/")
def index():
    if current_user():
        return redirect(url_for("dashboard"))
    return render(
        """
        <section class="hero"><div class="wrap hero-grid"><div><span class="badge">Multi-school cloud and local SMS</span><h1>Smart Schools SMS</h1><p>Manage students, teachers, attendance, fees, scores, report sheets, and school identity from one polished platform.</p><p><a class="btn" href="{{ url_for('register_school') }}">Get Started</a> <a class="btn ghost" href="{{ url_for('login') }}">Login</a></p></div><div class="module-card"><div class="module"><strong>Students</strong><span class="muted">Unique logins, classes, results</span></div><div class="module"><strong>Teachers</strong><span class="muted">Enter scores and manage classes</span></div><div class="module"><strong>Results</strong><span class="muted">Printable report sheets</span></div><div class="module"><strong>Administration</strong><span class="muted">School setup, users, fees, analytics</span></div></div></div></section>
        <main class="wrap grid cols-3"><article class="card"><h3>School Branding</h3><p class="muted">Each school adds its crest, motto, contact details, academic year, and term.</p></article><article class="card"><h3>Role Portals</h3><p class="muted">System admin, school admin, teacher, and student users each see the tools they need.</p></article><article class="card"><h3>Cloud Ready</h3><p class="muted">Built with Flask and SQLite for local use, and ready to move to PostgreSQL for hosting.</p></article></main>
        """,
        title="Smart Schools SMS",
    )


@app.route("/register-school", methods=["GET", "POST"])
def register_school():
    if request.method == "POST":
        name = request.form["school_name"].strip()
        admin_name = request.form["admin_name"].strip()
        username = request.form["username"].strip().lower()
        password = request.form["password"]
        if not name or not admin_name or not username or not password:
            flash("Please complete all required fields.", "error")
        else:
            execute("INSERT INTO schools (name, created_at) VALUES (?, ?)", (name, datetime.utcnow().isoformat()))
            school = one("SELECT * FROM schools WHERE name=? ORDER BY id DESC", (name,))
            execute(
                "INSERT INTO users (school_id, role, full_name, username, password_hash, email, phone, created_at) VALUES (?, 'school_admin', ?, ?, ?, ?, ?, ?)",
                (school["id"], admin_name, username, generate_password_hash(password), request.form.get("email", ""), request.form.get("phone", ""), datetime.utcnow().isoformat()),
            )
            flash("School account created. Please login and complete setup.", "success")
            return redirect(url_for("login"))
    return render(
        """
        <main class="login-shell"><section class="card login-card"><h2>Register a School</h2><p class="muted">Create the first school admin account. After login, add crest, motto, and official details.</p>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post">{{ field('School Name', 'school_name') }}{{ field('Administrator Name', 'admin_name') }}{{ field('Admin Username', 'username') }}{{ field('Password', 'password', 'password') }}{{ field('Email', 'email', 'email') }}{{ field('Phone', 'phone') }}<button class="btn">Create School</button></form></section></main>
        """,
        title="Register School",
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = one("SELECT * FROM users WHERE username=? AND active=1", (request.form["username"].strip().lower(),))
        if user and check_password_hash(user["password_hash"], request.form["password"]):
            session["user_id"] = user["id"]
            flash(f"Welcome back, {user['full_name']}.", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "error")
    return render(
        """
        <main class="login-shell"><section class="card login-card"><h2>Login</h2><p class="muted">Use your admin, teacher, or student account.</p>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post">{{ field('Username', 'username') }}{{ field('Password', 'password', 'password') }}<button class="btn">Login</button></form><p class="muted">System admin default: username <b>admin</b>, password <b>admin123</b>. Change it before real use.</p></section></main>
        """,
        title="Login",
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def dashboard_stats(user):
    if user["role"] == "system_admin":
        return {"Schools": one("SELECT COUNT(*) c FROM schools")["c"], "Users": one("SELECT COUNT(*) c FROM users")["c"], "Students": one("SELECT COUNT(*) c FROM students")["c"], "Teachers": one("SELECT COUNT(*) c FROM users WHERE role='teacher'")["c"]}
    sid = user["school_id"]
    return {"Students": one("SELECT COUNT(*) c FROM students WHERE school_id=?", (sid,))["c"], "Teachers": one("SELECT COUNT(*) c FROM users WHERE school_id=? AND role='teacher'", (sid,))["c"], "Classes": one("SELECT COUNT(*) c FROM classes WHERE school_id=?", (sid,))["c"], "Subjects": one("SELECT COUNT(*) c FROM subjects WHERE school_id=?", (sid,))["c"]}


@app.route("/dashboard")
@login_required()
def dashboard():
    user = current_user()
    if user["role"] != "system_admin" and current_school() and not current_school()["onboarded"]:
        return redirect(url_for("onboarding"))
    if user["role"] == "student":
        return redirect(url_for("student_results"))
    schools = all_rows("SELECT * FROM schools ORDER BY created_at DESC LIMIT 8") if user["role"] == "system_admin" else []
    recent_scores = all_rows("SELECT st.admission_no, u.full_name, sub.name subject, sc.class_score, sc.exam_score FROM scores sc JOIN students st ON st.id=sc.student_id JOIN users u ON u.id=st.user_id JOIN subjects sub ON sub.id=sc.subject_id WHERE sc.school_id=? ORDER BY sc.updated_at DESC LIMIT 8", (user["school_id"],)) if user["role"] in {"school_admin", "teacher"} else []
    return render(
        """
        <main class="wrap">{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<div class="layout">""" + SIDEBAR + """<section class="grid"><h2>Dashboard</h2><div class="grid cols-4">{% for name, value in stats.items() %}<article class="card stat"><span>{{ name }}</span><b>{{ value }}</b></article>{% endfor %}</div>{% if schools %}<article class="card"><h3>Registered Schools</h3><table><tr><th>School</th><th>Motto</th><th>Status</th></tr>{% for s in schools %}<tr><td>{{ s['name'] }}</td><td>{{ s['motto'] or '-' }}</td><td>{{ 'Ready' if s['onboarded'] else 'Needs setup' }}</td></tr>{% endfor %}</table></article>{% endif %}{% if recent_scores %}<article class="card"><h3>Recent Scores</h3><table><tr><th>Student</th><th>Subject</th><th>Total</th></tr>{% for r in recent_scores %}<tr><td>{{ r['full_name'] }} <span class="muted">{{ r['admission_no'] }}</span></td><td>{{ r['subject'] }}</td><td>{{ r['class_score'] + r['exam_score'] }}</td></tr>{% endfor %}</table></article>{% endif %}</section></div></main>
        """,
        title="Dashboard",
        stats=dashboard_stats(user),
        schools=schools,
        recent_scores=recent_scores,
    )


@app.route("/onboarding", methods=["GET", "POST"])
@login_required("school_admin")
def onboarding():
    school = current_school()
    if request.method == "POST":
        crest = save_crest(request.files.get("crest")) or school["crest"]
        execute("UPDATE schools SET name=?, motto=?, crest=?, address=?, phone=?, email=?, academic_year=?, term=?, onboarded=1 WHERE id=?", (request.form["name"], request.form.get("motto", ""), crest, request.form.get("address", ""), request.form.get("phone", ""), request.form.get("email", ""), request.form.get("academic_year", ""), request.form.get("term", ""), school["id"]))
        flash("School profile saved.", "success")
        return redirect(url_for("dashboard"))
    return render(
        """
        <main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="card"><h2>School Profile Setup</h2><p class="muted">Add the information that appears on dashboards and printed report sheets.</p><form method="post" enctype="multipart/form-data" class="grid cols-2">{{ field('School Name', 'name', value=school['name']) }}{{ field('Motto', 'motto', value=school['motto']) }}{{ field('Phone', 'phone', value=school['phone']) }}{{ field('Email', 'email', 'email', school['email']) }}{{ field('Academic Year', 'academic_year', value=school['academic_year'], placeholder='2026/2027') }}{{ field('Term', 'term', value=school['term'], placeholder='Term 1') }}<label>School Crest<input name="crest" type="file" accept="image/*"></label><label>Address<textarea name="address">{{ school['address'] }}</textarea></label><button class="btn green">Save School Profile</button></form></section></div></main>
        """,
        title="School Profile",
        school=school,
    )


@app.route("/schools")
@login_required("system_admin")
def schools():
    schools = all_rows("SELECT * FROM schools ORDER BY name")
    return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="card"><h2>All Schools</h2><table><tr><th>Name</th><th>Contact</th><th>Academic Year</th><th>Status</th></tr>{% for s in schools %}<tr><td>{{ s['name'] }}</td><td>{{ s['phone'] }} {{ s['email'] }}</td><td>{{ s['academic_year'] }} {{ s['term'] }}</td><td>{{ 'Ready' if s['onboarded'] else 'Needs setup' }}</td></tr>{% endfor %}</table></section></div></main>""", title="Schools", schools=schools)


@app.route("/students", methods=["GET", "POST"])
@login_required("school_admin", "teacher")
@school_required
def students():
    user = current_user()
    sid = user["school_id"]
    if request.method == "POST" and user["role"] == "school_admin":
        full_name = request.form["full_name"].strip()
        admission_no = request.form["admission_no"].strip().upper()
        username = request.form["username"].strip().lower()
        password = request.form["password"] or admission_no.lower()
        try:
            execute("INSERT INTO users (school_id, role, full_name, username, password_hash, created_at) VALUES (?, 'student', ?, ?, ?, ?)", (sid, full_name, username, generate_password_hash(password), datetime.utcnow().isoformat()))
            new_user = one("SELECT * FROM users WHERE school_id=? AND username=?", (sid, username))
            execute("INSERT INTO students (school_id, user_id, class_id, admission_no, guardian_name, guardian_phone) VALUES (?, ?, ?, ?, ?, ?)", (sid, new_user["id"], request.form.get("class_id") or None, admission_no, request.form.get("guardian_name", ""), request.form.get("guardian_phone", "")))
            flash("Student added with a unique login.", "success")
        except sqlite3.IntegrityError:
            flash("Student username or admission number already exists.", "error")
    classes = all_rows("SELECT * FROM classes WHERE school_id=? ORDER BY name", (sid,))
    students = all_rows("SELECT st.*, u.full_name, u.username, c.name class_name FROM students st JOIN users u ON u.id=st.user_id LEFT JOIN classes c ON c.id=st.class_id WHERE st.school_id=? ORDER BY u.full_name", (sid,))
    return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>Students</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}{% if user['role'] == 'school_admin' %}<form method="post" class="grid cols-3">{{ field('Full Name', 'full_name') }}{{ field('Admission No', 'admission_no') }}{{ field('Username', 'username') }}{{ field('Password', 'password', 'password', placeholder='Leave blank to use admission no') }}<label>Class<select name="class_id"><option value="">No class</option>{% for c in classes %}<option value="{{ c['id'] }}">{{ c['name'] }}</option>{% endfor %}</select></label>{{ field('Guardian Name', 'guardian_name') }}{{ field('Guardian Phone', 'guardian_phone') }}<button class="btn green">Add Student</button></form>{% endif %}</article><article class="card"><table><tr><th>Name</th><th>Username</th><th>Admission No</th><th>Class</th><th>Guardian</th></tr>{% for s in students %}<tr><td>{{ s['full_name'] }}</td><td>{{ s['username'] }}</td><td>{{ s['admission_no'] }}</td><td>{{ s['class_name'] or '-' }}</td><td>{{ s['guardian_name'] }} {{ s['guardian_phone'] }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Students", students=students, classes=classes)


@app.route("/teachers", methods=["GET", "POST"])
@login_required("school_admin", "teacher")
@school_required
def teachers():
    user = current_user()
    sid = user["school_id"]
    if request.method == "POST" and user["role"] == "school_admin":
        try:
            execute("INSERT INTO users (school_id, role, full_name, username, password_hash, email, phone, created_at) VALUES (?, 'teacher', ?, ?, ?, ?, ?, ?)", (sid, request.form["full_name"], request.form["username"].strip().lower(), generate_password_hash(request.form["password"]), request.form.get("email", ""), request.form.get("phone", ""), datetime.utcnow().isoformat()))
            flash("Teacher account created.", "success")
        except sqlite3.IntegrityError:
            flash("That teacher username already exists.", "error")
    teachers = all_rows("SELECT * FROM users WHERE school_id=? AND role='teacher' ORDER BY full_name", (sid,))
    return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>Teachers</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}{% if user['role'] == 'school_admin' %}<form method="post" class="grid cols-3">{{ field('Full Name','full_name') }}{{ field('Username','username') }}{{ field('Password','password','password') }}{{ field('Email','email','email') }}{{ field('Phone','phone') }}<button class="btn green">Add Teacher</button></form>{% endif %}</article><article class="card"><table><tr><th>Name</th><th>Username</th><th>Email</th><th>Phone</th></tr>{% for t in teachers %}<tr><td>{{ t['full_name'] }}</td><td>{{ t['username'] }}</td><td>{{ t['email'] }}</td><td>{{ t['phone'] }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Teachers", teachers=teachers)


@app.route("/classes-subjects", methods=["GET", "POST"])
@login_required("school_admin", "teacher")
@school_required
def classes_subjects():
    user = current_user()
    sid = user["school_id"]
    if request.method == "POST" and user["role"] == "school_admin":
        try:
            if request.form["action"] == "class":
                execute("INSERT INTO classes (school_id, name, teacher_id) VALUES (?, ?, ?)", (sid, request.form["name"], request.form.get("teacher_id") or None))
            if request.form["action"] == "subject":
                execute("INSERT INTO subjects (school_id, name, code, teacher_id) VALUES (?, ?, ?, ?)", (sid, request.form["name"], request.form.get("code", ""), request.form.get("teacher_id") or None))
            flash("Saved.", "success")
        except sqlite3.IntegrityError:
            flash("That class or subject already exists.", "error")
    teachers = all_rows("SELECT * FROM users WHERE school_id=? AND role='teacher' ORDER BY full_name", (sid,))
    classes = all_rows("SELECT c.*, u.full_name teacher FROM classes c LEFT JOIN users u ON u.id=c.teacher_id WHERE c.school_id=? ORDER BY c.name", (sid,))
    subjects = all_rows("SELECT s.*, u.full_name teacher FROM subjects s LEFT JOIN users u ON u.id=s.teacher_id WHERE s.school_id=? ORDER BY s.name", (sid,))
    return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid cols-2"><article class="card"><h2>Classes</h2>{% if user['role']=='school_admin' %}<form method="post"><input type="hidden" name="action" value="class">{{ field('Class Name','name') }}<label>Class Teacher<select name="teacher_id"><option value="">None</option>{% for t in teachers %}<option value="{{ t['id'] }}">{{ t['full_name'] }}</option>{% endfor %}</select></label><button class="btn green">Add Class</button></form>{% endif %}<table><tr><th>Class</th><th>Teacher</th></tr>{% for c in classes %}<tr><td>{{ c['name'] }}</td><td>{{ c['teacher'] or '-' }}</td></tr>{% endfor %}</table></article><article class="card"><h2>Subjects</h2>{% if user['role']=='school_admin' %}<form method="post"><input type="hidden" name="action" value="subject">{{ field('Subject Name','name') }}{{ field('Code','code') }}<label>Teacher<select name="teacher_id"><option value="">None</option>{% for t in teachers %}<option value="{{ t['id'] }}">{{ t['full_name'] }}</option>{% endfor %}</select></label><button class="btn green">Add Subject</button></form>{% endif %}<table><tr><th>Subject</th><th>Code</th><th>Teacher</th></tr>{% for s in subjects %}<tr><td>{{ s['name'] }}</td><td>{{ s['code'] }}</td><td>{{ s['teacher'] or '-' }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Classes & Subjects", teachers=teachers, classes=classes, subjects=subjects)


@app.route("/scores", methods=["GET", "POST"])
@login_required("school_admin", "teacher")
@school_required
def scores():
    user = current_user()
    sid = user["school_id"]
    school = current_school()
    if request.method == "POST":
        execute("INSERT INTO scores (school_id, student_id, subject_id, class_score, exam_score, remarks, term, academic_year, teacher_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(student_id, subject_id, term, academic_year) DO UPDATE SET class_score=excluded.class_score, exam_score=excluded.exam_score, remarks=excluded.remarks, teacher_id=excluded.teacher_id, updated_at=excluded.updated_at", (sid, request.form["student_id"], request.form["subject_id"], float(request.form.get("class_score") or 0), float(request.form.get("exam_score") or 0), request.form.get("remarks", ""), request.form.get("term") or school["term"], request.form.get("academic_year") or school["academic_year"], user["id"], datetime.utcnow().isoformat()))
        flash("Score saved.", "success")
    students = all_rows("SELECT st.id, st.admission_no, u.full_name FROM students st JOIN users u ON u.id=st.user_id WHERE st.school_id=? ORDER BY u.full_name", (sid,))
    subjects = all_rows("SELECT * FROM subjects WHERE school_id=? ORDER BY name", (sid,))
    scores = all_rows("SELECT sc.*, u.full_name student, st.admission_no, sub.name subject FROM scores sc JOIN students st ON st.id=sc.student_id JOIN users u ON u.id=st.user_id JOIN subjects sub ON sub.id=sc.subject_id WHERE sc.school_id=? ORDER BY sc.updated_at DESC", (sid,))
    return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>Enter Scores</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3"><label>Student<select name="student_id" required>{% for s in students %}<option value="{{ s['id'] }}">{{ s['full_name'] }} - {{ s['admission_no'] }}</option>{% endfor %}</select></label><label>Subject<select name="subject_id" required>{% for s in subjects %}<option value="{{ s['id'] }}">{{ s['name'] }}</option>{% endfor %}</select></label>{{ field('Class Score', 'class_score', 'number') }}{{ field('Exam Score', 'exam_score', 'number') }}{{ field('Term', 'term', value=school['term']) }}{{ field('Academic Year', 'academic_year', value=school['academic_year']) }}{{ field('Remarks', 'remarks') }}<button class="btn green">Save Score</button></form></article><article class="card"><table><tr><th>Student</th><th>Subject</th><th>Total</th><th>Grade</th><th>Remarks</th></tr>{% for s in scores %}{% set total=s['class_score']+s['exam_score'] %}<tr><td>{{ s['student'] }} <span class="muted">{{ s['admission_no'] }}</span></td><td>{{ s['subject'] }}</td><td>{{ total }}</td><td>{{ 'A' if total>=80 else 'B' if total>=70 else 'C' if total>=60 else 'D' if total>=50 else 'F' }}</td><td>{{ s['remarks'] }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Scores", students=students, subjects=subjects, scores=scores)


@app.route("/attendance", methods=["GET", "POST"])
@login_required("school_admin")
@school_required
def attendance():
    user = current_user()
    sid = user["school_id"]
    school = current_school()
    if request.method == "POST":
        execute("INSERT INTO attendance (school_id, student_id, present_days, total_days, term, academic_year) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(student_id, term, academic_year) DO UPDATE SET present_days=excluded.present_days, total_days=excluded.total_days", (sid, request.form["student_id"], int(request.form.get("present_days") or 0), int(request.form.get("total_days") or 0), request.form.get("term") or school["term"], request.form.get("academic_year") or school["academic_year"]))
        flash("Attendance saved.", "success")
    students = all_rows("SELECT st.id, st.admission_no, u.full_name FROM students st JOIN users u ON u.id=st.user_id WHERE st.school_id=? ORDER BY u.full_name", (sid,))
    records = all_rows("SELECT a.*, u.full_name, st.admission_no FROM attendance a JOIN students st ON st.id=a.student_id JOIN users u ON u.id=st.user_id WHERE a.school_id=? ORDER BY u.full_name", (sid,))
    return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>Attendance</h2><p class="muted">Record each student's attendance for the active term. These figures appear on the student's printable report.</p>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3"><label>Student<select name="student_id" required>{% for s in students %}<option value="{{ s['id'] }}">{{ s['full_name'] }} - {{ s['admission_no'] }}</option>{% endfor %}</select></label>{{ field('Days Present', 'present_days', 'number') }}{{ field('Total School Days', 'total_days', 'number') }}{{ field('Term', 'term', value=school['term']) }}{{ field('Academic Year', 'academic_year', value=school['academic_year']) }}<button class="btn green">Save Attendance</button></form></article><article class="card"><table><tr><th>Student</th><th>Admission No</th><th>Present</th><th>Term</th></tr>{% for r in records %}<tr><td>{{ r['full_name'] }}</td><td>{{ r['admission_no'] }}</td><td>{{ r['present_days'] }}/{{ r['total_days'] }}</td><td>{{ r['term'] }} {{ r['academic_year'] }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Attendance", students=students, records=records)


@app.route("/fees", methods=["GET", "POST"])
@login_required("school_admin")
@school_required
def fees():
    user = current_user()
    sid = user["school_id"]
    school = current_school()
    if request.method == "POST":
        execute("INSERT INTO fees (school_id, student_id, amount_due, amount_paid, term, academic_year) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(student_id, term, academic_year) DO UPDATE SET amount_due=excluded.amount_due, amount_paid=excluded.amount_paid", (sid, request.form["student_id"], float(request.form.get("amount_due") or 0), float(request.form.get("amount_paid") or 0), request.form.get("term") or school["term"], request.form.get("academic_year") or school["academic_year"]))
        flash("Fee record saved.", "success")
    students = all_rows("SELECT st.id, st.admission_no, u.full_name FROM students st JOIN users u ON u.id=st.user_id WHERE st.school_id=? ORDER BY u.full_name", (sid,))
    records = all_rows("SELECT f.*, u.full_name, st.admission_no FROM fees f JOIN students st ON st.id=f.student_id JOIN users u ON u.id=st.user_id WHERE f.school_id=? ORDER BY u.full_name", (sid,))
    total_due = sum(r["amount_due"] for r in records)
    total_paid = sum(r["amount_paid"] for r in records)
    return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><div class="grid cols-3"><article class="card stat"><span>Total Due</span><b>{{ total_due }}</b></article><article class="card stat"><span>Total Paid</span><b>{{ total_paid }}</b></article><article class="card stat"><span>Outstanding</span><b>{{ total_due - total_paid }}</b></article></div><article class="card"><h2>Fees</h2><p class="muted">Track term bills, payments, and balances for each student.</p>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3"><label>Student<select name="student_id" required>{% for s in students %}<option value="{{ s['id'] }}">{{ s['full_name'] }} - {{ s['admission_no'] }}</option>{% endfor %}</select></label>{{ field('Amount Due', 'amount_due', 'number') }}{{ field('Amount Paid', 'amount_paid', 'number') }}{{ field('Term', 'term', value=school['term']) }}{{ field('Academic Year', 'academic_year', value=school['academic_year']) }}<button class="btn green">Save Fee</button></form></article><article class="card"><table><tr><th>Student</th><th>Admission No</th><th>Due</th><th>Paid</th><th>Balance</th></tr>{% for r in records %}<tr><td>{{ r['full_name'] }}</td><td>{{ r['admission_no'] }}</td><td>{{ r['amount_due'] }}</td><td>{{ r['amount_paid'] }}</td><td>{{ r['amount_due'] - r['amount_paid'] }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Fees", students=students, records=records, total_due=total_due, total_paid=total_paid)


@app.route("/announcements", methods=["GET", "POST"])
@login_required("school_admin", "teacher", "student")
@school_required
def announcements():
    user = current_user()
    sid = user["school_id"]
    if request.method == "POST" and user["role"] == "school_admin":
        execute("INSERT INTO announcements (school_id, title, body, audience, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)", (sid, request.form["title"], request.form["body"], request.form.get("audience", "all"), user["id"], datetime.utcnow().isoformat()))
        flash("Notice published.", "success")
    audience = "all" if user["role"] == "school_admin" else user["role"]
    rows = all_rows("SELECT a.*, u.full_name author FROM announcements a LEFT JOIN users u ON u.id=a.created_by WHERE a.school_id=? AND (a.audience='all' OR a.audience=?) ORDER BY a.created_at DESC", (sid, audience))
    return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid">{% if user['role'] == 'school_admin' %}<article class="card"><h2>Publish Notice</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-2">{{ field('Title', 'title') }}<label>Audience<select name="audience"><option value="all">Everyone</option><option value="teacher">Teachers</option><option value="student">Students</option></select></label><label style="grid-column:1/-1">Message<textarea name="body" required></textarea></label><button class="btn green">Publish Notice</button></form></article>{% endif %}<article class="card"><h2>School Notices</h2><table><tr><th>Title</th><th>Message</th><th>Audience</th><th>Date</th></tr>{% for r in rows %}<tr><td><b>{{ r['title'] }}</b><br><span class="muted">{{ r['author'] or 'School' }}</span></td><td>{{ r['body'] }}</td><td>{{ r['audience']|title }}</td><td>{{ r['created_at'][:10] }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Notices", rows=rows)


@app.route("/timetable", methods=["GET", "POST"])
@login_required("school_admin", "teacher", "student")
@school_required
def timetable():
    user = current_user()
    sid = user["school_id"]
    if request.method == "POST" and user["role"] == "school_admin":
        execute("INSERT INTO timetable (school_id, class_id, subject_id, teacher_id, day, start_time, end_time, room) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (sid, request.form.get("class_id") or None, request.form.get("subject_id") or None, request.form.get("teacher_id") or None, request.form["day"], request.form["start_time"], request.form["end_time"], request.form.get("room", "")))
        flash("Timetable period added.", "success")
    classes = all_rows("SELECT * FROM classes WHERE school_id=? ORDER BY name", (sid,))
    subjects = all_rows("SELECT * FROM subjects WHERE school_id=? ORDER BY name", (sid,))
    teachers = all_rows("SELECT * FROM users WHERE school_id=? AND role='teacher' ORDER BY full_name", (sid,))
    rows = all_rows("SELECT tt.*, c.name class_name, s.name subject_name, u.full_name teacher_name FROM timetable tt LEFT JOIN classes c ON c.id=tt.class_id LEFT JOIN subjects s ON s.id=tt.subject_id LEFT JOIN users u ON u.id=tt.teacher_id WHERE tt.school_id=? ORDER BY CASE tt.day WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 ELSE 6 END, tt.start_time", (sid,))
    return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid">{% if user['role'] == 'school_admin' %}<article class="card"><h2>Build Timetable</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-4"><label>Class<select name="class_id"><option value="">General</option>{% for c in classes %}<option value="{{ c['id'] }}">{{ c['name'] }}</option>{% endfor %}</select></label><label>Subject<select name="subject_id"><option value="">None</option>{% for s in subjects %}<option value="{{ s['id'] }}">{{ s['name'] }}</option>{% endfor %}</select></label><label>Teacher<select name="teacher_id"><option value="">None</option>{% for t in teachers %}<option value="{{ t['id'] }}">{{ t['full_name'] }}</option>{% endfor %}</select></label><label>Day<select name="day"><option>Monday</option><option>Tuesday</option><option>Wednesday</option><option>Thursday</option><option>Friday</option><option>Saturday</option></select></label>{{ field('Start Time', 'start_time', 'time') }}{{ field('End Time', 'end_time', 'time') }}{{ field('Room', 'room') }}<button class="btn green">Add Period</button></form></article>{% endif %}<article class="card"><h2>Timetable</h2><table><tr><th>Day</th><th>Time</th><th>Class</th><th>Subject</th><th>Teacher</th><th>Room</th></tr>{% for r in rows %}<tr><td>{{ r['day'] }}</td><td>{{ r['start_time'] }} - {{ r['end_time'] }}</td><td>{{ r['class_name'] or 'General' }}</td><td>{{ r['subject_name'] or '-' }}</td><td>{{ r['teacher_name'] or '-' }}</td><td>{{ r['room'] }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Timetable", classes=classes, subjects=subjects, teachers=teachers, rows=rows)


@app.route("/library", methods=["GET", "POST"])
@login_required("school_admin", "teacher", "student")
@school_required
def library():
    user = current_user()
    sid = user["school_id"]
    if request.method == "POST" and user["role"] == "school_admin":
        execute("INSERT INTO library_resources (school_id, title, category, location, copies, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (sid, request.form["title"], request.form.get("category", ""), request.form.get("location", ""), int(request.form.get("copies") or 1), request.form.get("notes", ""), datetime.utcnow().isoformat()))
        flash("Library resource added.", "success")
    rows = all_rows("SELECT * FROM library_resources WHERE school_id=? ORDER BY title", (sid,))
    return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid">{% if user['role'] == 'school_admin' %}<article class="card"><h2>Library Resources</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ field('Title', 'title') }}{{ field('Category', 'category', placeholder='Textbook, Reader, Device') }}{{ field('Location', 'location', placeholder='Library shelf A') }}{{ field('Copies', 'copies', 'number', value='1') }}{{ field('Notes', 'notes') }}<button class="btn green">Add Resource</button></form></article>{% endif %}<article class="card"><h2>Library Catalogue</h2><table><tr><th>Title</th><th>Category</th><th>Location</th><th>Copies</th><th>Notes</th></tr>{% for r in rows %}<tr><td>{{ r['title'] }}</td><td>{{ r['category'] }}</td><td>{{ r['location'] }}</td><td>{{ r['copies'] }}</td><td>{{ r['notes'] }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Library", rows=rows)


@app.route("/my-results")
@login_required("student")
@school_required
def student_results():
    user = current_user()
    student = one("SELECT * FROM students WHERE user_id=?", (user["id"],))
    rows = all_rows("SELECT sc.*, sub.name subject, sub.code FROM scores sc JOIN subjects sub ON sub.id=sc.subject_id WHERE sc.student_id=? ORDER BY sub.name", (student["id"],)) if student else []
    attendance = one("SELECT * FROM attendance WHERE student_id=? ORDER BY id DESC LIMIT 1", (student["id"],)) if student else None
    fees = one("SELECT * FROM fees WHERE student_id=? ORDER BY id DESC LIMIT 1", (student["id"],)) if student else None
    total = sum((r["class_score"] + r["exam_score"]) for r in rows)
    average = round(total / len(rows), 2) if rows else 0
    return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="card"><div class="report-head">{% if school['crest'] %}<img class="crest" src="{{ url_for('uploads', filename=school['crest']) }}" alt="crest">{% endif %}<div class="report-title"><h2>{{ school['name'] }}</h2><p>{{ school['motto'] }}</p><strong>Student Report Sheet</strong></div><button class="btn no-print" onclick="window.print()">Print Result</button></div><div class="grid cols-3"><p><b>Student:</b> {{ user['full_name'] }}</p><p><b>Admission No:</b> {{ student['admission_no'] if student else '-' }}</p><p><b>Term:</b> {{ school['term'] }}</p></div><table><tr><th>Subject</th><th>Class Score</th><th>Exam Score</th><th>Total</th><th>Grade</th><th>Remarks</th></tr>{% for r in rows %}{% set total=r['class_score']+r['exam_score'] %}<tr><td>{{ r['subject'] }}</td><td>{{ r['class_score'] }}</td><td>{{ r['exam_score'] }}</td><td>{{ total }}</td><td>{{ 'A' if total>=80 else 'B' if total>=70 else 'C' if total>=60 else 'D' if total>=50 else 'F' }}</td><td>{{ r['remarks'] }}</td></tr>{% endfor %}</table><div class="grid cols-3" style="margin-top:18px"><article class="card"><b>Average</b><br>{{ average }}%</article><article class="card"><b>Attendance</b><br>{{ attendance['present_days'] if attendance else 0 }}/{{ attendance['total_days'] if attendance else 0 }}</article><article class="card"><b>Fee Balance</b><br>{{ ((fees['amount_due'] - fees['amount_paid']) if fees else 0) }}</article></div><p class="muted">Printed on {{ now.strftime('%d %B %Y') }}. This soft copy can be sent to parents or guardians.</p></section></div></main>""", title="My Results", student=student, rows=rows, attendance=attendance, fees=fees, average=average)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=Config.DEBUG)
