from __future__ import annotations

import os
import secrets
from datetime import datetime
from functools import wraps
from pathlib import Path
from uuid import uuid4

from flask import Flask, abort, flash, redirect, render_template_string, request, send_from_directory, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, UniqueConstraint, event, text
from sqlalchemy.engine import Engine
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from config import Config


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

db = SQLAlchemy()

LOGIN_AUDIENCES = {
    "admin": {"system_admin", "school_admin"},
    "teacher": {"teacher"},
    "student": {"student"},
}


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["SQLALCHEMY_DATABASE_URI"] = normalize_database_url(
        os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'smart_schools_sms.db'}")
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = Config.SQLALCHEMY_ENGINE_OPTIONS
    app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    db.init_app(app)
    register_routes(app)
    return app


class School(db.Model):
    __tablename__ = "schools"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(180), nullable=False, index=True)
    motto = db.Column(db.String(220), default="")
    crest = db.Column(db.String(260), default="")
    address = db.Column(db.Text, default="")
    phone = db.Column(db.String(80), default="")
    email = db.Column(db.String(160), default="")
    head_name = db.Column(db.String(160), default="")
    head_title = db.Column(db.String(100), default="Head of School")
    head_signature = db.Column(db.String(260), default="")
    academic_year = db.Column(db.String(40), default="")
    term = db.Column(db.String(40), default="")
    onboarded = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=True, index=True)
    role = db.Column(db.String(30), nullable=False, index=True)
    full_name = db.Column(db.String(160), nullable=False)
    username = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(160), default="")
    phone = db.Column(db.String(80), default="")
    active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    must_change_password = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("school_id", "username", name="uq_user_school_username"),)


class ClassRoom(db.Model):
    __tablename__ = "classes"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    __table_args__ = (UniqueConstraint("school_id", "name", name="uq_class_school_name"),)


class Subject(db.Model):
    __tablename__ = "subjects"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(40), default="")
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    __table_args__ = (UniqueConstraint("school_id", "name", name="uq_subject_school_name"),)


class Student(db.Model):
    __tablename__ = "students"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id", ondelete="SET NULL"), nullable=True, index=True)
    admission_no = db.Column(db.String(80), nullable=False)
    guardian_name = db.Column(db.String(160), default="")
    guardian_phone = db.Column(db.String(80), default="")
    __table_args__ = (UniqueConstraint("school_id", "admission_no", name="uq_student_school_admission"),)


class Score(db.Model):
    __tablename__ = "scores"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True)
    class_score = db.Column(db.Float, default=0)
    exam_score = db.Column(db.Float, default=0)
    conduct = db.Column(db.String(120), default="")
    position = db.Column(db.String(40), default="")
    remarks = db.Column(db.String(220), default="")
    term = db.Column(db.String(40), default="", index=True)
    academic_year = db.Column(db.String(40), default="", index=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("student_id", "subject_id", "term", "academic_year", name="uq_score_period"),)


class Attendance(db.Model):
    __tablename__ = "attendance"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    present_days = db.Column(db.Integer, default=0)
    total_days = db.Column(db.Integer, default=0)
    term = db.Column(db.String(40), default="")
    academic_year = db.Column(db.String(40), default="")
    __table_args__ = (UniqueConstraint("student_id", "term", "academic_year", name="uq_attendance_period"),)


class Fee(db.Model):
    __tablename__ = "fees"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    amount_due = db.Column(db.Float, default=0)
    amount_paid = db.Column(db.Float, default=0)
    term = db.Column(db.String(40), default="")
    academic_year = db.Column(db.String(40), default="")
    __table_args__ = (UniqueConstraint("student_id", "term", "academic_year", name="uq_fee_period"),)


class Announcement(db.Model):
    __tablename__ = "announcements"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    body = db.Column(db.Text, nullable=False)
    audience = db.Column(db.String(30), default="all", index=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class Timetable(db.Model):
    __tablename__ = "timetable"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id", ondelete="SET NULL"), nullable=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    day = db.Column(db.String(20), nullable=False)
    start_time = db.Column(db.String(10), nullable=False)
    end_time = db.Column(db.String(10), nullable=False)
    room = db.Column(db.String(80), default="")


class LibraryResource(db.Model):
    __tablename__ = "library_resources"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False, index=True)
    category = db.Column(db.String(80), default="")
    location = db.Column(db.String(120), default="")
    copies = db.Column(db.Integer, default=1)
    notes = db.Column(db.String(260), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class SchoolEvent(db.Model):
    __tablename__ = "school_events"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    event_date = db.Column(db.Date, nullable=False, index=True)
    audience = db.Column(db.String(30), default="all", index=True)
    notes = db.Column(db.String(260), default="")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


Index("ix_scores_student_period", Score.student_id, Score.term, Score.academic_year)
Index("ix_users_role_school", User.school_id, User.role)
Index("ix_students_school_class", Student.school_id, Student.class_id)
Index("ix_attendance_student_period", Attendance.student_id, Attendance.term, Attendance.academic_year)
Index("ix_fees_student_period", Fee.student_id, Fee.term, Fee.academic_year)


@event.listens_for(Engine, "connect")
def set_database_pragmas(dbapi_connection, _):
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_db() -> None:
    db.create_all()
    ensure_compatibility_migrations()
    if not User.query.filter_by(role="system_admin", username="admin").first():
        admin = User(
            role="system_admin",
            full_name="System Administrator",
            username="admin",
            password_hash=generate_password_hash("admin123"),
            must_change_password=True,
        )
        db.session.add(admin)
        db.session.commit()


def ensure_compatibility_migrations() -> None:
    if db.engine.dialect.name != "sqlite":
        return
    user_columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(users)")).fetchall()}
    if "must_change_password" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT 1 NOT NULL"))
    school_columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(schools)")).fetchall()}
    for ddl in [
        ("head_name", "ALTER TABLE schools ADD COLUMN head_name VARCHAR(160) DEFAULT ''"),
        ("head_title", "ALTER TABLE schools ADD COLUMN head_title VARCHAR(100) DEFAULT 'Head of School'"),
        ("head_signature", "ALTER TABLE schools ADD COLUMN head_signature VARCHAR(260) DEFAULT ''"),
    ]:
        if ddl[0] not in school_columns:
            db.session.execute(text(ddl[1]))
    score_columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(scores)")).fetchall()}
    for ddl in [
        ("conduct", "ALTER TABLE scores ADD COLUMN conduct VARCHAR(120) DEFAULT ''"),
        ("position", "ALTER TABLE scores ADD COLUMN position VARCHAR(40) DEFAULT ''"),
    ]:
        if ddl[0] not in score_columns:
            db.session.execute(text(ddl[1]))
    if "school_events" not in {row[0] for row in db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}:
        db.session.execute(text("""
            CREATE TABLE school_events (
                id INTEGER NOT NULL,
                school_id INTEGER NOT NULL,
                title VARCHAR(180) NOT NULL,
                event_date DATE NOT NULL,
                audience VARCHAR(30) DEFAULT 'all',
                notes VARCHAR(260) DEFAULT '',
                created_by INTEGER,
                created_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(school_id) REFERENCES schools (id) ON DELETE CASCADE,
                FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
            )
        """))
        db.session.execute(text("CREATE INDEX ix_school_events_school_id ON school_events (school_id)"))
        db.session.execute(text("CREATE INDEX ix_school_events_event_date ON school_events (event_date)"))
        db.session.execute(text("CREATE INDEX ix_school_events_audience ON school_events (audience)"))
    db.session.commit()


def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None


def current_school():
    user = current_user()
    return db.session.get(School, user.school_id) if user and user.school_id else None


def role_label(role: str) -> str:
    return {"system_admin": "System Admin", "school_admin": "School Admin", "teacher": "Teacher", "student": "Student"}.get(role, role.title())


def login_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for("login"))
            if user.must_change_password and request.endpoint not in {"change_password", "logout", "uploads"}:
                flash("Please change your temporary password before continuing.", "error")
                return redirect(url_for("change_password"))
            if roles and user.role not in roles:
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
        if not school.onboarded and request.endpoint != "onboarding":
            return redirect(url_for("onboarding"))
        return fn(*args, **kwargs)
    return wrapper


def csrf_token() -> str:
    session.setdefault("_csrf", secrets.token_urlsafe(32))
    return session["_csrf"]


def validate_csrf() -> None:
    if request.method == "POST" and request.form.get("_csrf") != session.get("_csrf"):
        abort(400)


def save_crest(file_storage) -> str:
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


def clamp_score(value: str, maximum: float) -> float:
    score = float(value or 0)
    if score < 0 or score > maximum:
        raise ValueError(f"Score must be between 0 and {maximum:g}.")
    return score


def generate_temporary_password() -> str:
    return secrets.token_urlsafe(9).replace("-", "A").replace("_", "7")[:12]


def create_login_slip(user: User, temp_password: str) -> None:
    session["login_slip"] = {
        "user_id": user.id,
        "school_id": user.school_id,
        "name": user.full_name,
        "role": role_label(user.role),
        "username": user.username,
        "password": temp_password,
        "created_at": datetime.now().strftime("%d %B %Y %H:%M"),
    }


def get_school_student_query(school_id):
    return db.session.query(Student, User, ClassRoom).join(User, Student.user_id == User.id).outerjoin(ClassRoom, Student.class_id == ClassRoom.id).filter(Student.school_id == school_id)


BASE_HTML = """
{% macro csrf() -%}<input type="hidden" name="_csrf" value="{{ csrf_token() }}">{%- endmacro %}
{% macro field(label, name, type='text', value='', placeholder='', required=false) -%}
<label>{{ label }}<input name="{{ name }}" type="{{ type }}" value="{{ value }}" placeholder="{{ placeholder }}" {% if required %}required{% endif %}></label>
{%- endmacro %}
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title or 'Smart Schools SMS' }}</title>
<style>
:root{--ink:#132238;--muted:#667085;--line:#d9e2ee;--bg:#f5f8fc;--paper:#fff;--blue:#0b57d0;--navy:#062653;--teal:#008c8c;--green:#118a45;--gold:#f5ae2f;--red:#c92a2a;--purple:#6d3fc8;--shadow:0 16px 38px rgba(15,35,73,.12)}
*{box-sizing:border-box}body{margin:0;font-family:Inter,Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--ink)}a{text-decoration:none;color:inherit}.wrap{max-width:1220px;margin:0 auto;padding:0 22px}.topbar{background:linear-gradient(90deg,var(--navy),#071a38);color:#fff;position:sticky;top:0;z-index:5;box-shadow:0 8px 24px rgba(0,0,0,.16)}.nav{min-height:74px;display:flex;align-items:center;justify-content:space-between;gap:18px}.brand{display:flex;align-items:center;gap:12px;font-weight:800}.crest,.crest-fallback{width:44px;height:44px;border-radius:8px}.crest{object-fit:cover;background:#fff;padding:3px}.crest-fallback{background:linear-gradient(135deg,var(--gold),#00bdd6);display:grid;place-items:center;font-weight:900;color:#062653}.navlinks{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.navlinks a,.btn{border:0;border-radius:8px;padding:10px 14px;font-weight:800;cursor:pointer;display:inline-flex;align-items:center;gap:8px}.navlinks a{color:#dce9ff}.navlinks a:hover{background:rgba(255,255,255,.12);color:#fff}.btn{background:var(--blue);color:#fff}.btn.green{background:var(--green)}.btn.red{background:var(--red)}.btn.ghost{background:#edf4ff;color:var(--blue)}.btn.purple{background:var(--purple)}
.hero{min-height:520px;background:linear-gradient(90deg,rgba(6,38,83,.9),rgba(6,38,83,.5)),url('https://images.unsplash.com/photo-1580582932707-520aed937b7b?auto=format&fit=crop&w=1800&q=80') center/cover;color:white;display:flex;align-items:center}.hero-grid{display:grid;grid-template-columns:minmax(0,1fr) 450px;gap:36px;align-items:center}.hero h1{font-size:52px;line-height:1;margin:0 0 14px}.hero p{font-size:19px;line-height:1.55;max-width:650px;color:#e9f3ff}.module-card{background:white;color:var(--ink);border-radius:8px;padding:24px;box-shadow:var(--shadow);display:grid;grid-template-columns:repeat(2,1fr);gap:14px}.module{border:1px solid var(--line);border-radius:8px;padding:16px}.module strong{display:block;margin-bottom:6px}
main{padding:28px 0 60px}.grid{display:grid;gap:18px}.cols-4{grid-template-columns:repeat(4,1fr)}.cols-3{grid-template-columns:repeat(3,1fr)}.cols-2{grid-template-columns:repeat(2,1fr)}.card{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:20px;box-shadow:0 8px 22px rgba(15,35,73,.07)}.card h2,.card h3{margin-top:0}.stat{display:flex;justify-content:space-between;align-items:center}.stat b{font-size:28px}.muted{color:var(--muted)}.badge{display:inline-block;border-radius:999px;padding:5px 10px;background:#eaf2ff;color:var(--blue);font-weight:800;font-size:12px}
form{display:grid;gap:14px}label{font-weight:750;color:#344054;font-size:13px}input,select,textarea{width:100%;margin-top:6px;border:1px solid var(--line);border-radius:8px;padding:12px;background:white;color:var(--ink)}textarea{min-height:90px}table{width:100%;border-collapse:collapse;background:white;border-radius:8px;overflow:hidden}th,td{padding:12px;border-bottom:1px solid var(--line);text-align:left;font-size:14px;vertical-align:top}th{background:#eef5ff;color:#173763}.actions{display:flex;gap:8px;flex-wrap:wrap}.layout{display:grid;grid-template-columns:230px 1fr;gap:22px}.side{background:#062653;color:white;border-radius:8px;padding:18px;height:max-content;position:sticky;top:94px}.side a{display:block;padding:11px;border-radius:8px;color:#dce9ff}.side a:hover{background:rgba(255,255,255,.12);color:#fff}.flash{padding:12px 14px;border-radius:8px;margin-bottom:14px}.flash.success{background:#e8f7ef;color:#075d2f}.flash.error{background:#ffefed;color:#9c1f14}.login-shell{min-height:calc(100vh - 74px);display:grid;place-items:center;background:linear-gradient(135deg,#eef6ff,#f9fbff)}.login-card{width:min(460px,92vw)}.report-head{display:flex;align-items:center;justify-content:space-between;border-bottom:3px solid var(--navy);padding-bottom:16px;margin-bottom:16px;gap:16px}.report-title{text-align:center}.report-title h2{margin-bottom:4px}.report-title p{margin:3px 0}.report-card{max-width:1060px}.report-crest{width:72px;height:72px}.report-meta p{margin:0}.signature-row{display:grid;grid-template-columns:1fr 220px;gap:24px;align-items:end;margin-top:26px;border-top:1px solid var(--line);padding-top:18px}.signature-img{display:block;max-width:180px;max-height:70px;object-fit:contain;margin:10px 0}.signature-line{height:58px;border-bottom:1px solid var(--ink);max-width:220px;margin-bottom:10px}.slip{max-width:520px;margin:auto;border:2px dashed var(--navy);background:white;padding:26px;border-radius:8px}
@media(max-width:940px){.hero-grid,.layout,.cols-4,.cols-3,.cols-2{grid-template-columns:1fr}.hero h1{font-size:38px}.module-card{grid-template-columns:1fr}.nav{height:auto;padding:14px 0;align-items:flex-start}.side{position:static}.navlinks{justify-content:flex-end}table{display:block;overflow-x:auto;white-space:nowrap}.report-head{align-items:flex-start}.signature-row{grid-template-columns:1fr}}@media(max-width:620px){.wrap{padding:0 14px}.nav,.report-head{flex-direction:column}.navlinks{justify-content:flex-start}.hero{min-height:560px}.hero h1{font-size:34px}.card{padding:16px}th,td{padding:10px}.btn{width:100%;justify-content:center}.actions .btn{width:auto}}@media print{.topbar,.side,.no-print,.btn{display:none!important}body{background:white}.wrap,main{max-width:none;padding:0}.layout{display:block}.card{box-shadow:none;border:0}.report-card{max-width:none}.slip{border:2px solid #111}table{display:table;white-space:normal}}
.topbar{background:linear-gradient(90deg,#0a3b50,#123c69 55%,#22543d)}.card{transition:transform .18s ease,box-shadow .18s ease}.card:hover{transform:translateY(-1px);box-shadow:0 14px 30px rgba(14,43,72,.1)}.login-shell{background:linear-gradient(90deg,rgba(6,35,58,.82),rgba(8,74,83,.7)),url('https://images.unsplash.com/photo-1509062522246-3755977927d7?auto=format&fit=crop&w=1800&q=80') center/cover}.login-card{backdrop-filter:blur(8px);background:rgba(255,255,255,.95)}.login-visual{border-radius:8px;min-height:160px;background:center/cover;margin:-6px -6px 16px}.login-visual.admin{background-image:linear-gradient(0deg,rgba(6,35,58,.18),rgba(6,35,58,.18)),url('https://images.unsplash.com/photo-1577896851231-70ef18881754?auto=format&fit=crop&w=900&q=80')}.login-visual.teacher{background-image:linear-gradient(0deg,rgba(6,35,58,.18),rgba(6,35,58,.18)),url('https://images.unsplash.com/photo-1588072432836-e10032774350?auto=format&fit=crop&w=900&q=80')}.login-visual.student{background-image:linear-gradient(0deg,rgba(6,35,58,.18),rgba(6,35,58,.18)),url('https://images.unsplash.com/photo-1524995997946-a1c2e315a42f?auto=format&fit=crop&w=900&q=80')}.dashboard-hero{background:linear-gradient(90deg,rgba(10,59,80,.93),rgba(34,84,61,.78)),url('https://images.unsplash.com/photo-1497633762265-9d179a990aa6?auto=format&fit=crop&w=1600&q=80') center/cover;color:white;border:0}.dashboard-hero h2{margin:0}.dashboard-hero .muted{color:#e5f2f2}.metric-card{border-left:5px solid var(--teal)}.metric-card b{color:#0a3b50}.progress{height:10px;border-radius:999px;background:#dce8ed;overflow:hidden}.progress span{display:block;height:100%;background:linear-gradient(90deg,var(--teal),var(--green))}.quick-actions a{justify-content:center}.feature-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.feature-strip div{background:#f7fbfd;border:1px solid var(--line);border-radius:8px;padding:14px}@media(max-width:940px){.feature-strip{grid-template-columns:1fr}.login-visual{min-height:130px}}
</style></head><body>
<header class="topbar no-print"><div class="wrap nav"><a class="brand" href="{{ url_for('index') }}">{% if school and school.crest %}<img class="crest" src="{{ url_for('uploads', filename=school.crest) }}" alt="crest">{% else %}<span class="crest-fallback">SMS</span>{% endif %}<span><span style="display:block">Smart Schools SMS</span><small>{{ school.name if school else 'School Management System' }}</small></span></a><nav class="navlinks">{% if user %}<a href="{{ url_for('dashboard') }}">Dashboard</a><a href="{{ url_for('logout') }}">Logout</a>{% else %}<a href="{{ url_for('index') }}">Home</a><a href="{{ url_for('register_school') }}">Register School</a><a class="btn" href="{{ url_for('login') }}">Login</a>{% endif %}</nav></div></header>
{% block body %}{% endblock %}</body></html>
"""


SIDEBAR = """
<aside class="side no-print"><strong>{{ role_label(user.role) }}</strong><p class="muted" style="color:#bcd0ec">{{ user.full_name }}</p>
<a href="{{ url_for('dashboard') }}">Dashboard</a>
{% if user.role == 'system_admin' %}<a href="{{ url_for('schools') }}">Schools</a>{% endif %}
{% if user.role in ['school_admin','teacher'] %}<a href="{{ url_for('students') }}">Students</a><a href="{{ url_for('teachers') }}">Teachers</a><a href="{{ url_for('classes_subjects') }}">Classes & Subjects</a><a href="{{ url_for('scores') }}">Scores</a>{% endif %}
{% if user.role == 'school_admin' %}<a href="{{ url_for('attendance') }}">Attendance</a><a href="{{ url_for('fees') }}">Fees</a><a href="{{ url_for('onboarding') }}">School Profile</a>{% endif %}
{% if user.role in ['school_admin','teacher','student'] %}<a href="{{ url_for('announcements') }}">Notices</a><a href="{{ url_for('calendar') }}">Calendar</a><a href="{{ url_for('timetable') }}">Timetable</a><a href="{{ url_for('library') }}">Library</a>{% endif %}
{% if user.role == 'student' %}<a href="{{ url_for('student_results') }}">My Results</a>{% endif %}</aside>
"""


def render(page, **context):
    return render_template_string(BASE_HTML.replace("{% block body %}{% endblock %}", page), **context)


def grade_info(total: float) -> dict[str, str]:
    if total >= 80:
        return {"grade": "A1", "interpretation": "Excellent"}
    if total >= 70:
        return {"grade": "B2", "interpretation": "Very Good"}
    if total >= 65:
        return {"grade": "B3", "interpretation": "Good"}
    if total >= 60:
        return {"grade": "C4", "interpretation": "Credit"}
    if total >= 55:
        return {"grade": "C5", "interpretation": "Credit"}
    if total >= 50:
        return {"grade": "C6", "interpretation": "Credit"}
    if total >= 45:
        return {"grade": "D7", "interpretation": "Pass"}
    if total >= 40:
        return {"grade": "E8", "interpretation": "Pass"}
    return {"grade": "F9", "interpretation": "Fail"}


def grade(total: float) -> str:
    return grade_info(total)["grade"]


def register_routes(app: Flask) -> None:
    @app.before_request
    def protect_posts():
        validate_csrf()

    @app.context_processor
    def inject_helpers():
        return {"user": current_user(), "school": current_school(), "role_label": role_label, "now": datetime.now(), "csrf_token": csrf_token, "grade": grade, "grade_info": grade_info}

    @app.route("/uploads/<filename>")
    def uploads(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    @app.route("/")
    def index():
        if current_user():
            return redirect(url_for("dashboard"))
        return render("""<section class="hero"><div class="wrap hero-grid"><div><span class="badge">Cloud and local school management</span><h1>Smart Schools SMS</h1><p>Manage students, teachers, attendance, fees, examinations, professional report cards, notices, timetable, library resources, and school identity in one platform.</p><p><a class="btn" href="{{ url_for('register_school') }}">Get Started</a> <a class="btn ghost" href="{{ url_for('login', portal='admin') }}">Admin Login</a> <a class="btn ghost" href="{{ url_for('login', portal='teacher') }}">Teacher Login</a> <a class="btn ghost" href="{{ url_for('login', portal='student') }}">Student Login</a></p></div><div class="module-card">{% for title, desc in modules %}<div class="module"><strong>{{ title }}</strong><span class="muted">{{ desc }}</span></div>{% endfor %}</div></div></section><main class="wrap grid cols-3"><article class="card"><h3>Multi-school Ready</h3><p class="muted">Each school has separated users, branding, results, attendance, and fees.</p></article><article class="card"><h3>Professional Reports</h3><p class="muted">Ghana grading, conduct, attendance, fees, and head signature are included.</p></article><article class="card"><h3>PostgreSQL Ready</h3><p class="muted">Use PostgreSQL online by setting DATABASE_URL.</p></article></main>""", title="Smart Schools SMS", modules=[("Students", "Unique logins and records"), ("Teachers", "Score entry and classes"), ("Attendance", "Term attendance tracking"), ("Fees", "Balances and payments"), ("Exams", "Ghana grading report cards"), ("Library", "School resources")])

    @app.route("/register-school", methods=["GET", "POST"])
    def register_school():
        if request.method == "POST":
            school = School(name=request.form["school_name"].strip())
            db.session.add(school)
            db.session.flush()
            admin = User(school_id=school.id, role="school_admin", full_name=request.form["admin_name"].strip(), username=request.form["username"].strip().lower(), password_hash=generate_password_hash(request.form["password"]), email=request.form.get("email", ""), phone=request.form.get("phone", ""), must_change_password=True)
            db.session.add(admin)
            try:
                db.session.commit()
                create_login_slip(admin, request.form["password"])
                flash("School account created. Print the login slip and complete setup.", "success")
                return redirect(url_for("login_slip"))
            except Exception:
                db.session.rollback()
                flash("That school admin username already exists for this school.", "error")
        return render("""<main class="login-shell"><section class="card login-card"><h2>Register a School</h2><p class="muted">Create the first school admin account.</p>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post">{{ csrf() }}{{ field('School Name','school_name', required=true) }}{{ field('Administrator Name','admin_name', required=true) }}{{ field('Admin Username','username', required=true) }}{{ field('Temporary Password','password','password', required=true) }}{{ field('Email','email','email') }}{{ field('Phone','phone') }}<button class="btn">Create School</button></form></section></main>""", title="Register School")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        portal = request.args.get("portal", "").lower()
        allowed_roles = LOGIN_AUDIENCES.get(portal)
        if request.method == "POST":
            portal = request.form.get("portal", "").lower()
            allowed_roles = LOGIN_AUDIENCES.get(portal)
            user = User.query.filter_by(username=request.form["username"].strip().lower(), active=True).first()
            if user and (not allowed_roles or user.role in allowed_roles) and check_password_hash(user.password_hash, request.form["password"]):
                session.clear()
                csrf_token()
                session["user_id"] = user.id
                flash(f"Welcome back, {user.full_name}.", "success")
                if user.must_change_password:
                    return redirect(url_for("change_password"))
                return redirect(url_for("dashboard"))
            flash("Invalid username, password, or portal.", "error")
        portal_label = {"admin": "Admin Login", "teacher": "Teacher Login", "student": "Student Login"}.get(portal, "Login")
        visual = portal if portal in {"admin", "teacher", "student"} else "admin"
        return render("""<main class="login-shell"><section class="card login-card"><div class="login-visual {{ visual }}"></div><h2>{{ portal_label }}</h2><p class="muted">Choose the correct portal for your account.</p><div class="actions"><a class="btn ghost" href="{{ url_for('login', portal='admin') }}">Admin</a><a class="btn ghost" href="{{ url_for('login', portal='teacher') }}">Teacher</a><a class="btn ghost" href="{{ url_for('login', portal='student') }}">Student</a></div>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post">{{ csrf() }}<input type="hidden" name="portal" value="{{ portal }}">{{ field('Username','username', required=true) }}{{ field('Password','password','password', required=true) }}<button class="btn">Login</button></form><p class="muted">Default system admin: <b>admin</b> / <b>admin123</b>. Change it before real use.</p></section></main>""", title=portal_label, portal=portal, portal_label=portal_label, visual=visual)

    @app.route("/change-password", methods=["GET", "POST"])
    @login_required()
    def change_password():
        user = current_user()
        if request.method == "POST":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")
            if not check_password_hash(user.password_hash, current_password):
                flash("Current password is incorrect.", "error")
            elif len(new_password) < 8:
                flash("New password must be at least 8 characters.", "error")
            elif new_password != confirm_password:
                flash("New passwords do not match.", "error")
            else:
                user.password_hash = generate_password_hash(new_password)
                user.must_change_password = False
                db.session.commit()
                flash("Password changed successfully.", "success")
                return redirect(url_for("dashboard"))
        return render("""<main class="login-shell"><section class="card login-card"><h2>Change Password</h2><p class="muted">Create a private password before using your account.</p>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post">{{ csrf() }}{{ field('Current Temporary Password','current_password','password', required=true) }}{{ field('New Password','new_password','password', required=true) }}{{ field('Confirm New Password','confirm_password','password', required=true) }}<button class="btn green">Save Password</button></form></section></main>""", title="Change Password")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/login-slip")
    def login_slip():
        slip = session.get("login_slip")
        if not slip:
            flash("No login slip is available. Create a user to generate one.", "error")
            return redirect(url_for("dashboard") if current_user() else "login")
        return render("""<main class="wrap"><section class="slip"><h2>Smart Schools SMS Login Slip</h2><p class="muted">Print this slip and give it to the user. The password is shown only now.</p><table><tr><th>Name</th><td>{{ slip.name }}</td></tr><tr><th>Role</th><td>{{ slip.role }}</td></tr><tr><th>Username</th><td><b>{{ slip.username }}</b></td></tr><tr><th>Temporary Password</th><td><b>{{ slip.password }}</b></td></tr><tr><th>Created</th><td>{{ slip.created_at }}</td></tr></table><p class="no-print"><button class="btn" onclick="window.print()">Print Login Slip</button> <a class="btn ghost" href="{{ url_for('dashboard') }}">Done</a></p></section></main>""", title="Login Slip", slip=slip)

    @app.route("/dashboard")
    @login_required()
    def dashboard():
        user = current_user()
        if user.role != "system_admin" and current_school() and not current_school().onboarded:
            return redirect(url_for("onboarding"))
        if user.role == "student":
            return redirect(url_for("student_results"))
        sid = user.school_id
        stats = {"Schools": School.query.count(), "Users": User.query.count(), "Students": Student.query.count(), "Teachers": User.query.filter_by(role="teacher").count()} if user.role == "system_admin" else {"Students": Student.query.filter_by(school_id=sid).count(), "Teachers": User.query.filter_by(school_id=sid, role="teacher").count(), "Classes": ClassRoom.query.filter_by(school_id=sid).count(), "Subjects": Subject.query.filter_by(school_id=sid).count()}
        schools = School.query.order_by(School.created_at.desc()).limit(8).all() if user.role == "system_admin" else []
        recent_scores = db.session.query(Score, Student, User, Subject).join(Student, Score.student_id == Student.id).join(User, Student.user_id == User.id).join(Subject, Score.subject_id == Subject.id).filter(Score.school_id == sid).order_by(Score.updated_at.desc()).limit(8).all() if user.role in {"school_admin", "teacher"} else []
        analytics = {}
        grade_bands = []
        upcoming_events = []
        if user.role != "system_admin":
            school = current_school()
            attendance_rows = Attendance.query.filter_by(school_id=sid, term=school.term, academic_year=school.academic_year).all()
            present = sum(r.present_days for r in attendance_rows)
            possible = sum(r.total_days for r in attendance_rows)
            fees = Fee.query.filter_by(school_id=sid, term=school.term, academic_year=school.academic_year).all()
            due = sum(r.amount_due for r in fees)
            paid = sum(r.amount_paid for r in fees)
            scores_all = Score.query.filter_by(school_id=sid, term=school.term, academic_year=school.academic_year).all()
            totals = [s.class_score + s.exam_score for s in scores_all]
            analytics = {
                "Attendance Rate": round((present / possible) * 100, 1) if possible else 0,
                "Fee Collection": round((paid / due) * 100, 1) if due else 0,
                "Average Score": round(sum(totals) / len(totals), 1) if totals else 0,
                "Pending Fees": round(max(due - paid, 0), 2),
            }
            grade_bands = [
                ("A1-B3", len([t for t in totals if t >= 65])),
                ("C4-C6", len([t for t in totals if 50 <= t < 65])),
                ("D7-E8", len([t for t in totals if 40 <= t < 50])),
                ("F9", len([t for t in totals if t < 40])),
            ]
            upcoming_events = SchoolEvent.query.filter(SchoolEvent.school_id == sid, SchoolEvent.event_date >= datetime.utcnow().date(), SchoolEvent.audience.in_(["all", user.role])).order_by(SchoolEvent.event_date).limit(5).all()
        return render("""<main class="wrap">{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card dashboard-hero"><h2>{{ school.name if school else 'System Dashboard' }}</h2><p class="muted">{{ school.academic_year ~ ' ' ~ school.term if school else 'All registered schools and users' }}</p><div class="feature-strip"><div><b>Learning</b><br><span class="muted">Classes, subjects, scores, reports</span></div><div><b>Operations</b><br><span class="muted">Attendance, fees, timetable</span></div><div><b>Communication</b><br><span class="muted">Notices, calendar, library</span></div></div></article><div class="grid cols-4">{% for name, value in stats.items() %}<article class="card stat metric-card"><span>{{ name }}</span><b>{{ value }}</b></article>{% endfor %}</div>{% if analytics %}<div class="grid cols-4">{% for name, value in analytics.items() %}<article class="card metric-card"><span>{{ name }}</span><b>{% if name == 'Pending Fees' %}{{ value }}{% else %}{{ value }}%{% endif %}</b>{% if name != 'Pending Fees' %}<div class="progress"><span style="width:{{ [value,100]|min }}%"></span></div>{% endif %}</article>{% endfor %}</div><article class="card"><h3>Academic Performance Bands</h3><table><tr><th>Grade Band</th><th>Entries</th></tr>{% for label, count in grade_bands %}<tr><td>{{ label }}</td><td>{{ count }}</td></tr>{% endfor %}</table></article><article class="card quick-actions"><h3>Quick Actions</h3><div class="grid cols-4"><a class="btn ghost" href="{{ url_for('students') }}">Students</a><a class="btn ghost" href="{{ url_for('scores') }}">Exams</a><a class="btn ghost" href="{{ url_for('attendance') }}">Attendance</a><a class="btn ghost" href="{{ url_for('calendar') }}">Calendar</a></div></article>{% endif %}{% if upcoming_events %}<article class="card"><h3>Upcoming School Calendar</h3><table><tr><th>Date</th><th>Event</th><th>Audience</th></tr>{% for e in upcoming_events %}<tr><td>{{ e.event_date.strftime('%d %b %Y') }}</td><td>{{ e.title }}</td><td>{{ e.audience|title }}</td></tr>{% endfor %}</table></article>{% endif %}{% if schools %}<article class="card"><h3>Registered Schools</h3><table><tr><th>School</th><th>Academic Year</th><th>Status</th></tr>{% for s in schools %}<tr><td>{{ s.name }}</td><td>{{ s.academic_year }} {{ s.term }}</td><td>{{ 'Ready' if s.onboarded else 'Needs setup' }}</td></tr>{% endfor %}</table></article>{% endif %}{% if recent_scores %}<article class="card"><h3>Recent Scores</h3><table><tr><th>Student</th><th>Subject</th><th>Total</th><th>Grade</th></tr>{% for sc, st, su, sub in recent_scores %}{% set total=sc.class_score + sc.exam_score %}<tr><td>{{ su.full_name }} <span class="muted">{{ st.admission_no }}</span></td><td>{{ sub.name }}</td><td>{{ total }}</td><td>{{ grade(total) }}</td></tr>{% endfor %}</table></article>{% endif %}</section></div></main>""", title="Dashboard", stats=stats, schools=schools, recent_scores=recent_scores, analytics=analytics, grade_bands=grade_bands, upcoming_events=upcoming_events)

    @app.route("/schools", methods=["GET", "POST"])
    @login_required("system_admin")
    def schools():
        if request.method == "POST" and request.form.get("action") == "delete":
            school = db.session.get(School, int(request.form["school_id"]))
            if school:
                db.session.delete(school)
                db.session.commit()
                flash("School and its related records were deleted.", "success")
        schools = School.query.order_by(School.name).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="card"><h2>All Schools</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<table><tr><th>Name</th><th>Contact</th><th>Status</th><th>Action</th></tr>{% for s in schools %}<tr><td>{{ s.name }}</td><td>{{ s.phone }} {{ s.email }}</td><td>{{ 'Ready' if s.onboarded else 'Needs setup' }}</td><td><form method="post" onsubmit="return confirm('Delete this school and all its data?')">{{ csrf() }}<input type="hidden" name="action" value="delete"><input type="hidden" name="school_id" value="{{ s.id }}"><button class="btn red">Delete</button></form></td></tr>{% endfor %}</table></section></div></main>""", title="Schools", schools=schools)

    @app.route("/onboarding", methods=["GET", "POST"])
    @login_required("school_admin")
    def onboarding():
        school = current_school()
        if request.method == "POST":
            school.name = request.form["name"]
            school.motto = request.form.get("motto", "")
            school.phone = request.form.get("phone", "")
            school.email = request.form.get("email", "")
            school.address = request.form.get("address", "")
            school.head_name = request.form.get("head_name", "")
            school.head_title = request.form.get("head_title", "Head of School")
            school.academic_year = request.form.get("academic_year", "")
            school.term = request.form.get("term", "")
            school.crest = save_crest(request.files.get("crest")) or school.crest
            school.head_signature = save_crest(request.files.get("head_signature")) or school.head_signature
            school.onboarded = True
            db.session.commit()
            flash("School profile saved.", "success")
            return redirect(url_for("dashboard"))
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="card"><h2>School Profile Setup</h2><form method="post" enctype="multipart/form-data" class="grid cols-2">{{ csrf() }}{{ field('School Name','name', value=school.name) }}{{ field('Motto','motto', value=school.motto) }}{{ field('Phone','phone', value=school.phone) }}{{ field('Email','email','email', school.email) }}{{ field('Academic Year','academic_year', value=school.academic_year, placeholder='2026/2027') }}{{ field('Term','term', value=school.term, placeholder='Term 1') }}{{ field('Head Name','head_name', value=school.head_name) }}{{ field('Head Title','head_title', value=school.head_title or 'Head of School') }}<label>School Crest<input name="crest" type="file" accept="image/*"></label><label>Head Signature<input name="head_signature" type="file" accept="image/*"></label><label style="grid-column:1/-1">Address<textarea name="address">{{ school.address }}</textarea></label><button class="btn green">Save School Profile</button></form></section></div></main>""", title="School Profile")

    @app.route("/students", methods=["GET", "POST"])
    @login_required("school_admin", "teacher")
    @school_required
    def students():
        user = current_user()
        sid = user.school_id
        if request.method == "POST" and user.role == "school_admin":
            action = request.form.get("action")
            if action == "delete":
                student = Student.query.filter_by(id=int(request.form["student_id"]), school_id=sid).first()
                if student:
                    linked_user = db.session.get(User, student.user_id)
                    db.session.delete(student)
                    if linked_user:
                        db.session.delete(linked_user)
                    db.session.commit()
                    flash("Student account and records deleted.", "success")
            elif action == "reset_password":
                student = Student.query.filter_by(id=int(request.form["student_id"]), school_id=sid).first()
                linked_user = db.session.get(User, student.user_id) if student else None
                if linked_user:
                    password = generate_temporary_password()
                    linked_user.password_hash = generate_password_hash(password)
                    linked_user.must_change_password = True
                    db.session.commit()
                    create_login_slip(linked_user, password)
                    flash("Student password reset. Print the new login slip.", "success")
                    return redirect(url_for("login_slip"))
            else:
                password = request.form["password"] or generate_temporary_password()
                new_user = User(school_id=sid, role="student", full_name=request.form["full_name"].strip(), username=request.form["username"].strip().lower(), password_hash=generate_password_hash(password), must_change_password=True)
                db.session.add(new_user)
                db.session.flush()
                db.session.add(Student(school_id=sid, user_id=new_user.id, class_id=request.form.get("class_id") or None, admission_no=request.form["admission_no"].strip().upper(), guardian_name=request.form.get("guardian_name", ""), guardian_phone=request.form.get("guardian_phone", "")))
                try:
                    db.session.commit()
                    create_login_slip(new_user, password)
                    return redirect(url_for("login_slip"))
                except Exception:
                    db.session.rollback()
                    flash("Student username or admission number already exists.", "error")
        classes = ClassRoom.query.filter_by(school_id=sid).order_by(ClassRoom.name).all()
        students = get_school_student_query(sid).order_by(User.full_name).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>Students</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}{% if user.role == 'school_admin' %}<form method="post" class="grid cols-3">{{ csrf() }}{{ field('Full Name','full_name') }}{{ field('Admission No','admission_no') }}{{ field('Username','username') }}{{ field('Temporary Password','password','password', placeholder='Leave blank to auto-generate') }}<label>Class<select name="class_id"><option value="">No class</option>{% for c in classes %}<option value="{{ c.id }}">{{ c.name }}</option>{% endfor %}</select></label>{{ field('Guardian Name','guardian_name') }}{{ field('Guardian Phone','guardian_phone') }}<button class="btn green">Add Student & Print Login Slip</button></form>{% endif %}</article><article class="card"><table><tr><th>Name</th><th>Username</th><th>Admission No</th><th>Class</th><th>Guardian</th><th>Action</th></tr>{% for st, u, c in students %}<tr><td>{{ u.full_name }}</td><td>{{ u.username }}</td><td>{{ st.admission_no }}</td><td>{{ c.name if c else '-' }}</td><td>{{ st.guardian_name }} {{ st.guardian_phone }}</td><td>{% if user.role == 'school_admin' %}<div class="actions"><form method="post">{{ csrf() }}<input type="hidden" name="action" value="reset_password"><input type="hidden" name="student_id" value="{{ st.id }}"><button class="btn ghost">Reset Password</button></form><form method="post" onsubmit="return confirm('Delete this student?')">{{ csrf() }}<input type="hidden" name="action" value="delete"><input type="hidden" name="student_id" value="{{ st.id }}"><button class="btn red">Delete</button></form></div>{% endif %}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Students", classes=classes, students=students)

    @app.route("/teachers", methods=["GET", "POST"])
    @login_required("school_admin", "teacher")
    @school_required
    def teachers():
        user = current_user()
        sid = user.school_id
        if request.method == "POST" and user.role == "school_admin":
            action = request.form.get("action")
            if action == "delete":
                teacher = User.query.filter_by(id=int(request.form["teacher_id"]), school_id=sid, role="teacher").first()
                if teacher:
                    ClassRoom.query.filter_by(teacher_id=teacher.id).update({"teacher_id": None})
                    Subject.query.filter_by(teacher_id=teacher.id).update({"teacher_id": None})
                    Timetable.query.filter_by(teacher_id=teacher.id).update({"teacher_id": None})
                    db.session.delete(teacher)
                    db.session.commit()
                    flash("Teacher account deleted.", "success")
            elif action == "reset_password":
                teacher = User.query.filter_by(id=int(request.form["teacher_id"]), school_id=sid, role="teacher").first()
                if teacher:
                    password = generate_temporary_password()
                    teacher.password_hash = generate_password_hash(password)
                    teacher.must_change_password = True
                    db.session.commit()
                    create_login_slip(teacher, password)
                    flash("Teacher password reset. Print the new login slip.", "success")
                    return redirect(url_for("login_slip"))
            else:
                password = request.form["password"] or generate_temporary_password()
                teacher = User(school_id=sid, role="teacher", full_name=request.form["full_name"], username=request.form["username"].strip().lower(), password_hash=generate_password_hash(password), email=request.form.get("email", ""), phone=request.form.get("phone", ""), must_change_password=True)
                db.session.add(teacher)
                try:
                    db.session.commit()
                    create_login_slip(teacher, password)
                    return redirect(url_for("login_slip"))
                except Exception:
                    db.session.rollback()
                    flash("That teacher username already exists.", "error")
        teachers = User.query.filter_by(school_id=sid, role="teacher").order_by(User.full_name).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>Teachers</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}{% if user.role == 'school_admin' %}<form method="post" class="grid cols-3">{{ csrf() }}{{ field('Full Name','full_name') }}{{ field('Username','username') }}{{ field('Temporary Password','password','password', placeholder='Leave blank to auto-generate') }}{{ field('Email','email','email') }}{{ field('Phone','phone') }}<button class="btn green">Add Teacher & Print Login Slip</button></form>{% endif %}</article><article class="card"><table><tr><th>Name</th><th>Username</th><th>Email</th><th>Phone</th><th>Action</th></tr>{% for t in teachers %}<tr><td>{{ t.full_name }}</td><td>{{ t.username }}</td><td>{{ t.email }}</td><td>{{ t.phone }}</td><td>{% if user.role == 'school_admin' %}<div class="actions"><form method="post">{{ csrf() }}<input type="hidden" name="action" value="reset_password"><input type="hidden" name="teacher_id" value="{{ t.id }}"><button class="btn ghost">Reset Password</button></form><form method="post" onsubmit="return confirm('Delete this teacher?')">{{ csrf() }}<input type="hidden" name="action" value="delete"><input type="hidden" name="teacher_id" value="{{ t.id }}"><button class="btn red">Delete</button></form></div>{% endif %}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Teachers", teachers=teachers)

    @app.route("/classes-subjects", methods=["GET", "POST"])
    @login_required("school_admin", "teacher")
    @school_required
    def classes_subjects():
        user = current_user()
        sid = user.school_id
        if request.method == "POST" and user.role == "school_admin":
            try:
                if request.form["action"] == "class":
                    db.session.add(ClassRoom(school_id=sid, name=request.form["name"], teacher_id=request.form.get("teacher_id") or None))
                if request.form["action"] == "subject":
                    db.session.add(Subject(school_id=sid, name=request.form["name"], code=request.form.get("code", ""), teacher_id=request.form.get("teacher_id") or None))
                db.session.commit()
                flash("Saved.", "success")
            except Exception:
                db.session.rollback()
                flash("That class or subject already exists.", "error")
        teachers = User.query.filter_by(school_id=sid, role="teacher").order_by(User.full_name).all()
        classes = db.session.query(ClassRoom, User).outerjoin(User, ClassRoom.teacher_id == User.id).filter(ClassRoom.school_id == sid).order_by(ClassRoom.name).all()
        subjects = db.session.query(Subject, User).outerjoin(User, Subject.teacher_id == User.id).filter(Subject.school_id == sid).order_by(Subject.name).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid cols-2"><article class="card"><h2>Classes</h2>{% if user.role=='school_admin' %}<form method="post">{{ csrf() }}<input type="hidden" name="action" value="class">{{ field('Class Name','name') }}<label>Class Teacher<select name="teacher_id"><option value="">None</option>{% for t in teachers %}<option value="{{ t.id }}">{{ t.full_name }}</option>{% endfor %}</select></label><button class="btn green">Add Class</button></form>{% endif %}<table><tr><th>Class</th><th>Teacher</th></tr>{% for c,t in classes %}<tr><td>{{ c.name }}</td><td>{{ t.full_name if t else '-' }}</td></tr>{% endfor %}</table></article><article class="card"><h2>Subjects</h2>{% if user.role=='school_admin' %}<form method="post">{{ csrf() }}<input type="hidden" name="action" value="subject">{{ field('Subject Name','name') }}{{ field('Code','code') }}<label>Teacher<select name="teacher_id"><option value="">None</option>{% for t in teachers %}<option value="{{ t.id }}">{{ t.full_name }}</option>{% endfor %}</select></label><button class="btn green">Add Subject</button></form>{% endif %}<table><tr><th>Subject</th><th>Code</th><th>Teacher</th></tr>{% for s,t in subjects %}<tr><td>{{ s.name }}</td><td>{{ s.code }}</td><td>{{ t.full_name if t else '-' }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Classes & Subjects", teachers=teachers, classes=classes, subjects=subjects)

    @app.route("/scores", methods=["GET", "POST"])
    @login_required("school_admin", "teacher")
    @school_required
    def scores():
        user = current_user()
        school = current_school()
        sid = user.school_id
        if request.method == "POST":
            try:
                score = Score.query.filter_by(student_id=request.form["student_id"], subject_id=request.form["subject_id"], term=request.form.get("term") or school.term, academic_year=request.form.get("academic_year") or school.academic_year).first() or Score(school_id=sid, student_id=request.form["student_id"], subject_id=request.form["subject_id"], term=request.form.get("term") or school.term, academic_year=request.form.get("academic_year") or school.academic_year)
                score.class_score = clamp_score(request.form.get("class_score"), 50)
                score.exam_score = clamp_score(request.form.get("exam_score"), 50)
                score.conduct = request.form.get("conduct", "")
                score.position = request.form.get("position", "")
                score.remarks = request.form.get("remarks", "")
                score.teacher_id = user.id
                score.updated_at = datetime.utcnow()
                db.session.add(score)
                db.session.commit()
                flash("Score saved.", "success")
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "error")
        students = db.session.query(Student, User).join(User, Student.user_id == User.id).filter(Student.school_id == sid).order_by(User.full_name).all()
        subjects = Subject.query.filter_by(school_id=sid).order_by(Subject.name).all()
        scores = db.session.query(Score, Student, User, Subject).join(Student, Score.student_id == Student.id).join(User, Student.user_id == User.id).join(Subject, Score.subject_id == Subject.id).filter(Score.school_id == sid).order_by(Score.updated_at.desc()).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>Examination Scores</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ csrf() }}<label>Student<select name="student_id" required>{% for st,u in students %}<option value="{{ st.id }}">{{ u.full_name }} - {{ st.admission_no }}</option>{% endfor %}</select></label><label>Subject<select name="subject_id" required>{% for s in subjects %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}</select></label>{{ field('Class Score / 50','class_score','number') }}{{ field('Exam Score / 50','exam_score','number') }}{{ field('Term','term', value=school.term) }}{{ field('Academic Year','academic_year', value=school.academic_year) }}{{ field('Position','position', placeholder='1st, 2nd, 3rd') }}{{ field('Conduct','conduct', placeholder='Excellent, Good') }}{{ field('Remarks','remarks') }}<button class="btn green">Save Score</button></form></article><article class="card"><table><tr><th>Student</th><th>Subject</th><th>Total</th><th>Grade</th><th>Meaning</th><th>Remarks</th></tr>{% for sc,st,u,sub in scores %}{% set total=sc.class_score+sc.exam_score %}{% set info=grade_info(total) %}<tr><td>{{ u.full_name }} <span class="muted">{{ st.admission_no }}</span></td><td>{{ sub.name }}</td><td>{{ total }}</td><td>{{ info.grade }}</td><td>{{ info.interpretation }}</td><td>{{ sc.remarks }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Examination Scores", students=students, subjects=subjects, scores=scores)

    def period_record(model, student_id, school):
        return model.query.filter_by(student_id=student_id, term=school.term, academic_year=school.academic_year).first()

    @app.route("/attendance", methods=["GET", "POST"])
    @login_required("school_admin")
    @school_required
    def attendance():
        user = current_user()
        school = current_school()
        sid = user.school_id
        if request.method == "POST":
            rec = Attendance.query.filter_by(student_id=request.form["student_id"], term=request.form.get("term") or school.term, academic_year=request.form.get("academic_year") or school.academic_year).first() or Attendance(school_id=sid, student_id=request.form["student_id"], term=request.form.get("term") or school.term, academic_year=request.form.get("academic_year") or school.academic_year)
            rec.present_days = int(request.form.get("present_days") or 0)
            rec.total_days = int(request.form.get("total_days") or 0)
            db.session.add(rec)
            db.session.commit()
            flash("Attendance saved.", "success")
        students = db.session.query(Student, User).join(User, Student.user_id == User.id).filter(Student.school_id == sid).order_by(User.full_name).all()
        records = db.session.query(Attendance, Student, User).join(Student, Attendance.student_id == Student.id).join(User, Student.user_id == User.id).filter(Attendance.school_id == sid).order_by(User.full_name).all()
        return simple_period_page("Attendance", students, records, school, "attendance")

    @app.route("/fees", methods=["GET", "POST"])
    @login_required("school_admin")
    @school_required
    def fees():
        user = current_user()
        school = current_school()
        sid = user.school_id
        if request.method == "POST":
            rec = Fee.query.filter_by(student_id=request.form["student_id"], term=request.form.get("term") or school.term, academic_year=request.form.get("academic_year") or school.academic_year).first() or Fee(school_id=sid, student_id=request.form["student_id"], term=request.form.get("term") or school.term, academic_year=request.form.get("academic_year") or school.academic_year)
            rec.amount_due = float(request.form.get("amount_due") or 0)
            rec.amount_paid = float(request.form.get("amount_paid") or 0)
            db.session.add(rec)
            db.session.commit()
            flash("Fee record saved.", "success")
        students = db.session.query(Student, User).join(User, Student.user_id == User.id).filter(Student.school_id == sid).order_by(User.full_name).all()
        records = db.session.query(Fee, Student, User).join(Student, Fee.student_id == Student.id).join(User, Student.user_id == User.id).filter(Fee.school_id == sid).order_by(User.full_name).all()
        return simple_period_page("Fees", students, records, school, "fees")

    def simple_period_page(title, students, records, school, kind):
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>{{ title }}</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ csrf() }}<label>Student<select name="student_id" required>{% for st,u in students %}<option value="{{ st.id }}">{{ u.full_name }} - {{ st.admission_no }}</option>{% endfor %}</select></label>{% if kind == 'attendance' %}{{ field('Days Present','present_days','number') }}{{ field('Total School Days','total_days','number') }}{% else %}{{ field('Amount Due','amount_due','number') }}{{ field('Amount Paid','amount_paid','number') }}{% endif %}{{ field('Term','term', value=school.term) }}{{ field('Academic Year','academic_year', value=school.academic_year) }}<button class="btn green">Save</button></form></article><article class="card"><table>{% if kind == 'attendance' %}<tr><th>Student</th><th>Admission No</th><th>Present</th><th>Term</th></tr>{% for r,st,u in records %}<tr><td>{{ u.full_name }}</td><td>{{ st.admission_no }}</td><td>{{ r.present_days }}/{{ r.total_days }}</td><td>{{ r.term }} {{ r.academic_year }}</td></tr>{% endfor %}{% else %}<tr><th>Student</th><th>Admission No</th><th>Due</th><th>Paid</th><th>Balance</th></tr>{% for r,st,u in records %}<tr><td>{{ u.full_name }}</td><td>{{ st.admission_no }}</td><td>{{ r.amount_due }}</td><td>{{ r.amount_paid }}</td><td>{{ r.amount_due - r.amount_paid }}</td></tr>{% endfor %}{% endif %}</table></article></section></div></main>""", title=title, students=students, records=records, school=school, kind=kind)

    @app.route("/announcements", methods=["GET", "POST"])
    @login_required("school_admin", "teacher", "student")
    @school_required
    def announcements():
        user = current_user()
        sid = user.school_id
        if request.method == "POST" and user.role == "school_admin":
            db.session.add(Announcement(school_id=sid, title=request.form["title"], body=request.form["body"], audience=request.form.get("audience", "all"), created_by=user.id))
            db.session.commit()
            flash("Notice published.", "success")
        rows = Announcement.query.filter(Announcement.school_id == sid, Announcement.audience.in_(["all", user.role])).order_by(Announcement.created_at.desc()).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid">{% if user.role == 'school_admin' %}<article class="card"><h2>Publish Notice</h2><form method="post" class="grid cols-2">{{ csrf() }}{{ field('Title','title') }}<label>Audience<select name="audience"><option value="all">Everyone</option><option value="teacher">Teachers</option><option value="student">Students</option></select></label><label style="grid-column:1/-1">Message<textarea name="body" required></textarea></label><button class="btn green">Publish</button></form></article>{% endif %}<article class="card"><h2>School Notices</h2><table><tr><th>Title</th><th>Message</th><th>Audience</th><th>Date</th></tr>{% for r in rows %}<tr><td><b>{{ r.title }}</b></td><td>{{ r.body }}</td><td>{{ r.audience|title }}</td><td>{{ r.created_at.strftime('%Y-%m-%d') }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Notices", rows=rows)

    @app.route("/timetable", methods=["GET", "POST"])
    @login_required("school_admin", "teacher", "student")
    @school_required
    def timetable():
        user = current_user()
        sid = user.school_id
        if request.method == "POST" and user.role == "school_admin":
            db.session.add(Timetable(school_id=sid, class_id=request.form.get("class_id") or None, subject_id=request.form.get("subject_id") or None, teacher_id=request.form.get("teacher_id") or None, day=request.form["day"], start_time=request.form["start_time"], end_time=request.form["end_time"], room=request.form.get("room", "")))
            db.session.commit()
            flash("Timetable period added.", "success")
        classes = ClassRoom.query.filter_by(school_id=sid).order_by(ClassRoom.name).all()
        subjects = Subject.query.filter_by(school_id=sid).order_by(Subject.name).all()
        teachers = User.query.filter_by(school_id=sid, role="teacher").order_by(User.full_name).all()
        rows = db.session.query(Timetable, ClassRoom, Subject, User).outerjoin(ClassRoom, Timetable.class_id == ClassRoom.id).outerjoin(Subject, Timetable.subject_id == Subject.id).outerjoin(User, Timetable.teacher_id == User.id).filter(Timetable.school_id == sid).order_by(Timetable.day, Timetable.start_time).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid">{% if user.role == 'school_admin' %}<article class="card"><h2>Build Timetable</h2><form method="post" class="grid cols-4">{{ csrf() }}<label>Class<select name="class_id"><option value="">General</option>{% for c in classes %}<option value="{{ c.id }}">{{ c.name }}</option>{% endfor %}</select></label><label>Subject<select name="subject_id"><option value="">None</option>{% for s in subjects %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}</select></label><label>Teacher<select name="teacher_id"><option value="">None</option>{% for t in teachers %}<option value="{{ t.id }}">{{ t.full_name }}</option>{% endfor %}</select></label><label>Day<select name="day"><option>Monday</option><option>Tuesday</option><option>Wednesday</option><option>Thursday</option><option>Friday</option><option>Saturday</option></select></label>{{ field('Start Time','start_time','time') }}{{ field('End Time','end_time','time') }}{{ field('Room','room') }}<button class="btn green">Add Period</button></form></article>{% endif %}<article class="card"><h2>Timetable</h2><table><tr><th>Day</th><th>Time</th><th>Class</th><th>Subject</th><th>Teacher</th><th>Room</th></tr>{% for r,c,s,t in rows %}<tr><td>{{ r.day }}</td><td>{{ r.start_time }} - {{ r.end_time }}</td><td>{{ c.name if c else 'General' }}</td><td>{{ s.name if s else '-' }}</td><td>{{ t.full_name if t else '-' }}</td><td>{{ r.room }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Timetable", classes=classes, subjects=subjects, teachers=teachers, rows=rows)

    @app.route("/calendar", methods=["GET", "POST"])
    @login_required("school_admin", "teacher", "student")
    @school_required
    def calendar():
        user = current_user()
        sid = user.school_id
        if request.method == "POST" and user.role == "school_admin":
            try:
                db.session.add(SchoolEvent(school_id=sid, title=request.form["title"].strip(), event_date=datetime.strptime(request.form["event_date"], "%Y-%m-%d").date(), audience=request.form.get("audience", "all"), notes=request.form.get("notes", ""), created_by=user.id))
                db.session.commit()
                flash("Calendar event added.", "success")
            except ValueError:
                db.session.rollback()
                flash("Please enter a valid event date.", "error")
        rows = SchoolEvent.query.filter(SchoolEvent.school_id == sid, SchoolEvent.audience.in_(["all", user.role])).order_by(SchoolEvent.event_date.desc()).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid">{% if user.role == 'school_admin' %}<article class="card"><h2>School Calendar</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ csrf() }}{{ field('Event Title','title', required=true) }}{{ field('Date','event_date','date', required=true) }}<label>Audience<select name="audience"><option value="all">Everyone</option><option value="teacher">Teachers</option><option value="student">Students</option></select></label>{{ field('Notes','notes') }}<button class="btn green">Add Event</button></form></article>{% endif %}<article class="card"><h2>Academic Calendar</h2><table><tr><th>Date</th><th>Event</th><th>Audience</th><th>Notes</th></tr>{% for r in rows %}<tr><td>{{ r.event_date.strftime('%d %B %Y') }}</td><td><b>{{ r.title }}</b></td><td>{{ r.audience|title }}</td><td>{{ r.notes }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="School Calendar", rows=rows)

    @app.route("/library", methods=["GET", "POST"])
    @login_required("school_admin", "teacher", "student")
    @school_required
    def library():
        user = current_user()
        sid = user.school_id
        if request.method == "POST" and user.role == "school_admin":
            db.session.add(LibraryResource(school_id=sid, title=request.form["title"], category=request.form.get("category", ""), location=request.form.get("location", ""), copies=int(request.form.get("copies") or 1), notes=request.form.get("notes", "")))
            db.session.commit()
            flash("Library resource added.", "success")
        rows = LibraryResource.query.filter_by(school_id=sid).order_by(LibraryResource.title).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid">{% if user.role == 'school_admin' %}<article class="card"><h2>Library Resources</h2><form method="post" class="grid cols-3">{{ csrf() }}{{ field('Title','title') }}{{ field('Category','category', placeholder='Textbook, Reader, Device') }}{{ field('Location','location', placeholder='Library shelf A') }}{{ field('Copies','copies','number', value='1') }}{{ field('Notes','notes') }}<button class="btn green">Add Resource</button></form></article>{% endif %}<article class="card"><h2>Library Catalogue</h2><table><tr><th>Title</th><th>Category</th><th>Location</th><th>Copies</th><th>Notes</th></tr>{% for r in rows %}<tr><td>{{ r.title }}</td><td>{{ r.category }}</td><td>{{ r.location }}</td><td>{{ r.copies }}</td><td>{{ r.notes }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Library", rows=rows)

    @app.route("/my-results")
    @login_required("student")
    @school_required
    def student_results():
        user = current_user()
        school = current_school()
        student = Student.query.filter_by(user_id=user.id).first()
        rows = db.session.query(Score, Subject).join(Subject, Score.subject_id == Subject.id).filter(Score.student_id == student.id).order_by(Subject.name).all() if student else []
        attendance = period_record(Attendance, student.id, school) if student else None
        fees = period_record(Fee, student.id, school) if student else None
        total = sum((sc.class_score + sc.exam_score) for sc, _ in rows)
        average = round(total / len(rows), 2) if rows else 0
        conduct = next((sc.conduct for sc, _ in rows if sc.conduct), "")
        position = next((sc.position for sc, _ in rows if sc.position), "")
        overall = grade_info(average)
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="card report-card"><div class="report-head">{% if school.crest %}<img class="crest report-crest" src="{{ url_for('uploads', filename=school.crest) }}" alt="crest">{% endif %}<div class="report-title"><h2>{{ school.name }}</h2><p>{{ school.address }}</p><p>{{ school.motto }}</p><strong>Terminal Report Card</strong></div><button class="btn no-print" onclick="window.print()">Print Result</button></div><div class="grid cols-3 report-meta"><p><b>Student:</b> {{ user.full_name }}</p><p><b>Admission No:</b> {{ student.admission_no if student else '-' }}</p><p><b>Class:</b> {{ student_class.name if student_class else '-' }}</p><p><b>Term:</b> {{ school.term }}</p><p><b>Academic Year:</b> {{ school.academic_year }}</p><p><b>Position:</b> {{ position or '-' }}</p></div><table><tr><th>Subject</th><th>Class Score / 50</th><th>Exam / 50</th><th>Total / 100</th><th>Grade</th><th>Meaning</th><th>Teacher Remarks</th></tr>{% for sc,sub in rows %}{% set subject_total=sc.class_score+sc.exam_score %}{% set info=grade_info(subject_total) %}<tr><td>{{ sub.name }}</td><td>{{ sc.class_score }}</td><td>{{ sc.exam_score }}</td><td>{{ subject_total }}</td><td><b>{{ info.grade }}</b></td><td>{{ info.interpretation }}</td><td>{{ sc.remarks }}</td></tr>{% endfor %}</table><div class="grid cols-4" style="margin-top:18px"><article class="card"><b>Total Marks</b><br>{{ report_total }}</article><article class="card"><b>Average</b><br>{{ average }}%</article><article class="card"><b>Overall Grade</b><br>{{ overall.grade }} - {{ overall.interpretation }}</article><article class="card"><b>Attendance</b><br>{{ attendance.present_days if attendance else 0 }}/{{ attendance.total_days if attendance else 0 }}</article></div><div class="grid cols-3" style="margin-top:18px"><article class="card"><b>Conduct</b><br>{{ conduct or 'Good' }}</article><article class="card"><b>Fee Balance</b><br>{{ ((fees.amount_due - fees.amount_paid) if fees else 0) }}</article><article class="card"><b>Next Term</b><br>{{ school.term }}</article></div><section class="signature-row"><div><b>Head's Signature</b>{% if school.head_signature %}<img class="signature-img" src="{{ url_for('uploads', filename=school.head_signature) }}" alt="signature">{% else %}<div class="signature-line"></div>{% endif %}<p>{{ school.head_name or 'Head of School' }}<br><span class="muted">{{ school.head_title or 'Head of School' }}</span></p></div><div><b>Printed</b><p>{{ now.strftime('%d %B %Y') }}</p></div></section><p class="muted no-print">This report card is printable and responsive for phone, tablet, and desktop viewing.</p></section></div></main>""", title="My Results", student=student, student_class=db.session.get(ClassRoom, student.class_id) if student and student.class_id else None, rows=rows, attendance=attendance, fees=fees, average=average, report_total=total, conduct=conduct, position=position, overall=overall)


app = create_app()

with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=Config.DEBUG)
