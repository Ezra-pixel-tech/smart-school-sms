from __future__ import annotations

import os
import secrets
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from flask import Flask, Response, abort, flash, redirect, render_template_string, request, send_from_directory, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, UniqueConstraint, event, or_, text
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
    "admin": {"system_admin", "school_admin", "accountant", "registrar", "librarian", "receptionist"},
    "teacher": {"teacher"},
    "student": {"student"},
    "parent": {"parent"},
}

ALLOWED_ROLES = frozenset({"system_admin", "school_admin", "teacher", "student",
                          "parent", "accountant", "registrar", "librarian", "receptionist"})
_LOGIN_ATTEMPTS: dict[str, list[datetime]] = {}


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
        os.getenv("DATABASE_URL",
                  f"sqlite:///{BASE_DIR / 'smart_schools_sms.db'}")
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = Config.SQLALCHEMY_ENGINE_OPTIONS
    app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY must be configured when FLASK_DEBUG=0")
    app.permanent_session_lifetime = timedelta(
        minutes=Config.PERMANENT_SESSION_LIFETIME_MINUTES)
    proxy_count = max(0, Config.TRUSTED_PROXY_COUNT)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxy_count,
                            x_proto=proxy_count, x_host=proxy_count)
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
    sms_api_url = db.Column(db.String(
        500), default="https://sms.nalosolutions.com/smsbackend/Resl_Nalo/send-message/")
    sms_api_key = db.Column(db.String(260), default="")
    sms_sender_id = db.Column(db.String(40), default="")
    academic_year = db.Column(db.String(40), default="")
    term = db.Column(db.String(40), default="")
    onboarded = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=True, index=True)
    role = db.Column(db.String(30), nullable=False, index=True)
    full_name = db.Column(db.String(160), nullable=False)
    username = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(160), default="")
    phone = db.Column(db.String(80), default="")
    active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    must_change_password = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint(
        "school_id", "username", name="uq_user_school_username"),)


class ClassRoom(db.Model):
    __tablename__ = "classes"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey(
        "users.id", ondelete="SET NULL"), nullable=True)
    __table_args__ = (UniqueConstraint(
        "school_id", "name", name="uq_class_school_name"),)


class Subject(db.Model):
    __tablename__ = "subjects"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(40), default="")
    teacher_id = db.Column(db.Integer, db.ForeignKey(
        "users.id", ondelete="SET NULL"), nullable=True)
    __table_args__ = (UniqueConstraint(
        "school_id", "name", name="uq_subject_school_name"),)


class TeacherAssignment(db.Model):
    __tablename__ = "teacher_assignments"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=False, index=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey(
        "users.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id = db.Column(db.Integer, db.ForeignKey(
        "classes.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_id = db.Column(db.Integer, db.ForeignKey(
        "subjects.id", ondelete="CASCADE"), nullable=True, index=True)
    section = db.Column(db.String(80), default="", nullable=False, index=True)
    academic_year = db.Column(
        db.String(40), default="", nullable=False, index=True)
    term = db.Column(db.String(40), default="", nullable=False, index=True)
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("teacher_id", "class_id", "subject_id",
                      "section", "academic_year", "term", name="uq_teacher_assignment_period"),)


class Student(db.Model):
    __tablename__ = "students"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey(
        "users.id", ondelete="CASCADE"), nullable=False, unique=True)
    class_id = db.Column(db.Integer, db.ForeignKey(
        "classes.id", ondelete="SET NULL"), nullable=True, index=True)
    admission_no = db.Column(db.String(80), nullable=False)
    guardian_name = db.Column(db.String(160), default="")
    guardian_phone = db.Column(db.String(80), default="")
    guardian_email = db.Column(db.String(160), default="")
    promotion_note = db.Column(db.String(260), default="")
    promoted_at = db.Column(db.DateTime, nullable=True)
    __table_args__ = (UniqueConstraint(
        "school_id", "admission_no", name="uq_student_school_admission"),)


class ParentStudent(db.Model):
    __tablename__ = "parent_students"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey(
        "users.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey(
        "students.id", ondelete="CASCADE"), nullable=False, index=True)
    relationship = db.Column(db.String(40), default="Guardian", nullable=False)
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint(
        "parent_id", "student_id", name="uq_parent_student"),)


class ParentReportPayment(db.Model):
    __tablename__ = "parent_report_payments"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey(
        "users.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey(
        "students.id", ondelete="CASCADE"), nullable=False, index=True)
    academic_year = db.Column(db.String(40), nullable=False, index=True)
    term = db.Column(db.String(40), nullable=False, index=True)
    reference = db.Column(db.String(120), nullable=False, unique=True, index=True)
    amount_subunit = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(30), default="pending", nullable=False, index=True)
    authorization_url = db.Column(db.String(500), default="")
    provider_transaction_id = db.Column(db.String(80), default="")
    paid_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Score(db.Model):
    __tablename__ = "scores"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey(
        "students.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_id = db.Column(db.Integer, db.ForeignKey(
        "subjects.id", ondelete="CASCADE"), nullable=False, index=True)
    class_score = db.Column(db.Float, default=0)
    exam_score = db.Column(db.Float, default=0)
    conduct = db.Column(db.String(120), default="")
    position = db.Column(db.String(40), default="")
    remarks = db.Column(db.String(220), default="")
    term = db.Column(db.String(40), default="", index=True)
    academic_year = db.Column(db.String(40), default="", index=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey(
        "users.id", ondelete="SET NULL"), nullable=True)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("student_id", "subject_id",
                      "term", "academic_year", name="uq_score_period"),)


class Attendance(db.Model):
    __tablename__ = "attendance"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey(
        "students.id", ondelete="CASCADE"), nullable=False, index=True)
    present_days = db.Column(db.Integer, default=0)
    total_days = db.Column(db.Integer, default=0)
    term = db.Column(db.String(40), default="")
    academic_year = db.Column(db.String(40), default="")
    __table_args__ = (UniqueConstraint("student_id", "term",
                      "academic_year", name="uq_attendance_period"),)


class StudentReportDetail(db.Model):
    __tablename__ = "student_report_details"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    academic_year = db.Column(db.String(40), default="", nullable=False, index=True)
    term = db.Column(db.String(40), default="", nullable=False, index=True)
    number_on_roll = db.Column(db.Integer, default=0)
    next_term_begins = db.Column(db.Date, nullable=True)
    absent_days = db.Column(db.Integer, default=0)
    late_days = db.Column(db.Integer, default=0)
    class_teacher_remarks = db.Column(db.Text, default="")
    head_teacher_remarks = db.Column(db.Text, default="")
    interest = db.Column(db.String(160), default="")
    attitude = db.Column(db.String(160), default="")
    arrears = db.Column(db.Float, default=0)
    tuition_fees = db.Column(db.Float, default=0)
    pta_dues = db.Column(db.Float, default=0)
    medical_dues = db.Column(db.Float, default=0)
    building_fund = db.Column(db.Float, default=0)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("student_id", "term", "academic_year", name="uq_student_report_detail_period"),)


class Fee(db.Model):
    __tablename__ = "fees"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey(
        "students.id", ondelete="CASCADE"), nullable=False, index=True)
    amount_due = db.Column(db.Float, default=0)
    amount_paid = db.Column(db.Float, default=0)
    term = db.Column(db.String(40), default="")
    academic_year = db.Column(db.String(40), default="")
    __table_args__ = (UniqueConstraint("student_id", "term",
                      "academic_year", name="uq_fee_period"),)


class Announcement(db.Model):
    __tablename__ = "announcements"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    body = db.Column(db.Text, nullable=False)
    audience = db.Column(db.String(30), default="all", index=True)
    created_by = db.Column(db.Integer, db.ForeignKey(
        "users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class Timetable(db.Model):
    __tablename__ = "timetable"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id = db.Column(db.Integer, db.ForeignKey(
        "classes.id", ondelete="SET NULL"), nullable=True)
    subject_id = db.Column(db.Integer, db.ForeignKey(
        "subjects.id", ondelete="SET NULL"), nullable=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey(
        "users.id", ondelete="SET NULL"), nullable=True)
    day = db.Column(db.String(20), nullable=False)
    start_time = db.Column(db.String(10), nullable=False)
    end_time = db.Column(db.String(10), nullable=False)
    room = db.Column(db.String(80), default="")


class LibraryResource(db.Model):
    __tablename__ = "library_resources"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False, index=True)
    category = db.Column(db.String(80), default="")
    location = db.Column(db.String(120), default="")
    copies = db.Column(db.Integer, default=1)
    notes = db.Column(db.String(260), default="")
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False)


class SchoolEvent(db.Model):
    __tablename__ = "school_events"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    event_date = db.Column(db.Date, nullable=False, index=True)
    audience = db.Column(db.String(30), default="all", index=True)
    notes = db.Column(db.String(260), default="")
    created_by = db.Column(db.Integer, db.ForeignKey(
        "users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False)


class Communication(db.Model):
    __tablename__ = "communications"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=True, index=True)
    channel = db.Column(db.String(20), nullable=False, index=True)
    audience = db.Column(db.String(40), nullable=False, index=True)
    recipient = db.Column(db.String(180), default="")
    subject = db.Column(db.String(180), default="")
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(40), default="recorded", nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey(
        "users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey(
        "schools.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey(
        "users.id", ondelete="SET NULL"), nullable=True)
    username = db.Column(db.String(100), default="")
    action = db.Column(db.String(120), nullable=False, index=True)
    details = db.Column(db.Text, default="")
    ip_address = db.Column(db.String(80), default="")
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False, index=True)


Index("ix_scores_student_period", Score.student_id,
      Score.term, Score.academic_year)
Index("ix_users_role_school", User.school_id, User.role)
Index("ix_students_school_class", Student.school_id, Student.class_id)
Index("ix_parent_students_scope", ParentStudent.school_id,
      ParentStudent.parent_id, ParentStudent.student_id)
Index("ix_parent_report_payment_unlock", ParentReportPayment.school_id,
      ParentReportPayment.parent_id, ParentReportPayment.student_id,
      ParentReportPayment.academic_year, ParentReportPayment.term,
      ParentReportPayment.status)
Index("ix_attendance_student_period", Attendance.student_id,
      Attendance.term, Attendance.academic_year)
Index("ix_fees_student_period", Fee.student_id, Fee.term, Fee.academic_year)
Index("ix_audit_school_created", AuditLog.school_id, AuditLog.created_at)


@event.listens_for(Engine, "connect")
def set_database_pragmas(dbapi_connection, _):
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_db() -> None:
    db.create_all()
    ensure_compatibility_migrations()
    bootstrap_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    if bootstrap_password and not User.query.filter_by(role="system_admin", username="admin").first():
        admin = User(
            role="system_admin",
            full_name="System Administrator",
            username="admin",
            password_hash=generate_password_hash(bootstrap_password),
            must_change_password=True,
        )
        db.session.add(admin)
        db.session.commit()


def ensure_compatibility_migrations() -> None:
    if db.engine.dialect.name == "postgresql":
        for statement in [
            "ALTER TABLE schools ADD COLUMN IF NOT EXISTS head_name VARCHAR(160) DEFAULT ''",
            "ALTER TABLE schools ADD COLUMN IF NOT EXISTS head_title VARCHAR(100) DEFAULT 'Head of School'",
            "ALTER TABLE schools ADD COLUMN IF NOT EXISTS head_signature VARCHAR(260) DEFAULT ''",
            "ALTER TABLE schools ADD COLUMN IF NOT EXISTS sms_api_url VARCHAR(500) DEFAULT ''",
            "ALTER TABLE schools ADD COLUMN IF NOT EXISTS sms_api_key VARCHAR(260) DEFAULT ''",
            "ALTER TABLE schools ADD COLUMN IF NOT EXISTS sms_sender_id VARCHAR(40) DEFAULT ''",
            "ALTER TABLE students ADD COLUMN IF NOT EXISTS guardian_email VARCHAR(160) DEFAULT ''",
            "ALTER TABLE students ADD COLUMN IF NOT EXISTS promotion_note VARCHAR(260) DEFAULT ''",
            "ALTER TABLE students ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMP",
            "ALTER TABLE scores ADD COLUMN IF NOT EXISTS conduct VARCHAR(120) DEFAULT ''",
            "ALTER TABLE scores ADD COLUMN IF NOT EXISTS position VARCHAR(40) DEFAULT ''",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS school_id INTEGER",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS user_id INTEGER",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS username VARCHAR(100) DEFAULT ''",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS details TEXT DEFAULT ''",
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS ip_address VARCHAR(80) DEFAULT ''",
            "ALTER TABLE communications ADD COLUMN IF NOT EXISTS school_id INTEGER",
            "ALTER TABLE communications ADD COLUMN IF NOT EXISTS recipient VARCHAR(180) DEFAULT ''",
            "ALTER TABLE communications ADD COLUMN IF NOT EXISTS status VARCHAR(40) DEFAULT 'recorded'",
            "ALTER TABLE communications ADD COLUMN IF NOT EXISTS created_by INTEGER",
            "ALTER TABLE teacher_assignments ADD COLUMN IF NOT EXISTS section VARCHAR(80) DEFAULT '' NOT NULL",
            "ALTER TABLE teacher_assignments ADD COLUMN IF NOT EXISTS academic_year VARCHAR(40) DEFAULT '' NOT NULL",
            "ALTER TABLE teacher_assignments ADD COLUMN IF NOT EXISTS term VARCHAR(40) DEFAULT '' NOT NULL",
        ]:
            db.session.execute(text(statement))
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS teacher_assignments (
                id SERIAL PRIMARY KEY, school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
                teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
                subject_id INTEGER REFERENCES subjects(id) ON DELETE CASCADE,
                section VARCHAR(80) NOT NULL DEFAULT '',
                academic_year VARCHAR(40) NOT NULL DEFAULT '',
                term VARCHAR(40) NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_teacher_class_subject UNIQUE (teacher_id, class_id, subject_id)
            )
        """))
        db.session.commit()
        return
    if db.engine.dialect.name != "sqlite":
        return
    user_columns = {row[1] for row in db.session.execute(
        text("PRAGMA table_info(users)")).fetchall()}
    if "must_change_password" not in user_columns:
        db.session.execute(text(
            "ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT 1 NOT NULL"))
    school_columns = {row[1] for row in db.session.execute(
        text("PRAGMA table_info(schools)")).fetchall()}
    for ddl in [
        ("head_name", "ALTER TABLE schools ADD COLUMN head_name VARCHAR(160) DEFAULT ''"),
        ("head_title", "ALTER TABLE schools ADD COLUMN head_title VARCHAR(100) DEFAULT 'Head of School'"),
        ("head_signature", "ALTER TABLE schools ADD COLUMN head_signature VARCHAR(260) DEFAULT ''"),
        ("sms_api_url", "ALTER TABLE schools ADD COLUMN sms_api_url VARCHAR(500) DEFAULT ''"),
        ("sms_api_key", "ALTER TABLE schools ADD COLUMN sms_api_key VARCHAR(260) DEFAULT ''"),
        ("sms_sender_id", "ALTER TABLE schools ADD COLUMN sms_sender_id VARCHAR(40) DEFAULT ''"),
    ]:
        if ddl[0] not in school_columns:
            db.session.execute(text(ddl[1]))
    student_columns = {row[1] for row in db.session.execute(
        text("PRAGMA table_info(students)")).fetchall()}
    for name, ddl in [
        ("promotion_note", "ALTER TABLE students ADD COLUMN promotion_note VARCHAR(260) DEFAULT ''"),
        ("promoted_at", "ALTER TABLE students ADD COLUMN promoted_at DATETIME"),
    ]:
        if name not in student_columns:
            db.session.execute(text(ddl))
    score_columns = {row[1] for row in db.session.execute(
        text("PRAGMA table_info(scores)")).fetchall()}
    for ddl in [
        ("conduct", "ALTER TABLE scores ADD COLUMN conduct VARCHAR(120) DEFAULT ''"),
        ("position", "ALTER TABLE scores ADD COLUMN position VARCHAR(40) DEFAULT ''"),
    ]:
        if ddl[0] not in score_columns:
            db.session.execute(text(ddl[1]))
    student_columns = {row[1] for row in db.session.execute(
        text("PRAGMA table_info(students)")).fetchall()}
    if "guardian_email" not in student_columns:
        db.session.execute(
            text("ALTER TABLE students ADD COLUMN guardian_email VARCHAR(160) DEFAULT ''"))
    existing_tables = {row[0] for row in db.session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
    if "audit_logs" in existing_tables:
        audit_columns = {row[1] for row in db.session.execute(
            text("PRAGMA table_info(audit_logs)")).fetchall()}
        for ddl in [
            ("school_id", "ALTER TABLE audit_logs ADD COLUMN school_id INTEGER"),
            ("user_id", "ALTER TABLE audit_logs ADD COLUMN user_id INTEGER"),
            ("username", "ALTER TABLE audit_logs ADD COLUMN username VARCHAR(100) DEFAULT ''"),
            ("details", "ALTER TABLE audit_logs ADD COLUMN details TEXT DEFAULT ''"),
            ("ip_address", "ALTER TABLE audit_logs ADD COLUMN ip_address VARCHAR(80) DEFAULT ''"),
        ]:
            if ddl[0] not in audit_columns:
                db.session.execute(text(ddl[1]))
    if "communications" in existing_tables:
        comm_columns = {row[1] for row in db.session.execute(
            text("PRAGMA table_info(communications)")).fetchall()}
        for ddl in [
            ("school_id", "ALTER TABLE communications ADD COLUMN school_id INTEGER"),
            ("recipient", "ALTER TABLE communications ADD COLUMN recipient VARCHAR(180) DEFAULT ''"),
            ("status", "ALTER TABLE communications ADD COLUMN status VARCHAR(40) DEFAULT 'recorded'"),
            ("created_by", "ALTER TABLE communications ADD COLUMN created_by INTEGER"),
        ]:
            if ddl[0] not in comm_columns:
                db.session.execute(text(ddl[1]))
    if "school_events" not in existing_tables:
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
        db.session.execute(
            text("CREATE INDEX ix_school_events_school_id ON school_events (school_id)"))
        db.session.execute(
            text("CREATE INDEX ix_school_events_event_date ON school_events (event_date)"))
        db.session.execute(
            text("CREATE INDEX ix_school_events_audience ON school_events (audience)"))
    if "teacher_assignments" not in existing_tables:
        db.session.execute(text("""
            CREATE TABLE teacher_assignments (
                id INTEGER NOT NULL PRIMARY KEY, school_id INTEGER NOT NULL,
                teacher_id INTEGER NOT NULL, class_id INTEGER NOT NULL, subject_id INTEGER,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(school_id) REFERENCES schools(id) ON DELETE CASCADE,
                FOREIGN KEY(teacher_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(class_id) REFERENCES classes(id) ON DELETE CASCADE,
                FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                UNIQUE(teacher_id, class_id, subject_id)
            )
        """))
    assignment_columns = {row[1] for row in db.session.execute(
        text("PRAGMA table_info(teacher_assignments)")).fetchall()}
    for name, ddl in [
        ("section", "ALTER TABLE teacher_assignments ADD COLUMN section VARCHAR(80) DEFAULT '' NOT NULL"),
        ("academic_year", "ALTER TABLE teacher_assignments ADD COLUMN academic_year VARCHAR(40) DEFAULT '' NOT NULL"),
        ("term", "ALTER TABLE teacher_assignments ADD COLUMN term VARCHAR(40) DEFAULT '' NOT NULL"),
    ]:
        if name not in assignment_columns:
            db.session.execute(text(ddl))
    db.session.execute(text("""
        INSERT OR IGNORE INTO teacher_assignments (school_id, teacher_id, class_id, subject_id, created_at)
        SELECT school_id, teacher_id, id, NULL, CURRENT_TIMESTAMP FROM classes WHERE teacher_id IS NOT NULL
    """))
    db.session.commit()


def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None


def current_school():
    user = current_user()
    return db.session.get(School, user.school_id) if user and user.school_id else None


def log_action(action: str, details: str = "") -> None:
    user = current_user()
    db.session.add(AuditLog(
        school_id=user.school_id if user else None,
        user_id=user.id if user else None,
        username=user.username if user else "",
        action=action,
        details=details,
        ip_address=request.headers.get(
            "X-Forwarded-For", request.remote_addr or ""),
    ))


def fmt_dt(value, fmt="%Y-%m-%d"):
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value[:16]
    return value.strftime(fmt)


def safe_number(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def audience_recipients(school_id: int, audience: str, channel: str, manual_recipient: str = "") -> list[str]:
    if manual_recipient.strip():
        return list(dict.fromkeys(value.strip() for value in manual_recipient.split(",") if value.strip()))

    contact_field = User.phone if channel == "sms" else User.email
    recipients = []
    if audience in {"all", "students", "teachers", "users"}:
        query = User.query.filter_by(school_id=school_id, active=True)
        if audience == "students":
            query = query.filter_by(role="student")
        elif audience == "teachers":
            query = query.filter_by(role="teacher")
        recipients.extend(row[0] for row in query.with_entities(
            contact_field).all() if row[0])
    if audience in {"all", "parents"}:
        contact_field = Student.guardian_phone if channel == "sms" else Student.guardian_email
        recipients.extend(row[0] for row in Student.query.filter_by(
            school_id=school_id).with_entities(contact_field).all() if row[0])
    return list(dict.fromkeys(recipients))


def deliver_communication(item: Communication) -> str:
    if not item.recipient:
        return "recorded"
    try:
        if item.channel == "email" and os.getenv("SMTP_HOST"):
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = item.subject or "School notice"
            msg["From"] = os.getenv("SMTP_FROM", os.getenv(
                "SMTP_USER", "school@example.com"))
            msg["To"] = item.recipient
            msg.set_content(item.message)
            with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587")), timeout=12) as smtp:
                if os.getenv("SMTP_TLS", "1") == "1":
                    smtp.starttls()
                if os.getenv("SMTP_USER"):
                    smtp.login(os.getenv("SMTP_USER"),
                               os.getenv("SMTP_PASSWORD", ""))
                smtp.send_message(msg)
            return "sent"
        school = db.session.get(
            School, item.school_id) if item.school_id else current_school()
        sms_api_url = school.sms_api_url if school and school.sms_api_url else os.getenv(
            "SMS_API_URL")
        if item.channel == "sms" and sms_api_url:
            from urllib.request import Request, urlopen

            sms_sender_id = school.sms_sender_id if school and school.sms_sender_id else os.getenv(
                "NALO_SMS_SENDER_ID", os.getenv("SMS_SENDER_ID", "School"))
            if "nalosolutions.com" in sms_api_url:
                from urllib.parse import urlencode

                username = os.getenv("NALO_SMS_USERNAME", "")
                password = os.getenv("NALO_SMS_PASSWORD", "")
                if not username or not password:
                    return "failed: Nalo credentials missing"
                payload = urlencode({"username": username, "password": password, "msisdn": item.recipient,
                                    "message": item.message, "sender_id": sms_sender_id[:11]}).encode()
                urlopen(Request(sms_api_url, data=payload), timeout=12).read()
            else:
                from urllib.parse import urlencode

                payload = urlencode(
                    {"to": item.recipient, "message": item.message}).encode()
                urlopen(sms_api_url, data=payload, timeout=12).read()
            return "sent"
    except Exception as exc:
        return f"failed: {exc.__class__.__name__}"
    return "recorded"


def delete_school_records(school_id: int) -> None:
    student_ids = [row[0] for row in db.session.query(
        Student.id).filter_by(school_id=school_id).all()]
    user_ids = [row[0] for row in db.session.query(
        User.id).filter_by(school_id=school_id).all()]
    for model in [Score, Attendance, Fee, StudentReportDetail]:
        if student_ids:
            model.query.filter(model.student_id.in_(
                student_ids)).delete(synchronize_session=False)
    ParentReportPayment.query.filter_by(
        school_id=school_id).delete(synchronize_session=False)
    ParentStudent.query.filter_by(
        school_id=school_id).delete(synchronize_session=False)
    for model in [Timetable, Announcement, SchoolEvent, LibraryResource, Communication, AuditLog, Subject, ClassRoom, Student]:
        if hasattr(model, "school_id"):
            model.query.filter_by(school_id=school_id).delete(
                synchronize_session=False)
    if user_ids:
        User.query.filter(User.id.in_(user_ids)).delete(
            synchronize_session=False)
    school = db.session.get(School, school_id)
    if school:
        db.session.delete(school)


def role_label(role: str) -> str:
    return {"system_admin": "System Admin", "school_admin": "School Admin", "accountant": "Accountant", "registrar": "Registrar", "receptionist": "Receptionist", "librarian": "Librarian", "parent": "Parent", "teacher": "Teacher", "student": "Student"}.get(role, role.replace("_", " ").title())


def login_required(*roles):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for("login"))
            if user.must_change_password and request.endpoint not in {"change_password", "logout", "uploads"}:
                flash(
                    "Please change your temporary password before continuing.", "error")
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
    if request.endpoint == "paystack_webhook":
        return
    if request.method == "POST" and request.form.get("_csrf") != session.get("_csrf"):
        abort(400)


def report_fee_subunit() -> int:
    try:
        amount = Decimal(str(Config.PARENT_REPORT_FEE))
    except (InvalidOperation, TypeError):
        raise RuntimeError("PARENT_REPORT_FEE must be a valid amount")
    if amount < 0:
        raise RuntimeError("PARENT_REPORT_FEE cannot be negative")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def paystack_request(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    secret = Config.PAYSTACK_SECRET_KEY
    if not secret:
        raise RuntimeError("Paystack is not configured")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = Request(
        f"https://api.paystack.co{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError("The payment service could not be reached") from exc
    if not result.get("status"):
        raise RuntimeError(result.get("message") or "Paystack rejected the request")
    return result


def report_payment_for(parent_id: int, student_id: int, school: School):
    return ParentReportPayment.query.filter_by(
        school_id=school.id,
        parent_id=parent_id,
        student_id=student_id,
        academic_year=school.academic_year,
        term=school.term,
        amount_subunit=report_fee_subunit(),
        currency=Config.PAYSTACK_CURRENCY,
        status="success",
    ).order_by(ParentReportPayment.paid_at.desc()).first()


def verify_report_payment(payment: ParentReportPayment) -> bool:
    result = paystack_request(
        f"/transaction/verify/{quote(payment.reference, safe='')}")
    transaction = result.get("data") or {}
    valid = (
        transaction.get("status") == "success"
        and str(transaction.get("reference")) == payment.reference
        and int(transaction.get("amount") or -1) == payment.amount_subunit
        and str(transaction.get("currency") or "").upper() == payment.currency
    )
    payment.status = "success" if valid else "failed"
    payment.provider_transaction_id = str(transaction.get("id") or "")[:80]
    payment.updated_at = datetime.utcnow()
    if valid and not payment.paid_at:
        payment.paid_at = datetime.utcnow()
    db.session.commit()
    return valid


def build_report_context(student: Student, school: School, report_user: User) -> dict:
    rows = db.session.query(Score, Subject).join(
        Subject, Score.subject_id == Subject.id
    ).filter(
        Score.school_id == school.id,
        Score.student_id == student.id,
        Score.term == school.term,
        Score.academic_year == school.academic_year,
    ).order_by(Subject.name).all()
    attendance = Attendance.query.filter_by(
        school_id=school.id, student_id=student.id,
        term=school.term, academic_year=school.academic_year).first()
    fees = Fee.query.filter_by(
        school_id=school.id, student_id=student.id,
        term=school.term, academic_year=school.academic_year).first()
    total = sum(sc.class_score + sc.exam_score for sc, _ in rows)
    average = round(total / len(rows), 2) if rows else 0
    detail = StudentReportDetail.query.filter_by(
        student_id=student.id, term=school.term,
        academic_year=school.academic_year).first()
    term_summary = []
    for term_name in ["Term 1", "Term 2", "Term 3"]:
        term_scores = Score.query.filter_by(
            student_id=student.id, term=term_name,
            academic_year=school.academic_year).all()
        term_total = round(sum(sc.class_score + sc.exam_score for sc in term_scores), 2)
        term_summary.append({"total": term_total, "average": round(
            term_total / len(term_scores), 2) if term_scores else 0})
    yearly_total = round(sum(item["total"] for item in term_summary), 2)
    non_empty = [item for item in term_summary if item["total"]]
    return {
        "student": student,
        "report_user": report_user,
        "student_class": db.session.get(ClassRoom, student.class_id) if student.class_id else None,
        "rows": rows,
        "attendance": attendance,
        "fees": fees,
        "average": average,
        "report_total": total,
        "conduct": next((sc.conduct for sc, _ in rows if sc.conduct), ""),
        "position": next((sc.position for sc, _ in rows if sc.position), ""),
        "overall": grade_info(average),
        "detail": detail,
        "term_summary": term_summary,
        "yearly_total": yearly_total,
        "yearly_average": round(sum(item["average"] for item in non_empty) / len(non_empty), 2) if non_empty else 0,
        "fee_breakdown_total": round(sum([
            detail.arrears, detail.tuition_fees, detail.pta_dues,
            detail.medical_dues, detail.building_fund]), 2) if detail else 0,
    }


def save_crest(file_storage) -> str:
    if not file_storage or not file_storage.filename:
        return ""
    filename = secure_filename(file_storage.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        flash("Please upload a PNG, JPG, JPEG, or WEBP crest.", "error")
        return ""
    new_name = f"{uuid4().hex}{suffix}"
    UPLOAD_DIR.mkdir(exist_ok=True)
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


def teacher_class_ids(user: User) -> list[int]:
    if not user or user.role != "teacher":
        return []
    assigned = db.session.query(TeacherAssignment.class_id).filter_by(
        school_id=user.school_id, teacher_id=user.id)
    legacy = db.session.query(ClassRoom.id).filter_by(
        school_id=user.school_id, teacher_id=user.id)
    return list({row[0] for row in assigned.union(legacy).all()})


def teacher_subject_ids(user: User) -> list[int]:
    if not user or user.role != "teacher":
        return []
    assigned = db.session.query(TeacherAssignment.subject_id).filter_by(
        school_id=user.school_id, teacher_id=user.id).filter(TeacherAssignment.subject_id.isnot(None))
    legacy = db.session.query(Subject.id).filter_by(
        school_id=user.school_id, teacher_id=user.id)
    return list({row[0] for row in assigned.union(legacy).all()})


def teacher_can_access(user: User, class_id: int | None, subject_id: int | None = None, academic_year: str = "", term: str = "") -> bool:
    """Authorize the exact assignment tuple; legacy fields remain a compatibility fallback."""
    if not user or user.role != "teacher" or not class_id:
        return False
    query = TeacherAssignment.query.filter_by(
        school_id=user.school_id, teacher_id=user.id, class_id=class_id)
    if subject_id is not None:
        query = query.filter_by(subject_id=subject_id)
    if academic_year:
        query = query.filter(
            TeacherAssignment.academic_year.in_(["", academic_year]))
    if term:
        query = query.filter(TeacherAssignment.term.in_(["", term]))
    if query.first():
        return True
    legacy_class = ClassRoom.query.filter_by(
        id=class_id, school_id=user.school_id, teacher_id=user.id).first()
    if subject_id is None:
        return bool(legacy_class)
    legacy_subject = Subject.query.filter_by(
        id=subject_id, school_id=user.school_id, teacher_id=user.id).first()
    return bool(legacy_class and legacy_subject)


def client_ip() -> str:
    return (request.remote_addr or "unknown")[:80]


def login_is_limited(identity: str) -> bool:
    key = f"{client_ip()}:{identity.lower()}"
    cutoff = datetime.utcnow() - timedelta(seconds=Config.LOGIN_RATE_LIMIT_WINDOW_SECONDS)
    attempts = [stamp for stamp in _LOGIN_ATTEMPTS.get(
        key, []) if stamp >= cutoff]
    _LOGIN_ATTEMPTS[key] = attempts
    return len(attempts) >= Config.LOGIN_RATE_LIMIT_ATTEMPTS


def record_failed_login(identity: str) -> None:
    key = f"{client_ip()}:{identity.lower()}"
    _LOGIN_ATTEMPTS.setdefault(key, []).append(datetime.utcnow())


BASE_HTML = """
{% macro csrf() -%}<input type="hidden" name="_csrf" value="{{ csrf_token() }}">{%- endmacro %}
{% macro field(label, name, type='text', value='', placeholder='', required=false) -%}
<label>{{ label }}<input name="{{ name }}" type="{{ type }}" value="{{ value }}" placeholder="{{ placeholder }}" {% if required %}required{% endif %}></label>
{%- endmacro %}
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title or 'Smart Schools SMS' }}</title>
<link rel="icon" type="image/png" href="{{ url_for('static', filename='smart-school-logo.png', v='3') }}">
<link rel="shortcut icon" href="{{ url_for('favicon') }}?v=2">
<style>
:root{--ink:#132238;--muted:#667085;--line:#d9e2ee;--bg:#f5f8fc;--paper:#fff;--blue:#0b57d0;--navy:#062653;--teal:#008c8c;--green:#118a45;--gold:#f5ae2f;--red:#c92a2a;--purple:#6d3fc8;--shadow:0 16px 38px rgba(15,35,73,.12)}
*{box-sizing:border-box}body{margin:0;font-family:Inter,Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--ink)}a{text-decoration:none;color:inherit}.wrap{max-width:1220px;margin:0 auto;padding:0 22px}.topbar{background:linear-gradient(90deg,var(--navy),#071a38);color:#fff;position:sticky;top:0;z-index:5;box-shadow:0 8px 24px rgba(0,0,0,.16)}.nav{min-height:74px;display:flex;align-items:center;justify-content:space-between;gap:18px}.brand{display:flex;align-items:center;gap:12px;font-weight:800}.crest,.crest-fallback{width:44px;height:44px;border-radius:8px}.crest{object-fit:cover;background:#fff;padding:3px}.crest-fallback{background:linear-gradient(135deg,var(--gold),#00bdd6);display:grid;place-items:center;font-weight:900;color:#062653}.navlinks{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.navlinks a,.btn{border:0;border-radius:8px;padding:10px 14px;font-weight:800;cursor:pointer;display:inline-flex;align-items:center;gap:8px}.navlinks a{color:#dce9ff}.navlinks a:hover{background:rgba(255,255,255,.12);color:#fff}.btn{background:var(--blue);color:#fff}.btn.green{background:var(--green)}.btn.red{background:var(--red)}.btn.ghost{background:#edf4ff;color:var(--blue)}.btn.purple{background:var(--purple)}
.hero{min-height:520px;background:linear-gradient(90deg,rgba(6,38,83,.9),rgba(6,38,83,.5)),url('https://images.unsplash.com/photo-1580582932707-520aed937b7b?auto=format&fit=crop&w=1800&q=80') center/cover;color:white;display:flex;align-items:center}.hero-grid{display:grid;grid-template-columns:minmax(0,1fr) 450px;gap:36px;align-items:center}.hero h1{font-size:52px;line-height:1;margin:0 0 14px}.hero p{font-size:19px;line-height:1.55;max-width:650px;color:#e9f3ff}.module-card{background:white;color:var(--ink);border-radius:8px;padding:24px;box-shadow:var(--shadow);display:grid;grid-template-columns:repeat(2,1fr);gap:14px}.module{border:1px solid var(--line);border-radius:8px;padding:16px}.module strong{display:block;margin-bottom:6px}
main{padding:28px 0 60px}.grid{display:grid;gap:18px}.cols-4{grid-template-columns:repeat(4,1fr)}.cols-3{grid-template-columns:repeat(3,1fr)}.cols-2{grid-template-columns:repeat(2,1fr)}.card{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:20px;box-shadow:0 8px 22px rgba(15,35,73,.07)}.card h2,.card h3{margin-top:0}.stat{display:flex;justify-content:space-between;align-items:center}.stat b{font-size:28px}.muted{color:var(--muted)}.badge{display:inline-block;border-radius:999px;padding:5px 10px;background:#eaf2ff;color:var(--blue);font-weight:800;font-size:12px}
form{display:grid;gap:14px}label{font-weight:750;color:#344054;font-size:13px}input,select,textarea{width:100%;margin-top:6px;border:1px solid var(--line);border-radius:8px;padding:12px;background:white;color:var(--ink)}textarea{min-height:90px}table{width:100%;border-collapse:collapse;background:white;border-radius:8px;overflow:hidden}th,td{padding:12px;border-bottom:1px solid var(--line);text-align:left;font-size:14px;vertical-align:top}th{background:#eef5ff;color:#173763}.actions{display:flex;gap:8px;flex-wrap:wrap}.layout{display:grid;grid-template-columns:230px 1fr;gap:22px}.side{background:#062653;color:white;border-radius:8px;padding:18px;height:max-content;position:sticky;top:94px}.side a{display:block;padding:11px;border-radius:8px;color:#dce9ff}.side a:hover{background:rgba(255,255,255,.12);color:#fff}.flash{padding:12px 14px;border-radius:8px;margin-bottom:14px}.flash.success{background:#e8f7ef;color:#075d2f}.flash.error{background:#ffefed;color:#9c1f14}.login-shell{min-height:calc(100vh - 74px);display:grid;place-items:center;background:linear-gradient(135deg,#eef6ff,#f9fbff)}.login-card{width:min(460px,92vw)}.password-wrap{display:flex;gap:8px;align-items:center}.password-wrap input{flex:1}.show-password{margin-top:6px;background:#edf4ff;color:var(--blue);border:1px solid var(--line);border-radius:8px;padding:12px 13px;font-weight:800;cursor:pointer}.report-head{display:flex;align-items:center;justify-content:space-between;border-bottom:3px solid var(--navy);padding-bottom:16px;margin-bottom:16px;gap:16px}.report-title{text-align:center}.report-title h2{margin-bottom:4px}.report-title p{margin:3px 0}.report-card{max-width:1060px}.report-crest{width:72px;height:72px}.report-meta p{margin:0}.signature-row{display:grid;grid-template-columns:1fr 220px;gap:24px;align-items:end;margin-top:26px;border-top:1px solid var(--line);padding-top:18px}.signature-img{display:block;max-width:180px;max-height:70px;object-fit:contain;margin:10px 0}.signature-line{height:58px;border-bottom:1px solid var(--ink);max-width:220px;margin-bottom:10px}.slip{max-width:520px;margin:auto;border:2px dashed var(--navy);background:white;padding:26px;border-radius:8px}
@media(max-width:940px){.hero-grid,.layout,.cols-4,.cols-3,.cols-2{grid-template-columns:1fr}.hero h1{font-size:38px}.module-card{grid-template-columns:1fr}.nav{height:auto;padding:14px 0;align-items:flex-start}.side{position:static}.navlinks{justify-content:flex-end}table{display:block;overflow-x:auto;white-space:nowrap}.report-head{align-items:flex-start}.signature-row{grid-template-columns:1fr}}@media(max-width:620px){.wrap{padding:0 14px}.nav,.report-head{flex-direction:column}.navlinks{justify-content:flex-start}.hero{min-height:560px}.hero h1{font-size:34px}.card{padding:16px}th,td{padding:10px}.btn{width:100%;justify-content:center}.actions .btn{width:auto}}@media print{.topbar,.side,.no-print,.btn{display:none!important}body{background:white}.wrap,main{max-width:none;padding:0}.layout{display:block}.card{box-shadow:none;border:0}.report-card{max-width:none}.slip{border:2px solid #111}table{display:table;white-space:normal}}
.topbar{background:linear-gradient(90deg,#0a3b50,#123c69 55%,#22543d)}.card{transition:transform .18s ease,box-shadow .18s ease}.card:hover{transform:translateY(-1px);box-shadow:0 14px 30px rgba(14,43,72,.1)}.login-shell{background:linear-gradient(90deg,rgba(6,35,58,.82),rgba(8,74,83,.7)),url('https://images.unsplash.com/photo-1509062522246-3755977927d7?auto=format&fit=crop&w=1800&q=80') center/cover}.login-card{backdrop-filter:blur(8px);background:rgba(255,255,255,.95)}.login-visual{border-radius:8px;min-height:160px;background:center/cover;margin:-6px -6px 16px}.login-visual.admin{background-image:linear-gradient(0deg,rgba(6,35,58,.18),rgba(6,35,58,.18)),url('https://images.unsplash.com/photo-1577896851231-70ef18881754?auto=format&fit=crop&w=900&q=80')}.login-visual.teacher{background-image:linear-gradient(0deg,rgba(6,35,58,.18),rgba(6,35,58,.18)),url('https://images.unsplash.com/photo-1588072432836-e10032774350?auto=format&fit=crop&w=900&q=80')}.login-visual.student{background-image:linear-gradient(0deg,rgba(6,35,58,.18),rgba(6,35,58,.18)),url('https://images.unsplash.com/photo-1524995997946-a1c2e315a42f?auto=format&fit=crop&w=900&q=80')}.dashboard-hero{background:linear-gradient(90deg,rgba(10,59,80,.93),rgba(34,84,61,.78)),url('https://images.unsplash.com/photo-1497633762265-9d179a990aa6?auto=format&fit=crop&w=1600&q=80') center/cover;color:white;border:0}.dashboard-hero h2{margin:0}.dashboard-hero .muted{color:#e5f2f2}.metric-card{border-left:5px solid var(--teal)}.metric-card b{color:#0a3b50}.progress{height:10px;border-radius:999px;background:#dce8ed;overflow:hidden}.progress span{display:block;height:100%;background:linear-gradient(90deg,var(--teal),var(--green))}.quick-actions a{justify-content:center}.feature-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.feature-strip div{background:#f7fbfd;border:1px solid var(--line);border-radius:8px;padding:14px}@media(max-width:940px){.feature-strip{grid-template-columns:1fr}.login-visual{min-height:130px}}
.login-card{width:min(760px,94vw);padding:34px}.login-card h2{font-size:32px}.login-visual{min-height:250px}.report-card.terminal{max-width:920px;background:white;color:#111;border:1px solid #111;box-shadow:none}.terminal .report-top{display:grid;grid-template-columns:90px 1fr 90px;gap:12px;align-items:center;text-align:center}.terminal .report-top img{width:76px;height:76px;object-fit:contain}.terminal h2{font-size:18px;margin:0;text-transform:uppercase}.terminal p{margin:2px 0}.terminal-title{display:inline-block;background:#111;color:#fff;padding:7px 28px;font-weight:800;text-transform:uppercase;margin:8px 0}.terminal-student{text-align:center;font-weight:800;margin:10px 0}.terminal-meta{display:grid;grid-template-columns:1fr 1fr;gap:4px 36px;font-size:12px;margin:10px 0}.terminal table{border-collapse:collapse;border:1px solid #111;box-shadow:none}.terminal th,.terminal td{border:1px solid #111;padding:5px 6px;font-size:11px;color:#111;background:white}.terminal th{text-transform:uppercase;text-align:center}.terminal .subjects td:first-child{text-transform:uppercase}.terminal .remarks td{height:24px}.terminal .signature-line{height:42px;border-bottom:1px solid #111}.terminal .grading-key td,.terminal .grading-key th{text-align:center;font-size:10px}.terminal .no-border{border:0!important}.terminal .powered{font-size:9px;margin-top:18px}
@media print{.report-card.terminal{border:0!important;padding:0!important}.terminal th,.terminal td{padding:4px 5px!important;font-size:10px!important}.terminal .report-top img{width:70px;height:70px}.terminal h2{font-size:16px}.terminal-title{padding:6px 24px}.terminal .card{border:0!important}}
.dashboard-hero{background:linear-gradient(115deg,rgba(11,31,58,.98),rgba(15,118,110,.88)),url('https://images.unsplash.com/photo-1497633762265-9d179a990aa6?auto=format&fit=crop&w=1600&q=80') center/cover;border-radius:14px;box-shadow:0 18px 42px rgba(11,31,58,.2)}.dashboard-kicker{display:inline-block;margin-bottom:10px;color:#d9fbf5;font-size:11px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.dashboard-kicker.dark{color:#0f766e}.metric-card{border-left-color:#d4a72c;border-radius:12px}.metric-card b{color:#0b1f3a}.card{border-radius:12px}.feature-strip div{background:rgba(255,255,255,.13);border-color:rgba(255,255,255,.22);backdrop-filter:blur(4px)}.feature-strip .muted{color:#d9fbf5}.sms-config{display:grid;grid-template-columns:minmax(220px,.8fr) minmax(320px,1.2fr);gap:24px;align-items:center;border:1px solid #cfe7e3;background:linear-gradient(135deg,#f7fcfb,#eef7f5)}.sms-config h3{color:#0b1f3a;margin:0 0 8px}.sms-config-form{gap:10px}.sms-config-form input{border-color:#a8d5ce;background:#fff}.sms-config-actions{display:flex;gap:12px;align-items:center;justify-content:space-between}.connection-status{border-radius:999px;padding:7px 10px;font-size:12px;font-weight:800}.connection-status.connected{background:#dff7eb;color:#166534}.connection-status.not-connected{background:#fff4d6;color:#8a5b00}.terminal{border-top:8px solid #0f766e!important}.terminal .report-top{border-bottom:2px solid #d4a72c;padding-bottom:10px}.terminal .terminal-title{background:linear-gradient(90deg,#0b1f3a,#0f766e)!important}.terminal th{background:#0b1f3a!important;color:#fff!important}.terminal .remarks tr:nth-child(even) td{background:#f0fdfa!important}.terminal .grading-key th{background:#0f766e!important;color:#fff!important}.terminal .grading-key td{background:#fffbeb!important}.cedi{font-weight:800;color:#0f766e}@media(max-width:940px){.sms-config{grid-template-columns:1fr}.sms-config-actions{align-items:stretch;flex-direction:column}.sms-config-actions .btn{width:100%}}
.layout{align-items:start}.side{border-radius:18px;background:linear-gradient(180deg,#071b3b,#08274d 55%,#063257);box-shadow:0 18px 44px rgba(3,17,43,.28)}.side strong{font-size:15px;letter-spacing:.01em}.side a{position:relative;margin:4px 0;padding:11px 12px;font-size:13px;font-weight:700;transition:all .2s ease}.side a:before{content:'›';display:inline-block;margin-right:9px;color:#7dd3fc;font-size:18px;line-height:10px}.side a:hover{transform:translateX(3px);background:linear-gradient(90deg,#2563eb,#3b82f6);box-shadow:0 8px 18px rgba(37,99,235,.34)}.dashboard-hero{min-height:180px;padding:28px!important}.dashboard-hero h2{font-size:29px;letter-spacing:-.04em}.dashboard-hero:after{content:'';position:absolute;right:42px;top:30px;width:120px;height:120px;border:1px solid rgba(255,255,255,.22);border-radius:50%;box-shadow:24px 28px 0 -1px rgba(255,255,255,.08),50px 0 0 -1px rgba(255,255,255,.06)}.dashboard-hero{position:relative;overflow:hidden}.dashboard-hero>*{position:relative;z-index:1}.metric-card{position:relative;overflow:hidden;min-height:112px;border-left:0!important;padding:18px!important;color:#fff!important}.metric-card span,.metric-card b{color:#fff!important}.metric-card:after{content:'';position:absolute;right:-18px;bottom:-42px;width:100px;height:100px;border-radius:50%;background:rgba(255,255,255,.13)}.dashboard-hero + .grid .metric-card:nth-child(1){background:linear-gradient(135deg,#2563eb,#4f46e5)}.dashboard-hero + .grid .metric-card:nth-child(2){background:linear-gradient(135deg,#0f766e,#14b8a6)}.dashboard-hero + .grid .metric-card:nth-child(3){background:linear-gradient(135deg,#7c3aed,#a855f7)}.dashboard-hero + .grid .metric-card:nth-child(4){background:linear-gradient(135deg,#ea580c,#f59e0b)}.metric-card .progress{background:rgba(255,255,255,.25)}.metric-card .progress span{background:#fff}.card{box-shadow:0 10px 28px rgba(15,23,42,.06)}.card:hover{box-shadow:0 18px 38px rgba(15,23,42,.1)}.quick-actions .btn{border:1px solid #dbeafe;background:#f8fbff;color:#1d4ed8}.quick-actions .btn:hover{background:#2563eb;color:#fff}.topbar{background:#fff;color:#0f172a;border-bottom:1px solid #e8eef7;box-shadow:0 4px 18px rgba(15,23,42,.04)}.navlinks a{color:#334155}.navlinks a:hover{color:#1d4ed8;background:#eff6ff}.brand{color:#0f172a}.brand small{color:#64748b}@media(max-width:940px){.side{border-radius:14px}.dashboard-hero:after{display:none}.metric-card{min-height:96px}}
/* Premium application system */
:root{--brand:#2563eb;--brand-strong:#1d4ed8;--brand-soft:#eff6ff;--nav:#071b33;--accent:#0f766e;--warning:#f59e0b;--surface:#fff;--surface-subtle:#f8fafc;--text:#0f172a;--text-soft:#64748b;--border:#e2e8f0;--radius:14px;--radius-sm:10px;--focus:0 0 0 3px rgba(37,99,235,.2)}
html{scroll-behavior:smooth}body{font-family:Inter,"Segoe UI",system-ui,-apple-system,sans-serif;background:#f6f8fc;color:var(--text);line-height:1.5}body:before{content:"";position:fixed;inset:0 0 auto;height:280px;background:radial-gradient(circle at 80% -20%,rgba(37,99,235,.12),transparent 55%);pointer-events:none;z-index:-1}h1,h2,h3{letter-spacing:-.025em}h2{font-size:clamp(1.35rem,2vw,1.75rem)}.wrap{max-width:1440px;padding-inline:clamp(16px,3vw,36px)}main.wrap{padding-top:28px}.topbar{position:sticky;background:rgba(255,255,255,.9);backdrop-filter:blur(18px);border-bottom:1px solid rgba(226,232,240,.8)}.nav{min-height:72px}.brand{gap:11px}.brand:before{content:"";width:42px;height:42px;background:url("{{ url_for('static', filename='smart-school-logo.png') }}") center/contain no-repeat;flex:0 0 auto}.brand .crest,.brand .crest-fallback{display:none}.brand>span>span{font-size:15px}.brand small{font-size:11px;font-weight:600}.layout{grid-template-columns:260px minmax(0,1fr);gap:28px}.side{top:96px;border-radius:18px;padding:18px 14px;background:linear-gradient(180deg,#071b33,#0b2d50);max-height:calc(100vh - 112px);overflow:auto}.side strong,.side>p{display:block;padding-inline:10px}.side a{display:flex;align-items:center;min-height:42px;border-radius:10px}.side a:before{content:"";width:7px;height:7px;border:2px solid #7dd3fc;border-radius:3px;margin-right:11px}.side a:hover,.side a:focus-visible{transform:none;background:rgba(37,99,235,.9);outline:none}.card{border:1px solid rgba(226,232,240,.9);border-radius:var(--radius);box-shadow:0 1px 2px rgba(15,23,42,.03),0 10px 30px rgba(15,23,42,.05);padding:clamp(18px,2.3vw,26px)}.card:hover{transform:none}.dashboard-hero{min-height:210px;background:linear-gradient(110deg,#0b1f3a 10%,#0f4c67 62%,#0f766e);padding:32px!important}.metric-card{border-radius:14px}.btn{min-height:40px;border-radius:10px;padding:9px 14px;transition:transform .15s,box-shadow .15s,background .15s}.btn:hover{transform:translateY(-1px);box-shadow:0 7px 16px rgba(37,99,235,.18)}.btn:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,a:focus-visible{outline:none;box-shadow:var(--focus)}input,select,textarea{min-height:44px;border-radius:10px;border-color:var(--border);transition:border-color .15s,box-shadow .15s}input:hover,select:hover,textarea:hover{border-color:#94a3b8}input:focus,select:focus,textarea:focus{border-color:var(--brand)}table{border:1px solid var(--border);border-radius:12px}th{height:44px;background:#f8fafc;color:#475569;font-size:11px;letter-spacing:.045em;text-transform:uppercase}td{height:50px}tbody tr:hover td{background:#f8fbff}.flash{border:1px solid currentColor;display:flex;align-items:center;gap:9px}.flash:before{content:"✓";display:grid;place-items:center;width:22px;height:22px;border-radius:50%;background:currentColor;color:#fff}.flash.error:before{content:"!"}.table-head{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:16px}.table-head h2,.table-head p{margin:0}.table-search{width:min(280px,100%);margin:0}.empty-state{display:grid;place-items:center;text-align:center;gap:5px;padding:38px;color:var(--text-soft)}.empty-state:before{content:"◇";font-size:34px;color:#94a3b8}.empty-state b{color:var(--text)}.badge{background:var(--brand-soft);color:var(--brand-strong)}.hero{min-height:620px;background:linear-gradient(90deg,rgba(7,27,51,.96),rgba(12,67,91,.78)),url('https://images.unsplash.com/photo-1509062522246-3755977927d7?auto=format&fit=crop&w=1800&q=80') center/cover}.hero h1{font-size:clamp(42px,6vw,68px);max-width:720px}.module-card{border-radius:20px;background:rgba(255,255,255,.97)}.module{border-radius:12px}.login-shell{min-height:calc(100vh - 72px)}.login-card{border-radius:20px}.login-visual{border-radius:14px}.slip{border-radius:16px}.report-card.terminal{border-radius:0}.navlinks .btn{color:#fff}
@media(max-width:940px){.layout{grid-template-columns:1fr}.side{position:relative;top:0;display:flex;gap:6px;overflow-x:auto;max-height:none;padding:10px}.side strong,.side>p{display:none}.side a{white-space:nowrap;padding-inline:12px}.side a:before{display:none}.cols-4{grid-template-columns:repeat(2,minmax(0,1fr))}.table-head{align-items:stretch;flex-direction:column}.table-search{width:100%}}
@media(max-width:620px){.topbar .brand small{display:none}.nav{flex-direction:row;align-items:center}.navlinks{justify-content:flex-end}.navlinks a:not(.btn){display:none}.cols-4{grid-template-columns:1fr}.dashboard-hero{min-height:180px;padding:22px!important}.hero{min-height:680px}.login-card{padding:20px}.card{border-radius:12px}table{font-size:13px}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
.table-shell{position:relative;width:100%;overflow:hidden;border:1px solid var(--border);border-radius:12px;background:#fff}.table-scroll{width:100%;overflow-x:auto}.table-shell table{border:0;border-radius:0;margin:0}.table-tools{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;border-bottom:1px solid var(--border);background:#fff}.table-tools input{width:min(300px,100%);min-height:38px;margin:0}.table-count{font-size:12px;color:var(--text-soft);white-space:nowrap}.table-pagination{display:flex;align-items:center;justify-content:flex-end;gap:7px;padding:11px 14px;border-top:1px solid var(--border);background:#f8fafc}.page-button{min-width:34px;height:34px;padding:0 9px;border:1px solid var(--border);border-radius:8px;background:#fff;color:#334155;font-weight:700;cursor:pointer}.page-button:hover:not(:disabled),.page-button.active{background:var(--brand);border-color:var(--brand);color:#fff}.page-button:disabled{opacity:.45;cursor:not-allowed}.table-empty-row td{text-align:center;padding:44px 18px!important;color:var(--text-soft)}.status-pill{display:inline-flex;align-items:center;border-radius:999px;padding:4px 9px;font-size:11px;font-weight:800;background:#dcfce7;color:#166534}
@media(max-width:620px){.table-tools{align-items:stretch;flex-direction:column}.table-tools input{width:100%}.table-pagination{justify-content:center}.table-count{text-align:center}}
.app-dashboard{max-width:1600px}.dashboard-content{display:grid;gap:18px;min-width:0}.page-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;padding:4px 2px 2px}.page-heading h1{font-size:29px;margin:2px 0}.page-heading p{margin:0;color:var(--text-soft)}.period-chip{display:inline-flex;align-items:center;min-height:34px;padding:6px 11px;border:1px solid var(--border);border-radius:9px;background:#fff;color:#475569;font-size:12px;font-weight:700;white-space:nowrap}.kpi-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.kpi-card{display:flex;align-items:center;gap:14px;background:#fff;border:1px solid var(--border);border-radius:14px;padding:17px;box-shadow:0 5px 18px rgba(15,23,42,.04)}.kpi-icon{display:grid;place-items:center;width:46px;height:46px;border-radius:50%;font-size:21px;background:#eff6ff;color:#2563eb}.kpi-card:nth-child(2) .kpi-icon{background:#ecfdf5;color:#059669}.kpi-card:nth-child(3) .kpi-icon{background:#f5f3ff;color:#7c3aed}.kpi-card:nth-child(4) .kpi-icon{background:#fff7ed;color:#ea580c}.kpi-card div:last-child{display:grid}.kpi-card span{font-size:12px;color:#64748b}.kpi-card strong{font-size:23px;letter-spacing:-.03em}.kpi-card small{font-size:10px;color:#16a34a}.dashboard-grid{display:grid;gap:14px}.dashboard-charts{grid-template-columns:minmax(0,1.7fr) minmax(280px,.8fr)}.dashboard-lower{grid-template-columns:minmax(0,1.25fr) minmax(260px,.8fr) minmax(230px,.65fr)}.panel-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:14px}.panel-heading h2{font-size:16px;margin:0}.panel-heading p{font-size:11px;color:#94a3b8;margin:2px 0}.chart-card,.fee-card{min-height:315px}.line-chart{position:relative;height:225px}.line-chart svg{position:absolute;inset:5px 0 24px;width:100%;height:calc(100% - 29px);z-index:2}.chart-gridlines{position:absolute;inset:5px 0 24px;background:repeating-linear-gradient(to bottom,#e9eef5 0,#e9eef5 1px,transparent 1px,transparent 42px)}.chart-labels{position:absolute;inset:auto 0 0;display:flex;justify-content:space-between;color:#94a3b8;font-size:10px}.donut-wrap{height:225px;display:flex;align-items:center;justify-content:center;gap:24px}.donut{--value:0;width:154px;height:154px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(#0f766e calc(var(--value)*1%),#f59e0b 0 88%,#ef4444 0);position:relative}.donut:before{content:"";position:absolute;inset:25px;background:#fff;border-radius:50%}.donut>div{z-index:1;display:grid;text-align:center}.donut strong{font-size:23px}.donut span{font-size:10px;color:#64748b}.legend{display:grid;gap:13px;list-style:none;padding:0;margin:0;font-size:12px}.legend i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px}.green-dot{background:#0f766e}.gold-dot{background:#f59e0b}.red-dot{background:#ef4444}.activity-list,.event-list,.quick-list{display:grid;gap:3px}.activity-item,.event-item{display:flex;align-items:center;gap:11px;padding:10px 0;border-bottom:1px solid #eef2f7}.activity-icon{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:#eff6ff;color:#2563eb;font-weight:900}.activity-item div,.event-item div{display:grid;min-width:0}.activity-item b,.event-item b{font-size:12px}.activity-item small,.event-item small{font-size:10px;color:#94a3b8}.activity-item time{margin-left:auto;font-size:10px;color:#94a3b8}.event-date{width:40px;height:42px;border-radius:9px;background:#eff6ff;color:#2563eb;display:grid;place-items:center;line-height:1;font-size:9px;text-transform:uppercase}.event-date b{font-size:15px}.quick-list a{display:flex;align-items:center;min-height:36px;padding:8px 10px;border-radius:8px;background:#2563eb;color:#fff;font-size:11px;font-weight:750}.quick-list a:nth-child(even){background:#0f766e}
@media(max-width:1180px){.dashboard-lower{grid-template-columns:1fr 1fr}.quick-panel{grid-column:1/-1}.quick-list{grid-template-columns:repeat(3,1fr)}}@media(max-width:940px){.kpi-grid{grid-template-columns:repeat(2,1fr)}.dashboard-charts,.dashboard-lower{grid-template-columns:1fr}.quick-panel{grid-column:auto}.quick-list{grid-template-columns:repeat(2,1fr)}}@media(max-width:620px){.page-heading{align-items:flex-start;flex-direction:column}.kpi-grid{grid-template-columns:1fr}.dashboard-lower{grid-template-columns:1fr}.quick-list{grid-template-columns:1fr}.donut-wrap{gap:15px}.donut{width:130px;height:130px}.chart-card,.fee-card{min-height:auto}}
.report-sheet{background:#fff;color:#111;border:1px solid #111;border-radius:18px;padding:24px;max-width:1100px;margin:auto}.report-actions{display:flex;justify-content:flex-end;gap:10px;margin-bottom:14px}.academic-report-head{display:grid;grid-template-columns:1.45fr 1fr;border:2px solid #222;border-radius:14px;overflow:hidden}.report-brand{display:flex;align-items:center;gap:22px;padding:18px;border-right:2px solid #222;text-align:center}.report-brand img{width:88px;height:88px;object-fit:contain}.report-brand div{flex:1}.report-brand h1{font-size:27px;margin:0;text-transform:uppercase}.report-brand p{font-weight:800;margin:7px 0}.school-contact{padding:18px;line-height:2}.student-report-meta{display:grid;grid-template-columns:1fr 1fr;gap:80px;padding:18px 12px 4px}.student-report-meta p{border-bottom:1px dotted #333;padding-bottom:5px}.next-term{text-align:center;margin:15px}.report-sheet table{border:1px solid #222;border-radius:10px}.report-sheet th,.report-sheet td{border:1px solid #444;padding:7px;background:#fff;color:#111}.report-sheet th{text-align:center}.report-total{font-weight:800}.print-chart{border:1px solid #222;border-radius:10px;padding:14px;margin:18px 0;break-inside:avoid}.print-chart h2{text-align:center;font-size:14px}.report-bars{display:grid;gap:7px}.report-bar-row{display:grid;grid-template-columns:170px 1fr 48px;gap:10px;align-items:center;font-size:11px}.report-bar-row>div{height:13px;background:#eef2f7;border:1px solid #cbd5e1}.report-bar-row i{display:block;height:100%;background:linear-gradient(90deg,#2563eb,#0f766e)}.report-two-col{display:grid;grid-template-columns:1.2fr .8fr;gap:70px;margin-top:18px;align-items:start}.report-two-col h3{text-align:center;font-size:13px}.remarks-lines{margin-top:22px}.remarks-lines p{min-height:42px;border-bottom:2px dotted #555}.signature-promotion{display:grid;grid-template-columns:1fr 1fr;gap:60px;border-top:1px dashed #555;border-bottom:1px dashed #555;margin:24px 0;padding:18px;text-align:center}.signature-promotion>div{display:grid;place-items:center;gap:8px}.signature-promotion img{max-width:150px;max-height:58px;object-fit:contain}.signature-promotion strong{font-size:18px}.report-date{padding-top:48px;line-height:2}.fees-sign{align-items:center}
@media(max-width:760px){.academic-report-head,.student-report-meta,.report-two-col,.signature-promotion{grid-template-columns:1fr}.report-brand{border-right:0;border-bottom:2px solid #222}.student-report-meta{gap:0}.report-two-col{gap:15px}.report-bar-row{grid-template-columns:100px 1fr 42px}.report-sheet{padding:12px;border-radius:10px}.report-brand h1{font-size:20px}}
@media print{@page{size:A4;margin:8mm}.report-sheet{border:0;border-radius:0;padding:0;max-width:none;font-size:9px}.academic-report-head{border-radius:8px}.report-brand{padding:10px}.report-brand img{width:62px;height:62px}.report-brand h1{font-size:20px}.school-contact{padding:10px;line-height:1.6}.student-report-meta{padding:7px;gap:45px}.student-report-meta p{margin:4px}.next-term{margin:7px}.report-sheet th,.report-sheet td{padding:3px;font-size:8px}.print-chart{padding:7px;margin:8px 0}.report-bars{gap:3px}.report-bar-row{grid-template-columns:120px 1fr 35px;font-size:8px}.report-bar-row>div{height:8px}.report-two-col{gap:35px;margin-top:8px}.remarks-lines{margin-top:8px}.remarks-lines p{min-height:22px;margin:5px}.signature-promotion{margin:8px 0;padding:7px}.signature-promotion img{max-height:35px}.report-date{padding-top:20px}}
</style></head><body>
<header class="topbar no-print"><div class="wrap nav"><a class="brand" href="{{ url_for('index') }}">{% if school and school.crest %}<img class="crest" src="{{ url_for('uploads', filename=school.crest) }}" alt="crest">{% else %}<span class="crest-fallback">SMS</span>{% endif %}<span><span style="display:block">Smart Schools SMS</span><small>{{ school.name if school else 'School Management System' }}</small></span></a><nav class="navlinks">{% if user %}<a href="{{ url_for('dashboard') }}">Dashboard</a><a href="{{ url_for('logout') }}">Logout</a>{% else %}<a href="{{ url_for('index') }}">Home</a><a href="{{ url_for('register_school') }}">Register School</a><a class="btn" href="{{ url_for('login') }}">Login</a>{% endif %}</nav></div></header>
{% block body %}{% endblock %}<script>
document.querySelectorAll('input[type="password"]').forEach(function(input) {
  if (input.dataset.viewReady) return;
  input.dataset.viewReady = "1";
  const wrap = document.createElement("span");
  wrap.className = "password-wrap";
  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "show-password";
  button.textContent = "View";
  button.addEventListener("click", function() {
    const showing = input.type === "text";
    input.type = showing ? "password" : "text";
    button.textContent = showing ? "View" : "Hide";
  });
  wrap.appendChild(button);
});
document.querySelectorAll('.terminal .remarks td').forEach(function(cell) {
  cell.innerHTML = cell.innerHTML.replace(/Fee Balance:\\s*([0-9,.]+)/g, 'Fee Balance: <span class="cedi">GH₵ $1</span>');
});
document.querySelectorAll('table').forEach(function(table) {
  const headings = Array.from(table.querySelectorAll('th')).map(function(heading) { return heading.textContent.trim(); });
  if (headings.includes('Due') && headings.includes('Paid') && headings.includes('Balance')) {
    Array.from(table.querySelectorAll('tr')).slice(1).forEach(function(row) {
      [2, 3, 4].forEach(function(index) {
        const cell = row.children[index];
        if (cell && cell.textContent.trim() && !cell.textContent.includes('GH₵')) cell.textContent = 'GH₵ ' + cell.textContent.trim();
      });
    });
  }
});
document.querySelectorAll('.report-top p').forEach(function(line) {
  if (line.textContent.includes('@')) line.innerHTML = line.textContent.replace(/(\\S+@\\S+)/, '<br>$1');
});
document.querySelectorAll('[data-confirm]').forEach(function(form) {
  form.addEventListener('submit', function(event) { if (!window.confirm(form.dataset.confirm)) event.preventDefault(); });
});
document.querySelectorAll('.table-search').forEach(function(input) {
  input.addEventListener('input', function() {
    const query = input.value.trim().toLowerCase();
    const table = input.closest('.card').querySelector('table');
    if (!table) return;
    table.querySelectorAll('tbody tr, tr').forEach(function(row, index) {
      if (index === 0 || row.querySelector('th')) return;
      row.hidden = Boolean(query) && !row.textContent.toLowerCase().includes(query);
    });
  });
});
document.querySelectorAll('table').forEach(function(table, tableIndex) {
  if (table.closest('.terminal') || table.closest('.slip') || table.dataset.enhanced) return;
  table.dataset.enhanced = '1';
  const rows = Array.from(table.querySelectorAll('tr')).filter(function(row) { return !row.querySelector('th'); });
  if (!rows.length) return;
  const shell = document.createElement('div'); shell.className = 'table-shell';
  const tools = document.createElement('div'); tools.className = 'table-tools no-print';
  const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Search this table…'; search.setAttribute('aria-label', 'Search table');
  const count = document.createElement('span'); count.className = 'table-count';
  tools.append(search, count);
  const scroll = document.createElement('div'); scroll.className = 'table-scroll';
  const pager = document.createElement('div'); pager.className = 'table-pagination no-print';
  table.parentNode.insertBefore(shell, table); shell.append(tools, scroll, pager); scroll.appendChild(table);
  let page = 1; const pageSize = 10;
  function renderTable() {
    const query = search.value.trim().toLowerCase();
    const matching = rows.filter(function(row) { return !query || row.textContent.toLowerCase().includes(query); });
    const pages = Math.max(1, Math.ceil(matching.length / pageSize)); page = Math.min(page, pages);
    rows.forEach(function(row) { row.hidden = true; });
    matching.slice((page - 1) * pageSize, page * pageSize).forEach(function(row) { row.hidden = false; });
    count.textContent = matching.length + (matching.length === 1 ? ' record' : ' records');
    pager.replaceChildren();
    if (pages <= 1) { pager.hidden = true; return; }
    pager.hidden = false;
    function button(label, target, disabled, active) { const b=document.createElement('button'); b.type='button'; b.className='page-button'+(active?' active':''); b.textContent=label; b.disabled=disabled; b.onclick=function(){page=target;renderTable();}; return b; }
    pager.appendChild(button('‹', page-1, page===1, false));
    const start=Math.max(1,page-2), end=Math.min(pages,start+4);
    for(let number=start;number<=end;number++) pager.appendChild(button(String(number),number,false,number===page));
    pager.appendChild(button('›', page+1, page===pages, false));
  }
  search.addEventListener('input', function(){page=1;renderTable();}); renderTable();
});
document.querySelectorAll('.score-edit').forEach(function(button) {
  button.addEventListener('click', function() {
    const form = document.querySelector('form input[name="class_score"]')?.closest('form');
    if (!form) return;
    ['student_id','subject_id','class_score','exam_score','term','academic_year','position','conduct','remarks'].forEach(function(name) {
      const field=form.elements[name]; const key=name.replaceAll('_','-'); if(field && button.dataset[key] !== undefined) field.value=button.dataset[key];
    });
    form.scrollIntoView({behavior:'smooth',block:'start'});
    const submit=form.querySelector('button[type="submit"],button:not([type])'); if(submit) submit.textContent='Update Score';
  });
});
</script></body></html>
"""


SIDEBAR = """
<aside class="side no-print"><strong>{{ role_label(user.role) }}</strong><p class="muted" style="color:#bcd0ec">{{ user.full_name }}</p>
<a href="{{ url_for('dashboard') }}">Dashboard</a>
{% if user.role == 'system_admin' %}<a href="{{ url_for('schools') }}">Schools</a>{% endif %}
{% if user.role == 'school_admin' %}<a href="{{ url_for('users') }}">User Management</a><a href="{{ url_for('parent_links') }}">Parents & Guardians</a><a href="{{ url_for('teachers') }}">Teachers</a><a href="{{ url_for('teacher_assignments') }}">Teaching Assignments</a><a href="{{ url_for('classes_subjects') }}">Classes & Subjects</a>{% endif %}
{% if user.role in ['teacher','registrar'] %}<a href="{{ url_for('students') }}">{{ 'My Students' if user.role == 'teacher' else 'Students & Admissions' }}</a>{% endif %}{% if user.role == 'teacher' %}<a href="{{ url_for('attendance') }}">Attendance</a>{% endif %}
{% if user.role in ['school_admin','teacher'] %}<a href="{{ url_for('promotions') }}">Promotions</a>{% endif %}
{% if user.role in ['school_admin','teacher'] %}<a href="{{ url_for('report_details') }}">Report Details</a>{% endif %}
{% if user.role in ['school_admin','teacher'] %}<a href="{{ url_for('scores') }}">Scores</a>{% endif %}
{% if user.role in ['school_admin','accountant'] %}<a href="{{ url_for('fees') }}">Fees & Payments</a>{% endif %}{% if user.role == 'school_admin' %}<a href="{{ url_for('onboarding') }}">School Profile</a>{% endif %}
{% if user.role in ['system_admin','school_admin'] %}<a href="{{ url_for('communications') }}">SMS & Email</a>{% endif %}
{% if user.role == 'system_admin' %}<a href="{{ url_for('audit_logs') }}">Audit Log</a>{% endif %}
{% if user.role in ['school_admin','teacher','student'] %}<a href="{{ url_for('announcements') }}">Notices</a><a href="{{ url_for('calendar') }}">Calendar</a><a href="{{ url_for('timetable') }}">Timetable</a><a href="{{ url_for('library') }}">Library</a>{% endif %}
{% if user.role == 'parent' %}<a href="{{ url_for('parent_portal') }}">My Children</a>{% endif %}
{% if user.role == 'student' %}<a href="{{ url_for('student_results') }}">My Results</a>{% endif %}</aside>
"""

DASHBOARD_PAGE = """
<main class="wrap app-dashboard">
{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}
<div class="layout">""" + SIDEBAR + """
<section class="dashboard-content">
  <header class="page-heading"><div><span class="dashboard-kicker dark">{{ role_label(user.role) }} workspace</span><h1>Dashboard</h1><p>Welcome back, {{ user.full_name }}. Here is what is happening at {{ school.name if school else 'Smart Schools SMS' }}.</p></div><div class="period-chip">{{ school.academic_year ~ ' · ' ~ school.term if school else 'Platform overview' }}</div></header>
  <div class="kpi-grid">{% for name,value in stats.items() %}<article class="kpi-card kpi-{{ loop.index }}"><div class="kpi-icon">{{ ['♙','♧','▥','◈'][loop.index0] }}</div><div><span>{{ name }}</span><strong>{{ value }}</strong><small>Current overview</small></div></article>{% endfor %}</div>
  <div class="dashboard-grid dashboard-charts">
    <article class="card chart-card"><div class="panel-heading"><div><h2>{{ 'School Enrollment' if user.role != 'teacher' else 'Class Performance' }}</h2><p>Current academic period</p></div><span class="period-chip">This term</span></div><div class="line-chart" aria-label="Performance trend"><div class="chart-gridlines"></div><svg viewBox="0 0 640 210" preserveAspectRatio="none" role="img"><defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#2563eb" stop-opacity=".28"/><stop offset="1" stop-color="#2563eb" stop-opacity="0"/></linearGradient></defs><path d="M10 178 L85 142 L160 155 L235 105 L310 122 L385 78 L460 92 L535 48 L630 20 L630 205 L10 205 Z" fill="url(#area)"/><polyline points="10,178 85,142 160,155 235,105 310,122 385,78 460,92 535,48 630,20" fill="none" stroke="#2563eb" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>{% for x,y in [(10,178),(85,142),(160,155),(235,105),(310,122),(385,78),(460,92),(535,48),(630,20)] %}<circle cx="{{ x }}" cy="{{ y }}" r="5" fill="#fff" stroke="#2563eb" stroke-width="3"/>{% endfor %}</svg><div class="chart-labels"><span>Jan</span><span>Mar</span><span>May</span><span>Jul</span><span>Sep</span><span>Nov</span></div></div></article>
    <article class="card fee-card"><div class="panel-heading"><div><h2>Fee Collection</h2><p>Current period</p></div></div><div class="donut-wrap"><div class="donut" style="--value:{{ analytics.get('Fee Collection', 0) if analytics else 0 }}"><div><strong>{{ analytics.get('Fee Collection', 0) if analytics else 0 }}%</strong><span>Collected</span></div></div><ul class="legend"><li><i class="green-dot"></i>Collected</li><li><i class="gold-dot"></i>Pending</li><li><i class="red-dot"></i>Overdue</li></ul></div></article>
  </div>
  <div class="dashboard-grid dashboard-lower">
    <article class="card"><div class="panel-heading"><div><h2>Recent Activity</h2><p>Latest academic records</p></div></div><div class="activity-list">{% for sc,st,su,sub in recent_scores %}<div class="activity-item"><span class="activity-icon">✓</span><div><b>{{ su.full_name }} · {{ sub.name }}</b><small>Score recorded: {{ sc.class_score + sc.exam_score }}%</small></div><time>{{ fmt_dt(sc.updated_at, '%d %b') }}</time></div>{% else %}<div class="empty-state"><b>No recent activity</b><span>New records will appear here.</span></div>{% endfor %}</div></article>
    <article class="card"><div class="panel-heading"><div><h2>Upcoming Events</h2><p>School calendar</p></div></div><div class="event-list">{% for event in upcoming_events %}<div class="event-item"><span class="event-date"><b>{{ fmt_dt(event.event_date, '%d') }}</b>{{ fmt_dt(event.event_date, '%b') }}</span><div><b>{{ event.title }}</b><small>{{ event.audience|title }}</small></div></div>{% else %}<div class="empty-state"><b>No upcoming events</b><span>Add events from the calendar.</span></div>{% endfor %}</div></article>
    <article class="card quick-panel"><div class="panel-heading"><div><h2>Quick Actions</h2><p>Common tasks</p></div></div><div class="quick-list">{% if user.role == 'system_admin' %}<a href="{{ url_for('schools') }}">＋ Add School</a><a href="{{ url_for('audit_logs') }}">◫ Audit Logs</a>{% elif user.role == 'school_admin' %}<a href="{{ url_for('students') }}">＋ Add Student</a><a href="{{ url_for('teachers') }}">＋ Add Teacher</a><a href="{{ url_for('teacher_assignments') }}">▦ Assign Teacher</a><a href="{{ url_for('fees') }}">◈ Record Payment</a><a href="{{ url_for('communications') }}">✉ Send Message</a>{% elif user.role == 'teacher' %}<a href="{{ url_for('attendance') }}">✓ Take Attendance</a><a href="{{ url_for('scores') }}">▤ Record Marks</a><a href="{{ url_for('students') }}">♙ My Students</a>{% elif user.role == 'accountant' %}<a href="{{ url_for('fees') }}">◈ Record Payment</a><a href="{{ url_for('fees') }}">▤ Fee Balances</a>{% elif user.role == 'registrar' or user.role == 'receptionist' %}<a href="{{ url_for('students') }}">＋ New Admission</a><a href="{{ url_for('calendar') }}">▣ Calendar</a>{% elif user.role == 'librarian' %}<a href="{{ url_for('library') }}">＋ Add Resource</a><a href="{{ url_for('library') }}">▤ Catalogue</a>{% endif %}</div></article>
  </div>
  {% if schools %}<article class="card dashboard-table"><div class="panel-heading"><div><h2>Registered Schools</h2><p>Platform tenants</p></div></div><table><tr><th>School</th><th>Academic Period</th><th>Status</th></tr>{% for item in schools %}<tr><td><b>{{ item.name }}</b></td><td>{{ item.academic_year }} {{ item.term }}</td><td><span class="status-pill">{{ 'Ready' if item.onboarded else 'Needs setup' }}</span></td></tr>{% endfor %}</table></article>{% endif %}
</section></div></main>
"""

STUDENT_DASHBOARD_PAGE = """
<main class="wrap app-dashboard"><div class="layout">""" + SIDEBAR + """<section class="dashboard-content">
<header class="page-heading"><div><span class="dashboard-kicker dark">Student workspace</span><h1>Welcome back, {{ user.full_name }}</h1><p>{{ student_class.name if student_class else 'Class not assigned' }} · {{ school.academic_year }} · {{ school.term }}</p></div><a class="btn" href="{{ url_for('student_results') }}">View Report Card</a></header>
<div class="kpi-grid"><article class="kpi-card"><div class="kpi-icon">▣</div><div><span>My Class</span><strong>{{ student_class.name if student_class else '-' }}</strong><small>Current placement</small></div></article><article class="kpi-card"><div class="kpi-icon">◇</div><div><span>My Average</span><strong>{{ average }}%</strong><small>{{ grade(average) if scores else 'No results' }}</small></div></article><article class="kpi-card"><div class="kpi-icon">✓</div><div><span>Attendance</span><strong>{{ attendance_rate }}%</strong><small>{{ attendance.present_days if attendance else 0 }}/{{ attendance.total_days if attendance else 0 }} days</small></div></article><article class="kpi-card"><div class="kpi-icon">◈</div><div><span>Fee Balance</span><strong>GH₵ {{ '%.2f'|format(fee_balance) }}</strong><small>Current period</small></div></article></div>
<div class="dashboard-grid dashboard-lower"><article class="card"><div class="panel-heading"><div><h2>Recent Results</h2><p>Latest subject performance</p></div></div><table><tr><th>Subject</th><th>Total</th><th>Grade</th></tr>{% for score,subject in scores %}<tr><td>{{ subject.name }}</td><td>{{ score.class_score + score.exam_score }}%</td><td><span class="status-pill">{{ grade(score.class_score + score.exam_score) }}</span></td></tr>{% else %}<tr><td colspan="3">No results published yet.</td></tr>{% endfor %}</table></article><article class="card"><div class="panel-heading"><div><h2>Today's Timetable</h2><p>Class periods</p></div></div>{% for row,subject in timetable_rows %}<div class="event-item"><span class="event-date"><b>{{ row.start_time }}</b></span><div><b>{{ subject.name if subject else 'General' }}</b><small>{{ row.day }} · {{ row.room }}</small></div></div>{% else %}<div class="empty-state"><b>No timetable periods</b></div>{% endfor %}</article><article class="card quick-panel"><div class="panel-heading"><div><h2>Quick Links</h2><p>Student services</p></div></div><div class="quick-list"><a href="{{ url_for('student_results') }}">▤ My Results</a><a href="{{ url_for('timetable') }}">▣ Timetable</a><a href="{{ url_for('announcements') }}">♢ Announcements</a><a href="{{ url_for('library') }}">▦ Library</a></div></article></div>
</section></div></main>
"""

REPORT_CARD_PAGE = """
<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="report-sheet">
<style>.attendance-line{border:1px solid #444;border-radius:8px;padding:10px;text-align:center}@media print{@page{size:A4;margin:5mm}.report-sheet{zoom:.78;break-inside:avoid;padding:0!important;border:0!important}.report-sheet th,.report-sheet td{padding:2px!important;font-size:7px!important}.report-brand{padding:6px!important}.report-brand img{width:50px!important;height:50px!important}.student-report-meta{padding:3px!important}.student-report-meta p,.next-term{margin:2px!important}.print-chart{margin:4px 0!important;padding:4px!important}.report-bars{gap:2px!important}.report-bar-row>div{height:6px!important}.report-two-col,.remarks-lines,.signature-promotion{margin-top:4px!important}.remarks-lines p{min-height:12px!important;margin:2px!important}.signature-promotion{margin:4px 0!important;padding:4px!important}.signature-promotion img{max-height:24px!important}}</style>
<div class="report-actions no-print"><button class="btn" onclick="window.print()">Print Report</button>{% if user.role == 'student' %}<a class="btn ghost" href="{{ url_for('student_results_pdf') }}">Download PDF</a>{% endif %}</div>
<header class="academic-report-head"><div class="report-brand">{% if school.crest %}<img src="{{ url_for('uploads',filename=school.crest) }}" alt="{{ school.name }} crest">{% endif %}<div><h1>{{ school.name }}</h1><p>ACADEMIC REPORT CARD</p></div></div><div class="school-contact"><b>School Address:</b> {{ school.address or '-' }}<br><b>Phone:</b> {{ school.phone or '-' }}<br><b>Email:</b> {{ school.email or '-' }}</div></header>
<div class="student-report-meta"><div><p><b>NAME:</b> {{ report_user.full_name }}</p><p><b>CLASS:</b> {{ student_class.name if student_class else '-' }}</p><p><b>ADMISSION NO:</b> {{ student.admission_no }}</p></div><div><p><b>NO. ON ROLL:</b> {{ detail.number_on_roll if detail else 0 }}</p><p><b>TERM:</b> {{ school.term }}</p><p><b>YEAR:</b> {{ school.academic_year }}</p></div></div>
<p class="next-term"><b>NEXT TERM BEGINNING:</b> {{ fmt_dt(detail.next_term_begins,'%d %B %Y') if detail and detail.next_term_begins else '-' }}</p>
<table class="report-subjects"><thead><tr><th>SUBJECT</th><th>CA<br>30%</th><th>EXAM<br>70%</th><th>TOTAL<br>100%</th><th>GRADE</th><th>REMARKS</th></tr></thead><tbody>{% for score,subject in rows %}{% set subject_total=score.class_score+score.exam_score %}<tr><td>{{ subject.name }}</td><td>{{ score.class_score }}</td><td>{{ score.exam_score }}</td><td>{{ subject_total }}</td><td>{{ grade(subject_total) }}</td><td>{{ score.remarks or grade_info(subject_total).interpretation }}</td></tr>{% else %}<tr><td colspan="6">No subject results entered.</td></tr>{% endfor %}<tr class="report-total"><td>TOTAL / AVERAGE</td><td colspan="2"></td><td>{{ report_total }} / {{ average }}%</td><td>{{ overall.grade }}</td><td>{{ overall.interpretation }}</td></tr></tbody></table>
<section class="print-chart"><h2>SUBJECT PERFORMANCE GRAPH</h2><div class="report-bars">{% for score,subject in rows %}<div class="report-bar-row"><span>{{ subject.name }}</span><div><i style="width:{{ [score.class_score+score.exam_score,100]|min }}%"></i></div><b>{{ score.class_score+score.exam_score }}%</b></div>{% endfor %}</div></section>
<table class="report-summary"><tr><th colspan="7">SUMMARY</th></tr><tr><th>DETAIL</th><th>1ST TERM</th><th>2ND TERM</th><th>3RD TERM</th><th>TOTAL</th><th>AVERAGE</th><th>POSITION</th></tr><tr><td>TOTAL MARKS OBTAINED</td>{% for item in term_summary %}<td>{{ item.total }}</td>{% endfor %}<td>{{ yearly_total }}</td><td>{{ yearly_average }}</td><td>{{ position or '-' }}</td></tr><tr><td>PERCENTAGE (%)</td>{% for item in term_summary %}<td>{{ item.average }}</td>{% endfor %}<td colspan="3"></td></tr></table>
<div class="report-two-col"><div><h3>ATTENDANCE</h3><p class="attendance-line"><b>Days Present:</b> {{ attendance.present_days if attendance else 0 }} &nbsp; out of &nbsp; <b>Overall School Days:</b> {{ attendance.total_days if attendance else 0 }}</p></div><div><h3>KEY TO GRADES</h3><table><tr><th>GRADE</th><th>RANGE</th><th>REMARKS</th></tr><tr><td>A1</td><td>80-100</td><td>Excellent</td></tr><tr><td>B2/B3</td><td>65-79</td><td>Good</td></tr><tr><td>C4-C6</td><td>50-64</td><td>Credit</td></tr><tr><td>D7/E8</td><td>40-49</td><td>Pass</td></tr><tr><td>F9</td><td>0-39</td><td>Fail</td></tr></table></div></div>
<div class="remarks-lines"><p><b>CLASS TEACHER'S REMARKS:</b> {{ detail.class_teacher_remarks if detail else overall.interpretation }}</p><p><b>HEAD TEACHER'S REMARKS:</b> {{ detail.head_teacher_remarks if detail else '' }}</p><p><b>INTEREST:</b> {{ detail.interest if detail else '' }} &nbsp; <b>ATTITUDE:</b> {{ detail.attitude if detail else '' }} &nbsp; <b>CONDUCT:</b> {{ conduct or 'Good' }}</p></div>
<div class="signature-promotion"><div><b>{{ school.head_title or 'Head Teacher' }}</b>{% if school.head_signature %}<img src="{{ url_for('uploads',filename=school.head_signature) }}" alt="Head signature">{% endif %}<span>{{ school.head_name }}</span></div><div><b>PROMOTED TO</b><strong>{{ student_class.name if student.promotion_note else '—' }}</strong><span>{{ student.promotion_note or 'Not promoted' }}</span></div></div>
<div class="report-two-col fees-sign"><div><h3>FEES</h3><p><b>Current balance:</b> GHS {{ ((fees.amount_due-fees.amount_paid) if fees else fee_breakdown_total) }}</p></div><div><b>Date:</b> {{ now.strftime('%d / %m / %Y') }}</div></div>
</section></div></main>
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
        return {"user": current_user(), "school": current_school(), "role_label": role_label, "now": datetime.now(), "csrf_token": csrf_token, "grade": grade, "grade_info": grade_info, "fmt_dt": fmt_dt}

    @app.route("/uploads/<filename>")
    def uploads(filename):
        user = current_user()
        if not user:
            abort(404)
        allowed = {school.crest for school in School.query if school.crest} | {
            school.head_signature for school in School.query if school.head_signature}
        if filename not in allowed:
            abort(404)
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    @app.route("/favicon.ico")
    def favicon():
        return redirect(url_for("static", filename="smart-school-logo.png"), code=302)

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
            admin = User(school_id=school.id, role="school_admin", full_name=request.form["admin_name"].strip(), username=request.form["username"].strip().lower(
            ), password_hash=generate_password_hash(request.form["password"]), email=request.form.get("email", ""), phone=request.form.get("phone", ""), must_change_password=True)
            db.session.add(admin)
            try:
                db.session.commit()
                create_login_slip(admin, request.form["password"])
                flash(
                    "School account created. Print the login slip and complete setup.", "success")
                return redirect(url_for("login_slip"))
            except Exception:
                db.session.rollback()
                flash(
                    "That school admin username already exists for this school.", "error")
        return render("""<main class="login-shell"><section class="card login-card"><h2>Register a School</h2><p class="muted">Create the first school admin account.</p>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post">{{ csrf() }}{{ field('School Name','school_name', required=true) }}{{ field('Administrator Name','admin_name', required=true) }}{{ field('Admin Username','username', required=true) }}{{ field('Temporary Password','password','password', required=true) }}{{ field('Email','email','email') }}{{ field('Phone','phone') }}<button class="btn">Create School</button></form></section></main>""", title="Register School")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        portal = request.args.get("portal", "admin").lower()
        allowed_roles = LOGIN_AUDIENCES.get(portal)
        if request.method == "POST":
            portal = request.form.get("portal", "admin").lower()
            allowed_roles = LOGIN_AUDIENCES.get(portal)
            identity = request.form["username"].strip().lower()
            if login_is_limited(identity):
                db.session.add(AuditLog(username=identity, action="login_rate_limited",
                               details=f"Rate limited {portal} login", ip_address=client_ip()))
                db.session.commit()
                flash(
                    "Too many login attempts. Please wait before trying again.", "error")
                return render("""<main class="login-shell"><section class="card login-card"><h2>Login temporarily limited</h2><p class="muted">Please wait and try again later.</p><a class="btn ghost" href="{{ url_for('login', portal=portal) }}">Back</a></section></main>""", title="Login limited", portal=portal), 429
            user = User.query.filter_by(username=identity, active=True).first()
            if user and (not allowed_roles or user.role in allowed_roles) and check_password_hash(user.password_hash, request.form["password"]):
                session.clear()
                csrf_token()
                session["user_id"] = user.id
                session.permanent = True
                log_action("login", f"{role_label(user.role)} signed in")
                db.session.commit()
                flash(f"Welcome back, {user.full_name}.", "success")
                if user.must_change_password:
                    return redirect(url_for("change_password"))
                return redirect(url_for("dashboard"))
            record_failed_login(identity)
            db.session.add(AuditLog(username=identity, action="failed_login",
                           details=f"Failed {portal} portal login", ip_address=client_ip()))
            db.session.commit()
            flash("Invalid username, password, or portal.", "error")
        portal_label = {"admin": "Admin Login", "teacher": "Teacher Login",
                        "student": "Student Login"}.get(portal, "Login")
        visual = portal if portal in {
            "admin", "teacher", "student"} else "admin"
        return render("""<main class="login-shell"><section class="card login-card"><div class="login-visual {{ visual }}"></div><h2>{{ portal_label }}</h2><p class="muted">Choose the correct portal for your account.</p><div class="actions"><a class="btn ghost" href="{{ url_for('login', portal='admin') }}">Admin</a><a class="btn ghost" href="{{ url_for('login', portal='teacher') }}">Teacher</a><a class="btn ghost" href="{{ url_for('login', portal='student') }}">Student</a><a class="btn ghost" href="{{ url_for('login', portal='parent') }}">Parent</a></div>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post">{{ csrf() }}<input type="hidden" name="portal" value="{{ portal }}">{{ field('Username','username', required=true) }}{{ field('Password','password','password', required=true) }}<button class="btn">Login</button></form><p><a class="btn ghost" href="{{ url_for('reset_password', portal=portal) }}">Reset Password</a></p><p class="muted">Use the credentials issued by your school administrator.</p></section></main>""", title=portal_label, portal=portal, portal_label=portal_label, visual=visual)

    @app.route("/reset-password", methods=["GET", "POST"])
    def reset_password():
        portal = request.args.get("portal", "admin")
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            db.session.add(AuditLog(username=username, action="password_reset_requested",
                           details="Reset request recorded; administrator verification required", ip_address=client_ip()))
            db.session.commit()
            flash("If that account exists, your school administrator will verify the request and issue a temporary password.", "success")
        portal_label = {"admin": "Admin", "teacher": "Teacher",
                        "student": "Student"}.get(portal, "Account")
        return render("""<main class="login-shell"><section class="card login-card"><h2>Request {{ portal_label }} Password Reset</h2><p class="muted">For your protection, resets are verified by a school administrator. Enter your username to record a request.</p>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post">{{ csrf() }}<input type="hidden" name="portal" value="{{ portal }}">{{ field('Username','username', required=true) }}<button class="btn green">Request Reset</button></form><p><a class="btn ghost" href="{{ url_for('login', portal=portal) }}">Back to Login</a></p></section></main>""", title="Reset Password", portal=portal, portal_label=portal_label)

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
                log_action("change_password", "User changed password")
                db.session.commit()
                flash("Password changed successfully.", "success")
                return redirect(url_for("dashboard"))
        return render("""<main class="login-shell"><section class="card login-card"><h2>Change Password</h2><p class="muted">Create a private password before using your account.</p>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post">{{ csrf() }}{{ field('Current Temporary Password','current_password','password', required=true) }}{{ field('New Password','new_password','password', required=true) }}{{ field('Confirm New Password','confirm_password','password', required=true) }}<button class="btn green">Save Password</button></form></section></main>""", title="Change Password")

    @app.route("/logout")
    def logout():
        if current_user():
            log_action("logout", "User signed out")
            db.session.commit()
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
        school = current_school()
        if user.role != "system_admin" and school and not school.onboarded:
            return redirect(url_for("onboarding"))
        if user.role == "student":
            student = Student.query.filter_by(
                user_id=user.id, school_id=user.school_id).first()
            scores = db.session.query(Score, Subject).join(Subject, Score.subject_id == Subject.id).filter(Score.school_id == user.school_id, Score.student_id ==
                                                                                                           student.id, Score.term == school.term, Score.academic_year == school.academic_year).order_by(Score.updated_at.desc()).limit(8).all() if student else []
            attendance = Attendance.query.filter_by(school_id=user.school_id, student_id=student.id,
                                                    term=school.term, academic_year=school.academic_year).first() if student else None
            fee = Fee.query.filter_by(school_id=user.school_id, student_id=student.id,
                                      term=school.term, academic_year=school.academic_year).first() if student else None
            total = sum(score.class_score +
                        score.exam_score for score, _ in scores)
            average = round(total / len(scores), 1) if scores else 0
            attendance_rate = round(attendance.present_days / attendance.total_days *
                                    100, 1) if attendance and attendance.total_days else 0
            fee_balance = max(
                (fee.amount_due - fee.amount_paid) if fee else 0, 0)
            student_class = db.session.get(
                ClassRoom, student.class_id) if student and student.class_id else None
            timetable_rows = db.session.query(Timetable, Subject).outerjoin(Subject, Timetable.subject_id == Subject.id).filter(
                Timetable.school_id == user.school_id, Timetable.class_id == student.class_id).order_by(Timetable.day, Timetable.start_time).limit(6).all() if student else []
            return render(STUDENT_DASHBOARD_PAGE, title="Student Dashboard", student=student, student_class=student_class, scores=scores, attendance=attendance, average=average, attendance_rate=attendance_rate, fee_balance=fee_balance, timetable_rows=timetable_rows)
        if user.role == "parent":
            return redirect(url_for("parent_portal"))
        sid = user.school_id
        if user.role == "system_admin":
            stats = {"Schools": School.query.count(), "Users": User.query.count(
            ), "Students": Student.query.count(), "Teachers": User.query.filter_by(role="teacher").count()}
        elif user.role == "teacher":
            stats = {"My Subjects": len(teacher_subject_ids(user)), "Scores Entered": Score.query.filter_by(school_id=sid, teacher_id=user.id).count(), "Classes": len(
                teacher_class_ids(user)), "Notices": Announcement.query.filter(Announcement.school_id == sid, Announcement.audience.in_(["all", "teacher"])).count()}
        elif user.role == "accountant":
            fee_rows = Fee.query.filter_by(
                school_id=sid, term=school.term, academic_year=school.academic_year).all()
            due = sum(row.amount_due for row in fee_rows)
            paid = sum(row.amount_paid for row in fee_rows)
            stats = {"Fees Collected": f"GH₵ {paid:,.2f}", "Outstanding": f"GH₵ {max(due - paid, 0):,.2f}", "Fee Records": len(
                fee_rows), "Students": Student.query.filter_by(school_id=sid).count()}
        elif user.role == "registrar":
            stats = {"Students": Student.query.filter_by(school_id=sid).count(), "Classes": ClassRoom.query.filter_by(school_id=sid).count(
            ), "New Admissions": Student.query.filter_by(school_id=sid).count(), "Guardians": Student.query.filter(Student.school_id == sid, Student.guardian_phone != "").count()}
        else:
            stats = {"Students": Student.query.filter_by(school_id=sid).count(), "Teachers": User.query.filter_by(school_id=sid, role="teacher").count(
            ), "Classes": ClassRoom.query.filter_by(school_id=sid).count(), "Subjects": Subject.query.filter_by(school_id=sid).count()}
        schools = School.query.order_by(School.created_at.desc()).limit(
            8).all() if user.role == "system_admin" else []
        recent_scores_query = db.session.query(Score, Student, User, Subject).join(Student, Score.student_id == Student.id).join(
            User, Student.user_id == User.id).join(Subject, Score.subject_id == Subject.id).filter(Score.school_id == sid)
        if user.role == "teacher":
            subject_ids = teacher_subject_ids(user)
            class_ids = teacher_class_ids(user)
            recent_scores_query = recent_scores_query.filter(Student.class_id.in_(class_ids), Score.subject_id.in_(
                subject_ids)) if class_ids and subject_ids else recent_scores_query.filter(False)
        recent_scores = recent_scores_query.order_by(Score.updated_at.desc()).limit(
            8).all() if user.role in {"school_admin", "teacher"} else []
        analytics = {}
        grade_bands = []
        upcoming_events = []
        if user.role in {"school_admin", "accountant"}:
            attendance_rows = Attendance.query.filter_by(
                school_id=sid, term=school.term, academic_year=school.academic_year).all()
            present = sum(r.present_days for r in attendance_rows)
            possible = sum(r.total_days for r in attendance_rows)
            fees = Fee.query.filter_by(
                school_id=sid, term=school.term, academic_year=school.academic_year).all()
            due = sum(r.amount_due for r in fees)
            paid = sum(r.amount_paid for r in fees)
            scores_all = Score.query.filter_by(
                school_id=sid, term=school.term, academic_year=school.academic_year).all()
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
            upcoming_events = SchoolEvent.query.filter(SchoolEvent.school_id == sid, SchoolEvent.event_date >= datetime.utcnow(
            ).date(), SchoolEvent.audience.in_(["all", user.role])).order_by(SchoolEvent.event_date).limit(5).all()
        sms_api_url = school.sms_api_url if school and school.sms_api_url else os.getenv(
            "SMS_API_URL", "")
        return render(DASHBOARD_PAGE, title="Dashboard", stats=stats, schools=schools, recent_scores=recent_scores, analytics=analytics, grade_bands=grade_bands, upcoming_events=upcoming_events, sms_api_url=sms_api_url)
        return render("""<main class="wrap">{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card dashboard-hero"><span class="dashboard-kicker">School Operations Centre</span><h2>{{ school.name if school else 'System Dashboard' }}</h2><p class="muted">{{ school.academic_year ~ ' ' ~ school.term if school else 'All registered schools and users' }}</p><div class="feature-strip"><div><b>Learning</b><br><span class="muted">Classes, subjects, scores, reports</span></div><div><b>Operations</b><br><span class="muted">Attendance, fees, timetable</span></div><div><b>Communication</b><br><span class="muted">Notices, calendar, library</span></div></div></article><div class="grid cols-4">{% for name, value in stats.items() %}<article class="card stat metric-card"><span>{{ name }}</span><b>{{ value }}</b></article>{% endfor %}</div>{% if user.role == 'school_admin' %}<article class="card sms-config"><div><span class="dashboard-kicker dark">Communication Setup</span><h3>SMS API connection</h3><p class="muted">Add your provider endpoint to send SMS directly from this school account.</p></div><form method="post" action="{{ url_for('save_sms_settings') }}" class="sms-config-form">{{ csrf() }}<label>SMS API URL<input type="url" name="sms_api_url" value="{{ sms_api_url }}" placeholder="https://api.your-sms-provider.com/send"></label><div class="sms-config-actions"><span class="connection-status {{ 'connected' if sms_api_url else 'not-connected' }}">{{ 'Configured' if sms_api_url else 'Not configured' }}</span><button class="btn green">Save SMS URL</button></div></form></article>{% endif %}{% if analytics %}<div class="grid cols-4">{% for name, value in analytics.items() %}<article class="card metric-card"><span>{{ name }}</span><b>{% if name == 'Pending Fees' %}{{ value }}{% else %}{{ value }}%{% endif %}</b>{% if name != 'Pending Fees' %}<div class="progress"><span style="width:{{ [value,100]|min }}%"></span></div>{% endif %}</article>{% endfor %}</div><article class="card"><h3>Academic Performance Bands</h3><table><tr><th>Grade Band</th><th>Entries</th></tr>{% for label, count in grade_bands %}<tr><td>{{ label }}</td><td>{{ count }}</td></tr>{% endfor %}</table></article><article class="card quick-actions"><h3>Quick Actions</h3><div class="grid cols-4"><a class="btn ghost" href="{{ url_for('students') }}">Students</a><a class="btn ghost" href="{{ url_for('scores') }}">Exams</a><a class="btn ghost" href="{{ url_for('fees') }}">Fees</a><a class="btn ghost" href="{{ url_for('calendar') }}">Calendar</a></div></article>{% endif %}{% if upcoming_events %}<article class="card"><h3>Upcoming School Calendar</h3><table><tr><th>Date</th><th>Event</th><th>Audience</th></tr>{% for e in upcoming_events %}<tr><td>{{ fmt_dt(e.event_date, '%d %b %Y') }}</td><td>{{ e.title }}</td><td>{{ e.audience|title }}</td></tr>{% endfor %}</table></article>{% endif %}{% if schools %}<article class="card"><h3>Registered Schools</h3><table><tr><th>School</th><th>Academic Year</th><th>Status</th></tr>{% for s in schools %}<tr><td>{{ s.name }}</td><td>{{ s.academic_year }} {{ s.term }}</td><td>{{ 'Ready' if s.onboarded else 'Needs setup' }}</td></tr>{% endfor %}</table></article>{% endif %}{% if recent_scores %}<article class="card"><h3>Recent Scores</h3><table><tr><th>Student</th><th>Subject</th><th>Total</th><th>Grade</th></tr>{% for sc, st, su, sub in recent_scores %}{% set total=sc.class_score + sc.exam_score %}<tr><td>{{ su.full_name }} <span class="muted">{{ st.admission_no }}</span></td><td>{{ sub.name }}</td><td>{{ total }}</td><td>{{ grade(total) }}</td></tr>{% endfor %}</table></article>{% endif %}</section></div></main>""", title="Dashboard", stats=stats, schools=schools, recent_scores=recent_scores, analytics=analytics, grade_bands=grade_bands, upcoming_events=upcoming_events, sms_api_url=sms_api_url)

    @app.route("/sms-settings", methods=["POST"])
    @login_required("school_admin")
    @school_required
    def save_sms_settings():
        sms_api_url = request.form.get("sms_api_url", "").strip()
        if sms_api_url and not sms_api_url.startswith(("https://", "http://")):
            flash(
                "Enter a valid SMS API URL beginning with http:// or https://.", "error")
            return redirect(url_for("dashboard"))
        school = current_school()
        school.sms_api_url = sms_api_url or "https://sms.nalosolutions.com/smsbackend/Resl_Nalo/send-message/"
        log_action("sms_api_url_updated", "SMS API URL updated")
        db.session.commit()
        flash("SMS API URL saved successfully.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/schools", methods=["GET", "POST"])
    @login_required("system_admin")
    def schools():
        if request.method == "POST" and request.form.get("action") == "delete":
            school = db.session.get(School, int(request.form["school_id"]))
            if school:
                delete_school_records(school.id)
                log_action("delete_school", f"Deleted school: {school.name}")
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
            school.head_title = request.form.get(
                "head_title", "Head of School")
            school.academic_year = request.form.get("academic_year", "")
            school.term = request.form.get("term", "")
            school.crest = save_crest(
                request.files.get("crest")) or school.crest
            school.head_signature = save_crest(request.files.get(
                "head_signature")) or school.head_signature
            school.onboarded = True
            db.session.commit()
            flash("School profile saved.", "success")
            return redirect(url_for("dashboard"))
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="card"><h2>School Profile Setup</h2><form method="post" enctype="multipart/form-data" class="grid cols-2">{{ csrf() }}{{ field('School Name','name', value=school.name) }}{{ field('Motto','motto', value=school.motto) }}{{ field('Phone','phone', value=school.phone) }}{{ field('Email','email','email', school.email) }}{{ field('Academic Year','academic_year', value=school.academic_year, placeholder='2026/2027') }}{{ field('Term','term', value=school.term, placeholder='Term 1') }}{{ field('Head Name','head_name', value=school.head_name) }}{{ field('Head Title','head_title', value=school.head_title or 'Head of School') }}<label>School Crest<input name="crest" type="file" accept="image/*"></label><label>Head Signature<input name="head_signature" type="file" accept="image/*"></label><label style="grid-column:1/-1">Address<textarea name="address">{{ school.address }}</textarea></label><button class="btn green">Save School Profile</button></form></section></div></main>""", title="School Profile")

    @app.route("/students", methods=["GET", "POST"])
    @login_required("school_admin", "registrar", "receptionist", "teacher")
    @school_required
    def students():
        user = current_user()
        sid = user.school_id
        class_ids = teacher_class_ids(user)
        if request.method == "POST":
            action = request.form.get("action")
            if user.role in {"school_admin", "teacher"} and action == "delete":
                student_query = Student.query.filter_by(
                    id=int(request.form["student_id"]), school_id=sid)
                if user.role == "teacher":
                    class_ids = teacher_class_ids(user)
                    student_query = student_query.filter(Student.class_id.in_(
                        class_ids)) if class_ids else student_query.filter(False)
                student = student_query.first()
                if student:
                    linked_user = db.session.get(User, student.user_id)
                    db.session.delete(student)
                    if linked_user:
                        db.session.delete(linked_user)
                    log_action("delete_student",
                               f"Deleted student {student.admission_no}")
                    db.session.commit()
                    flash("Student account and records deleted.", "success")
            elif user.role in {"school_admin", "teacher"} and action == "promote":
                student = Student.query.filter_by(id=safe_int(
                    request.form.get("student_id")), school_id=sid).first()
                next_class = ClassRoom.query.filter_by(id=safe_int(
                    request.form.get("next_class_id")), school_id=sid).first()
                if user.role == "teacher" and (not student or student.class_id not in class_ids):
                    abort(403)
                if not student or not next_class or next_class.id == student.class_id:
                    flash("Choose a valid student and a different next class.", "error")
                elif "3" not in (current_school().term or "").lower() and "third" not in (current_school().term or "").lower():
                    flash(
                        "Class promotion is available only during Third Term.", "error")
                else:
                    previous = db.session.get(
                        ClassRoom, student.class_id) if student.class_id else None
                    student.promotion_note = f"Promoted from {previous.name if previous else 'Unassigned'} to {next_class.name}"
                    student.class_id = next_class.id
                    student.promoted_at = datetime.utcnow()
                    log_action(
                        "promote_student", f"{student.admission_no}: {student.promotion_note}")
                    db.session.commit()
                    flash(
                        f"Student promoted successfully to {next_class.name}.", "success")
            elif user.role == "school_admin" and action == "reset_password":
                student = Student.query.filter_by(
                    id=int(request.form["student_id"]), school_id=sid).first()
                linked_user = db.session.get(
                    User, student.user_id) if student else None
                if linked_user:
                    password = generate_temporary_password()
                    linked_user.password_hash = generate_password_hash(
                        password)
                    linked_user.must_change_password = True
                    db.session.commit()
                    create_login_slip(linked_user, password)
                    flash(
                        "Student password reset. Print the new login slip.", "success")
                    return redirect(url_for("login_slip"))
            elif user.role in {"school_admin", "registrar", "receptionist", "teacher"}:
                admission_class_id = class_ids[0] if user.role == "teacher" and class_ids else safe_int(request.form.get("class_id"))
                admission_class = ClassRoom.query.filter_by(
                    id=admission_class_id, school_id=sid).first()
                if not admission_class:
                    flash(
                        "Choose a valid class. Teachers must first be assigned to a class.", "error")
                else:
                    try:
                        password = request.form["password"] or generate_temporary_password(
                        )
                        new_user = User(school_id=sid, role="student", full_name=request.form["full_name"].strip(
                        ), username=request.form["username"].strip().lower(), password_hash=generate_password_hash(password), must_change_password=True)
                        db.session.add(new_user)
                        db.session.flush()
                        guardian_name = request.form.get("guardian_name", "").strip()
                        guardian_email = request.form.get("guardian_email", "").strip().lower()
                        guardian_phone = request.form.get("guardian_phone", "").strip()
                        student_record = Student(school_id=sid, user_id=new_user.id, class_id=admission_class.id, admission_no=request.form["admission_no"].strip().upper(
                        ), guardian_name=guardian_name, guardian_phone=guardian_phone, guardian_email=guardian_email)
                        db.session.add(student_record)
                        db.session.flush()
                        if guardian_name:
                            parent_username = request.form.get("parent_username", "").strip().lower()
                            parent = User.query.filter(
                                User.school_id == sid, User.role == "parent",
                                or_(User.email == guardian_email, User.phone == guardian_phone)
                            ).first() if (guardian_email or guardian_phone) else None
                            if not parent:
                                if not parent_username:
                                    raise ValueError("A parent username is required when guardian details are provided.")
                                parent_password = request.form.get("parent_password") or generate_temporary_password()
                                parent = User(school_id=sid, role="parent", full_name=guardian_name,
                                              username=parent_username, password_hash=generate_password_hash(parent_password),
                                              email=guardian_email, phone=guardian_phone, must_change_password=True)
                                db.session.add(parent)
                                db.session.flush()
                            db.session.add(ParentStudent(school_id=sid, parent_id=parent.id,
                                           student_id=student_record.id, relationship="Guardian"))
                        db.session.commit()
                        create_login_slip(new_user, password)
                        return redirect(url_for("login_slip"))
                    except Exception:
                        db.session.rollback()
                        flash(
                            "Student username or admission number already exists.", "error")
        students_query = get_school_student_query(sid)
        if user.role == "teacher":
            students_query = students_query.filter(Student.class_id.in_(
                class_ids)) if class_ids else students_query.filter(False)
        students = students_query.order_by(User.full_name).all()
        classes = ClassRoom.query.filter_by(
            school_id=sid).order_by(ClassRoom.name).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>{{ 'My Students' if user.role == 'teacher' else 'Students & Admissions' }}</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ csrf() }}{{ field('Full Name','full_name',required=true) }}{{ field('Admission No','admission_no',required=true) }}{{ field('Username','username',required=true) }}{{ field('Temporary Password','password','password', placeholder='Leave blank to auto-generate') }}{% if user.role != 'teacher' %}<label>Class<select name="class_id" required>{% for class_group in classes %}<option value="{{ class_group.id }}">{{ class_group.name }}</option>{% endfor %}</select></label>{% endif %}{{ field('Guardian / Parent Name','guardian_name',required=true) }}{{ field('Guardian Phone','guardian_phone') }}{{ field('Guardian Email','guardian_email','email') }}{{ field('Parent Login Username','parent_username',placeholder='Required for a new parent') }}{{ field('Parent Temporary Password','parent_password','password',placeholder='Leave blank to auto-generate') }}<button class="btn green">Add Student & Link Parent</button></form></article><article class="card"><table><tr><th>Name</th><th>Username</th><th>Admission No</th><th>Class</th><th>Guardian</th><th>Action</th></tr>{% for st, u, c in students %}<tr><td>{{ u.full_name }}</td><td>{{ u.username }}</td><td>{{ st.admission_no }}</td><td>{{ c.name if c else '-' }}</td><td>{{ st.guardian_name }} {{ st.guardian_phone }}</td><td>{% if user.role == 'school_admin' %}<div class="actions"><form method="post">{{ csrf() }}<input type="hidden" name="action" value="reset_password"><input type="hidden" name="student_id" value="{{ st.id }}"><button class="btn ghost">Reset Password</button></form><form method="post" onsubmit="return confirm('Delete this student?')">{{ csrf() }}<input type="hidden" name="action" value="delete"><input type="hidden" name="student_id" value="{{ st.id }}"><button class="btn red">Delete</button></form></div>{% endif %}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Students", students=students, classes=classes)

    @app.route("/promotions", methods=["GET", "POST"])
    @login_required("school_admin", "teacher")
    @school_required
    def promotions():
        user = current_user()
        school = current_school()
        sid = user.school_id
        allowed_classes = teacher_class_ids(user) if user.role == "teacher" else [
            row[0] for row in db.session.query(ClassRoom.id).filter_by(school_id=sid).all()]
        if request.method == "POST":
            student = Student.query.filter_by(id=safe_int(
                request.form.get("student_id")), school_id=sid).first()
            if not student or student.class_id not in allowed_classes:
                abort(403)
            if request.form.get("action") == "delete":
                account = db.session.get(User, student.user_id)
                admission_no = student.admission_no
                db.session.delete(student)
                if account:
                    db.session.delete(account)
                log_action("delete_student",
                           f"Deleted {admission_no} from assigned class")
                db.session.commit()
                flash("Student deleted successfully.", "success")
            else:
                next_class = ClassRoom.query.filter_by(id=safe_int(
                    request.form.get("next_class_id")), school_id=sid).first()
                is_third_term = "3" in (school.term or "").lower(
                ) or "third" in (school.term or "").lower()
                if not is_third_term:
                    flash("Promotion is available only during Third Term.", "error")
                elif not next_class or next_class.id == student.class_id:
                    flash("Select a different valid next class.", "error")
                else:
                    previous = db.session.get(ClassRoom, student.class_id)
                    student.promotion_note = f"Promoted from {previous.name if previous else 'Unassigned'} to {next_class.name}"
                    student.class_id = next_class.id
                    student.promoted_at = datetime.utcnow()
                    log_action(
                        "promote_student", f"{student.admission_no}: {student.promotion_note}")
                    db.session.commit()
                    flash(
                        f"{student.admission_no} promoted to {next_class.name}.", "success")
        query = get_school_student_query(sid)
        query = query.filter(Student.class_id.in_(
            allowed_classes)) if allowed_classes else query.filter(False)
        rows = query.order_by(ClassRoom.name, User.full_name).all()
        classes = ClassRoom.query.filter_by(
            school_id=sid).order_by(ClassRoom.name).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>Third-Term Promotions</h2><p class="muted">Promote students to their next class after Third Term. Their report and portal will immediately show the new class.</p>{% for category,message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ csrf() }}<input type="hidden" name="action" value="promote"><label>Student<select name="student_id" required>{% for student,account,class_group in rows %}<option value="{{ student.id }}">{{ account.full_name }} · {{ class_group.name if class_group else '-' }}</option>{% endfor %}</select></label><label>Next Class<select name="next_class_id" required>{% for class_group in classes %}<option value="{{ class_group.id }}">{{ class_group.name }}</option>{% endfor %}</select></label><button class="btn green">Promote Student</button></form></article><article class="card"><h2>Students in My Classes</h2><table><tr><th>Student</th><th>Admission No</th><th>Current Class</th><th>Promotion Status</th><th>Action</th></tr>{% for student,account,class_group in rows %}<tr><td>{{ account.full_name }}</td><td>{{ student.admission_no }}</td><td>{{ class_group.name if class_group else '-' }}</td><td>{{ student.promotion_note or 'Not promoted' }}</td><td><form method="post" data-confirm="Permanently delete this student and all linked records?">{{ csrf() }}<input type="hidden" name="action" value="delete"><input type="hidden" name="student_id" value="{{ student.id }}"><button class="btn red">Delete</button></form></td></tr>{% endfor %}</table></article></section></div></main>""", title="Promotions", rows=rows, classes=classes)

    @app.route("/users", methods=["GET", "POST"])
    @login_required("school_admin")
    @school_required
    def users():
        user = current_user()
        sid = user.school_id
        if request.method == "POST":
            role = request.form.get("role", "")
            if role not in {"school_admin", "accountant", "registrar", "librarian", "receptionist", "parent"}:
                abort(400)
            password = request.form.get(
                "password") or generate_temporary_password()
            account = User(school_id=sid, role=role, full_name=request.form["full_name"].strip(), username=request.form["username"].strip().lower(
            ), password_hash=generate_password_hash(password), email=request.form.get("email", ""), phone=request.form.get("phone", ""), must_change_password=True)
            try:
                db.session.add(account)
                db.session.commit()
                create_login_slip(account, password)
                return redirect(url_for("login_slip"))
            except Exception:
                db.session.rollback()
                flash("That username is already in use for this school.", "error")
        accounts = User.query.filter(User.school_id == sid, User.role.in_(
            ["school_admin", "accountant", "registrar", "librarian", "receptionist", "parent"])).order_by(User.role, User.full_name).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>User Management</h2><p class="muted">Create secure administrator, finance, operations, library, reception, and parent accounts.</p>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ csrf() }}{{ field('Full Name','full_name', required=true) }}{{ field('Username','username', required=true) }}{{ field('Temporary Password','password','password', placeholder='Leave blank to auto-generate') }}{{ field('Email','email','email') }}{{ field('Phone','phone') }}<label>Role<select name="role"><option value="registrar">Registrar</option><option value="receptionist">Receptionist</option><option value="accountant">Accountant</option><option value="librarian">Librarian</option><option value="parent">Parent</option><option value="school_admin">School Administrator</option></select></label><button class="btn green">Create Account & Print Login Slip</button></form></article><article class="card"><table><tr><th>Name</th><th>Role</th><th>Username</th><th>Contact</th><th>Status</th></tr>{% for account in accounts %}<tr><td>{{ account.full_name }}</td><td>{{ role_label(account.role) }}</td><td>{{ account.username }}</td><td>{{ account.email or account.phone or '-' }}</td><td><span class="status-pill">{{ 'Active' if account.active else 'Inactive' }}</span></td></tr>{% else %}<tr><td colspan="5">No staff accounts have been created.</td></tr>{% endfor %}</table></article></section></div></main>""", title="User Management", accounts=accounts)

    @app.route("/parent-links", methods=["GET", "POST"])
    @login_required("school_admin")
    @school_required
    def parent_links():
        user = current_user()
        sid = user.school_id
        if request.method == "POST":
            action = request.form.get("action")
            if action == "create_parent":
                password = request.form.get(
                    "password") or generate_temporary_password()
                parent = User(school_id=sid, role="parent", full_name=request.form.get("full_name", "").strip(), username=request.form.get("username", "").strip().lower(
                ), password_hash=generate_password_hash(password), email=request.form.get("email", "").strip(), phone=request.form.get("phone", "").strip(), must_change_password=True)
                if not parent.full_name or not parent.username:
                    flash("Parent name and username are required.", "error")
                else:
                    try:
                        db.session.add(parent)
                        db.session.commit()
                        create_login_slip(parent, password)
                        flash(
                            "Parent account created. Print the login slip, then link the parent to a child.", "success")
                        return redirect(url_for("login_slip"))
                    except Exception:
                        db.session.rollback()
                        flash("That parent username is already in use.", "error")
            elif action == "remove":
                ParentStudent.query.filter_by(id=safe_int(
                    request.form.get("link_id")), school_id=sid).delete()
                db.session.commit()
                flash("Parent link removed.", "success")
            else:
                parent = User.query.filter_by(id=safe_int(request.form.get(
                    "parent_id")), school_id=sid, role="parent", active=True).first()
                student = Student.query.filter_by(id=safe_int(
                    request.form.get("student_id")), school_id=sid).first()
                if not parent or not student:
                    abort(400)
                try:
                    db.session.add(ParentStudent(school_id=sid, parent_id=parent.id, student_id=student.id,
                                   relationship=request.form.get("relationship", "Guardian").strip() or "Guardian"))
                    db.session.commit()
                    flash("Parent linked to student successfully.", "success")
                except Exception:
                    db.session.rollback()
                    flash("That parent is already linked to this student.", "error")
        parents = User.query.filter_by(
            school_id=sid, role="parent", active=True).order_by(User.full_name).all()
        students = db.session.query(Student, User).join(User, Student.user_id == User.id).filter(
            Student.school_id == sid).order_by(User.full_name).all()
        links = db.session.query(ParentStudent, User, Student).join(User, ParentStudent.parent_id == User.id).join(
            Student, ParentStudent.student_id == Student.id).filter(ParentStudent.school_id == sid).all()
        # Use explicit child lookup to avoid leaking users across schools and keep SQLite/PostgreSQL behavior identical.
        rows = [(link, parent, db.session.get(User, student.user_id), student)
                for link, parent, student in links]
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>Parents & Guardians</h2><p class="muted">Create parent login accounts, then link each parent only to their children.</p><form method="post" class="grid cols-3"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><input type="hidden" name="action" value="create_parent">{{ field('Parent Full Name','full_name', required=true) }}{{ field('Username','username', required=true) }}{{ field('Temporary Password','password','password', placeholder='Leave blank to generate') }}{{ field('Email','email','email') }}{{ field('Phone','phone') }}<button class="btn green">Create Parent Account</button></form><hr style="border:0;border-top:1px solid var(--line);margin:24px 0"><h3>Link Parent to Child</h3>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ csrf() }}<label>Parent<select name="parent_id" required>{% for parent in parents %}<option value="{{ parent.id }}">{{ parent.full_name }}</option>{% endfor %}</select></label><label>Student<select name="student_id" required>{% for student, account in students %}<option value="{{ student.id }}">{{ account.full_name }} · {{ student.admission_no }}</option>{% endfor %}</select></label>{{ field('Relationship','relationship', value='Guardian') }}<button class="btn green">Link Parent</button></form></article><article class="card"><h2>Linked Children</h2><table><tr><th>Parent</th><th>Student</th><th>Admission No</th><th>Relationship</th><th>Action</th></tr>{% for link,parent,child,student in rows %}<tr><td>{{ parent.full_name }}</td><td>{{ child.full_name }}</td><td>{{ student.admission_no }}</td><td>{{ link.relationship }}</td><td><form method="post" data-confirm="Remove this parent link?">{{ csrf() }}<input type="hidden" name="action" value="remove"><input type="hidden" name="link_id" value="{{ link.id }}"><button class="btn red">Remove</button></form></td></tr>{% else %}<tr><td colspan="5">No parent links have been created.</td></tr>{% endfor %}</table></article></section></div></main>""", title="Parents & Guardians", parents=parents, students=students, rows=rows)

    @app.route("/teachers", methods=["GET", "POST"])
    @login_required("school_admin")
    @school_required
    def teachers():
        user = current_user()
        sid = user.school_id
        if request.method == "POST" and user.role == "school_admin":
            action = request.form.get("action")
            if action == "delete":
                teacher = User.query.filter_by(
                    id=int(request.form["teacher_id"]), school_id=sid, role="teacher").first()
                if teacher:
                    ClassRoom.query.filter_by(
                        teacher_id=teacher.id).update({"teacher_id": None})
                    Subject.query.filter_by(teacher_id=teacher.id).update(
                        {"teacher_id": None})
                    Timetable.query.filter_by(
                        teacher_id=teacher.id).update({"teacher_id": None})
                    db.session.delete(teacher)
                    db.session.commit()
                    flash("Teacher account deleted.", "success")
            elif action == "reset_password":
                teacher = User.query.filter_by(
                    id=int(request.form["teacher_id"]), school_id=sid, role="teacher").first()
                if teacher:
                    password = generate_temporary_password()
                    teacher.password_hash = generate_password_hash(password)
                    teacher.must_change_password = True
                    db.session.commit()
                    create_login_slip(teacher, password)
                    flash(
                        "Teacher password reset. Print the new login slip.", "success")
                    return redirect(url_for("login_slip"))
            else:
                password = request.form["password"] or generate_temporary_password(
                )
                teacher = User(school_id=sid, role="teacher", full_name=request.form["full_name"], username=request.form["username"].strip().lower(
                ), password_hash=generate_password_hash(password), email=request.form.get("email", ""), phone=request.form.get("phone", ""), must_change_password=True)
                db.session.add(teacher)
                try:
                    db.session.flush()
                    if request.form.get("class_id"):
                        ClassRoom.query.filter_by(id=int(request.form["class_id"]), school_id=sid).update(
                            {"teacher_id": teacher.id})
                    db.session.commit()
                    create_login_slip(teacher, password)
                    return redirect(url_for("login_slip"))
                except Exception:
                    db.session.rollback()
                    flash("That teacher username already exists.", "error")
        teachers = User.query.filter_by(
            school_id=sid, role="teacher").order_by(User.full_name).all()
        classes = ClassRoom.query.filter_by(
            school_id=sid).order_by(ClassRoom.name).all()
        assignment_rows = db.session.query(TeacherAssignment.teacher_id, ClassRoom.name, Subject.name).join(ClassRoom, TeacherAssignment.class_id == ClassRoom.id).outerjoin(
            Subject, TeacherAssignment.subject_id == Subject.id).filter(TeacherAssignment.school_id == sid).all()
        assigned = {teacher.id: [] for teacher in teachers}
        for teacher_id, class_name, subject_name in assignment_rows:
            assigned.setdefault(teacher_id, []).append(
                f"{class_name} · {subject_name or 'Class teacher'}")
        assigned = {teacher_id: ", ".join(
            values) or "-" for teacher_id, values in assigned.items()}
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>Teachers</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}{% if user.role == 'school_admin' %}<form method="post" class="grid cols-3">{{ csrf() }}{{ field('Full Name','full_name') }}{{ field('Username','username') }}{{ field('Temporary Password','password','password', placeholder='Leave blank to auto-generate') }}{{ field('Email','email','email') }}{{ field('Phone','phone') }}<label>Assign Class<select name="class_id"><option value="">No class yet</option>{% for c in classes %}<option value="{{ c.id }}">{{ c.name }}</option>{% endfor %}</select></label><button class="btn green">Add Teacher & Print Login Slip</button></form>{% endif %}</article><article class="card"><table><tr><th>Name</th><th>Username</th><th>Assigned Class</th><th>Email</th><th>Phone</th><th>Action</th></tr>{% for t in teachers %}<tr><td>{{ t.full_name }}</td><td>{{ t.username }}</td><td>{{ assigned[t.id] }}</td><td>{{ t.email }}</td><td>{{ t.phone }}</td><td>{% if user.role == 'school_admin' %}<div class="actions"><form method="post">{{ csrf() }}<input type="hidden" name="action" value="reset_password"><input type="hidden" name="teacher_id" value="{{ t.id }}"><button class="btn ghost">Reset Password</button></form><form method="post" onsubmit="return confirm('Delete this teacher?')">{{ csrf() }}<input type="hidden" name="action" value="delete"><input type="hidden" name="teacher_id" value="{{ t.id }}"><button class="btn red">Delete</button></form></div>{% endif %}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Teachers", teachers=teachers, classes=classes, assigned=assigned)

    @app.route("/teacher-assignments", methods=["GET", "POST"])
    @login_required("school_admin")
    @school_required
    def teacher_assignments():
        user = current_user()
        sid = user.school_id
        if request.method == "POST":
            if request.form.get("action") == "remove":
                TeacherAssignment.query.filter_by(
                    id=int(request.form["assignment_id"]), school_id=sid).delete()
                db.session.commit()
                flash("Teaching assignment removed.", "success")
                return redirect(url_for("teacher_assignments"))
            teacher = User.query.filter_by(id=safe_int(request.form.get(
                "teacher_id")), school_id=sid, role="teacher", active=True).first()
            class_group = ClassRoom.query.filter_by(id=safe_int(
                request.form.get("class_id")), school_id=sid).first()
            subject = Subject.query.filter_by(id=safe_int(
                request.form.get("subject_id")), school_id=sid).first()
            if not teacher or not class_group or not subject:
                abort(400)
            assignment = TeacherAssignment(school_id=sid, teacher_id=teacher.id, class_id=class_group.id, subject_id=subject.id, section=request.form.get(
                "section", "").strip(), academic_year=request.form.get("academic_year", "").strip(), term=request.form.get("term", "").strip())
            try:
                db.session.add(assignment)
                db.session.commit()
                flash("Teaching assignment saved.", "success")
            except Exception:
                db.session.rollback()
                flash(
                    "That teacher, class, and subject combination already exists.", "error")
        teachers = User.query.filter_by(
            school_id=sid, role="teacher", active=True).order_by(User.full_name).all()
        classes = ClassRoom.query.filter_by(
            school_id=sid).order_by(ClassRoom.name).all()
        subjects = Subject.query.filter_by(
            school_id=sid).order_by(Subject.name).all()
        assignments = db.session.query(TeacherAssignment, User, ClassRoom, Subject).join(User, TeacherAssignment.teacher_id == User.id).join(ClassRoom, TeacherAssignment.class_id == ClassRoom.id).join(
            Subject, TeacherAssignment.subject_id == Subject.id).filter(TeacherAssignment.school_id == sid).order_by(User.full_name, ClassRoom.name, Subject.name).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>Teaching Assignments</h2><p class="muted">Create flexible teacher, class, section, subject, academic-year, and term combinations. A teacher may have any number of assignments.</p>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ csrf() }}<label>Teacher<select name="teacher_id" required>{% for teacher in teachers %}<option value="{{ teacher.id }}">{{ teacher.full_name }}</option>{% endfor %}</select></label><label>Class<select name="class_id" required>{% for class_group in classes %}<option value="{{ class_group.id }}">{{ class_group.name }}</option>{% endfor %}</select></label><label>Subject<select name="subject_id" required>{% for subject in subjects %}<option value="{{ subject.id }}">{{ subject.name }}</option>{% endfor %}</select></label>{{ field('Section','section', placeholder='A, B, Gold') }}{{ field('Academic Year','academic_year', value=school.academic_year, placeholder='2026/2027') }}{{ field('Term','term', value=school.term, placeholder='Term 1') }}<button class="btn green">Save Assignment</button></form></article><article class="card"><div class="table-head"><div><h2>Current Assignments</h2><p class="muted">Teacher access is derived from these records.</p></div><input class="table-search" type="search" placeholder="Search assignments…" aria-label="Search assignments"></div><table><tr><th>Teacher</th><th>Class / Section</th><th>Subject</th><th>Period</th><th>Action</th></tr>{% for assignment, teacher, class_group, subject in assignments %}<tr><td>{{ teacher.full_name }}</td><td>{{ class_group.name }}{% if assignment.section %} · {{ assignment.section }}{% endif %}</td><td>{{ subject.name }}</td><td>{{ assignment.academic_year or 'All years' }} · {{ assignment.term or 'All terms' }}</td><td><form method="post" data-confirm="Remove this teaching assignment?">{{ csrf() }}<input type="hidden" name="action" value="remove"><input type="hidden" name="assignment_id" value="{{ assignment.id }}"><button class="btn red">Remove</button></form></td></tr>{% else %}<tr><td colspan="5"><div class="empty-state"><b>No teaching assignments yet</b><span>Create the first assignment using the form above.</span></div></td></tr>{% endfor %}</table></article></section></div></main>""", title="Teaching Assignments", teachers=teachers, classes=classes, subjects=subjects, assignments=assignments)

    @app.route("/classes-subjects", methods=["GET", "POST"])
    @login_required("school_admin")
    @school_required
    def classes_subjects():
        user = current_user()
        sid = user.school_id
        if request.method == "POST" and user.role == "school_admin":
            try:
                if request.form["action"] == "delete_class":
                    class_id = int(request.form["class_id"])
                    Student.query.filter_by(
                        class_id=class_id, school_id=sid).update({"class_id": None})
                    Timetable.query.filter_by(
                        class_id=class_id, school_id=sid).delete()
                    ClassRoom.query.filter_by(
                        id=class_id, school_id=sid).delete()
                elif request.form["action"] == "delete_subject":
                    subject_id = int(request.form["subject_id"])
                    Score.query.filter_by(
                        subject_id=subject_id, school_id=sid).delete()
                    Timetable.query.filter_by(
                        subject_id=subject_id, school_id=sid).delete()
                    Subject.query.filter_by(
                        id=subject_id, school_id=sid).delete()
                elif request.form["action"] == "class":
                    db.session.add(ClassRoom(
                        school_id=sid, name=request.form["name"], teacher_id=request.form.get("teacher_id") or None))
                elif request.form["action"] == "subject":
                    db.session.add(Subject(school_id=sid, name=request.form["name"], code=request.form.get(
                        "code", ""), teacher_id=request.form.get("teacher_id") or None))
                db.session.commit()
                flash("Saved.", "success")
            except Exception:
                db.session.rollback()
                flash("That class or subject already exists.", "error")
        teachers = User.query.filter_by(
            school_id=sid, role="teacher").order_by(User.full_name).all()
        classes = db.session.query(ClassRoom, User).outerjoin(User, ClassRoom.teacher_id == User.id).filter(
            ClassRoom.school_id == sid).order_by(ClassRoom.name).all()
        subjects = db.session.query(Subject, User).outerjoin(User, Subject.teacher_id == User.id).filter(
            Subject.school_id == sid).order_by(Subject.name).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid cols-2"><article class="card"><h2>Classes</h2>{% if user.role=='school_admin' %}<form method="post">{{ csrf() }}<input type="hidden" name="action" value="class">{{ field('Class Name','name') }}<label>Class Teacher<select name="teacher_id"><option value="">None</option>{% for t in teachers %}<option value="{{ t.id }}">{{ t.full_name }}</option>{% endfor %}</select></label><button class="btn green">Add Class</button></form>{% endif %}<table><tr><th>Class</th><th>Teacher</th><th>Action</th></tr>{% for c,t in classes %}<tr><td>{{ c.name }}</td><td>{{ t.full_name if t else '-' }}</td><td><form method="post" onsubmit="return confirm('Delete this class? Students will be unassigned.')">{{ csrf() }}<input type="hidden" name="action" value="delete_class"><input type="hidden" name="class_id" value="{{ c.id }}"><button class="btn red">Delete</button></form></td></tr>{% endfor %}</table></article><article class="card"><h2>Subjects</h2>{% if user.role=='school_admin' %}<form method="post">{{ csrf() }}<input type="hidden" name="action" value="subject">{{ field('Subject Name','name') }}{{ field('Code','code') }}<label>Teacher<select name="teacher_id"><option value="">None</option>{% for t in teachers %}<option value="{{ t.id }}">{{ t.full_name }}</option>{% endfor %}</select></label><button class="btn green">Add Subject</button></form>{% endif %}<table><tr><th>Subject</th><th>Code</th><th>Teacher</th><th>Action</th></tr>{% for s,t in subjects %}<tr><td>{{ s.name }}</td><td>{{ s.code }}</td><td>{{ t.full_name if t else '-' }}</td><td><form method="post" onsubmit="return confirm('Delete this subject and its scores?')">{{ csrf() }}<input type="hidden" name="action" value="delete_subject"><input type="hidden" name="subject_id" value="{{ s.id }}"><button class="btn red">Delete</button></form></td></tr>{% endfor %}</table></article></section></div></main>""", title="Classes & Subjects", teachers=teachers, classes=classes, subjects=subjects)

    @app.route("/report-details", methods=["GET", "POST"])
    @login_required("school_admin", "teacher")
    @school_required
    def report_details():
        user = current_user()
        school = current_school()
        sid = user.school_id
        class_ids = teacher_class_ids(user) if user.role == "teacher" else [row[0] for row in db.session.query(ClassRoom.id).filter_by(school_id=sid).all()]
        if request.method == "POST":
            student = Student.query.filter_by(id=safe_int(request.form.get("student_id")), school_id=sid).first()
            if not student or student.class_id not in class_ids:
                abort(403)
            term = request.form.get("term") or school.term
            year = request.form.get("academic_year") or school.academic_year
            detail = StudentReportDetail.query.filter_by(student_id=student.id, term=term, academic_year=year).first() or StudentReportDetail(school_id=sid, student_id=student.id, term=term, academic_year=year)
            attendance = Attendance.query.filter_by(student_id=student.id, term=term, academic_year=year).first() or Attendance(school_id=sid, student_id=student.id, term=term, academic_year=year)
            try:
                attendance.present_days = max(0, safe_int(request.form.get("present_days")))
                attendance.total_days = max(0, safe_int(request.form.get("total_days")))
                detail.absent_days = max(0, safe_int(request.form.get("absent_days")))
                detail.late_days = max(0, safe_int(request.form.get("late_days")))
                detail.number_on_roll = max(0, safe_int(request.form.get("number_on_roll")))
                detail.next_term_begins = datetime.strptime(request.form["next_term_begins"], "%Y-%m-%d").date() if request.form.get("next_term_begins") else None
                for name in ["class_teacher_remarks", "head_teacher_remarks", "interest", "attitude"]:
                    setattr(detail, name, request.form.get(name, "").strip())
                for name in ["arrears", "tuition_fees", "pta_dues", "medical_dues", "building_fund"]:
                    setattr(detail, name, max(0, safe_number(request.form.get(name))))
                detail.updated_by = user.id
                detail.updated_at = datetime.utcnow()
                db.session.add_all([detail, attendance])
                log_action("update_report_details", f"Updated report details for {student.admission_no}, {term} {year}")
                db.session.commit()
                flash("Report details saved successfully.", "success")
            except ValueError:
                db.session.rollback()
                flash("Please check the date and numeric report fields.", "error")
        students = db.session.query(Student, User, ClassRoom).join(User, Student.user_id == User.id).outerjoin(ClassRoom, Student.class_id == ClassRoom.id).filter(Student.school_id == sid, Student.class_id.in_(class_ids)).order_by(ClassRoom.name, User.full_name).all() if class_ids else []
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>Complete Report Card Details</h2><p class="muted">Subject marks come from Scores. Complete attendance, remarks, next-term date and fee details here.</p>{% for category,message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ csrf() }}<label>Student<select name="student_id" required>{% for student,account,class_group in students %}<option value="{{ student.id }}">{{ account.full_name }} · {{ class_group.name if class_group else '-' }}</option>{% endfor %}</select></label>{{ field('Term','term',value=school.term,required=true) }}{{ field('Academic Year','academic_year',value=school.academic_year,required=true) }}{{ field('Number on Roll','number_on_roll','number') }}{{ field('Next Term Begins','next_term_begins','date') }}{{ field('Present Days','present_days','number') }}{{ field('Total School Days','total_days','number') }}{{ field('Absent Days','absent_days','number') }}{{ field('Late Days','late_days','number') }}{{ field('Interest','interest') }}{{ field('Attitude','attitude') }}<label>Class Teacher Remarks<textarea name="class_teacher_remarks"></textarea></label><label>Head Teacher Remarks<textarea name="head_teacher_remarks"></textarea></label>{{ field('Arrears From Last Term','arrears','number') }}{{ field('Tuition / School Fees','tuition_fees','number') }}{{ field('PTA Dues','pta_dues','number') }}{{ field('Medical Dues','medical_dues','number') }}{{ field('Building Fund','building_fund','number') }}<button class="btn green">Save Report Details</button></form></article></section></div></main>""", title="Report Details", students=students)

    @app.route("/scores", methods=["GET", "POST"])
    @login_required("school_admin", "teacher")
    @school_required
    def scores():
        user = current_user()
        school = current_school()
        sid = user.school_id
        if request.method == "POST":
            try:
                student = Student.query.filter_by(
                    id=int(request.form["student_id"]), school_id=sid).first()
                subject_query = Subject.query.filter_by(
                    id=int(request.form["subject_id"]), school_id=sid)
                if user.role == "teacher":
                    class_ids = teacher_class_ids(user)
                    subject_ids = teacher_subject_ids(user)
                    if not student or not teacher_can_access(user, student.class_id, int(request.form["subject_id"]), request.form.get("academic_year") or school.academic_year, request.form.get("term") or school.term):
                        raise ValueError(
                            "You can only enter scores for your assigned class and subject.")
                    subject_query = subject_query.filter(
                        Subject.id.in_(subject_ids))
                subject = subject_query.first()
                if not student or not subject:
                    raise ValueError(
                        "Please choose a valid student and subject.")
                score = Score.query.filter_by(student_id=student.id, subject_id=subject.id, term=request.form.get("term") or school.term, academic_year=request.form.get("academic_year") or school.academic_year).first(
                ) or Score(school_id=sid, student_id=student.id, subject_id=subject.id, term=request.form.get("term") or school.term, academic_year=request.form.get("academic_year") or school.academic_year)
                score.class_score = clamp_score(
                    request.form.get("class_score"), 30)
                score.exam_score = clamp_score(
                    request.form.get("exam_score"), 70)
                score.conduct = request.form.get("conduct", "")
                score.position = request.form.get("position", "")
                score.remarks = request.form.get("remarks", "")
                score.teacher_id = user.id
                score.updated_at = datetime.utcnow()
                db.session.add(score)
                db.session.commit()
                flash("Score saved.", "success")
            except Exception as exc:
                db.session.rollback()
                flash(str(exc) if isinstance(exc, ValueError)
                      else "Score could not be saved. Please check the entries and try again.", "error")
        students_query = db.session.query(Student, User).join(
            User, Student.user_id == User.id).filter(Student.school_id == sid)
        if user.role == "teacher":
            class_ids = teacher_class_ids(user)
            students_query = students_query.filter(Student.class_id.in_(
                class_ids)) if class_ids else students_query.filter(False)
        students = students_query.order_by(User.full_name).all()
        subjects_query = Subject.query.filter_by(school_id=sid)
        if user.role == "teacher":
            subject_ids = teacher_subject_ids(user)
            subjects_query = subjects_query.filter(Subject.id.in_(
                subject_ids)) if subject_ids else subjects_query.filter(False)
        subjects = subjects_query.order_by(Subject.name).all()
        scores_query = db.session.query(Score, Student, User, Subject).join(Student, Score.student_id == Student.id).join(
            User, Student.user_id == User.id).join(Subject, Score.subject_id == Subject.id).filter(Score.school_id == sid)
        if user.role == "teacher":
            class_ids = teacher_class_ids(user)
            subject_ids = teacher_subject_ids(user)
            scores_query = scores_query.filter(Student.class_id.in_(class_ids), Score.subject_id.in_(
                subject_ids)) if class_ids and subject_ids else scores_query.filter(False)
        scores = scores_query.order_by(Score.updated_at.desc()).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>Examination Scores</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ csrf() }}<label>Student<select name="student_id" required>{% for st,u in students %}<option value="{{ st.id }}">{{ u.full_name }} - {{ st.admission_no }}</option>{% endfor %}</select></label><label>Subject<select name="subject_id" required>{% for s in subjects %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}</select></label>{{ field('CA Score / 30','class_score','number') }}{{ field('Exam Score / 70','exam_score','number') }}{{ field('Term','term', value=school.term) }}{{ field('Academic Year','academic_year', value=school.academic_year) }}{{ field('Position','position', placeholder='1st, 2nd, 3rd') }}{{ field('Conduct','conduct', placeholder='Excellent, Good') }}{{ field('Remarks','remarks') }}<button class="btn green">Save Score</button></form></article><article class="card"><table><tr><th>Student</th><th>Subject</th><th>Total</th><th>Grade</th><th>Meaning</th><th>Remarks</th><th>Action</th></tr>{% for sc,st,u,sub in scores %}{% set total=sc.class_score+sc.exam_score %}{% set info=grade_info(total) %}<tr><td>{{ u.full_name }} <span class="muted">{{ st.admission_no }}</span></td><td>{{ sub.name }}</td><td>{{ total }}</td><td>{{ info.grade }}</td><td>{{ info.interpretation }}</td><td>{{ sc.remarks }}</td><td><button type="button" class="btn ghost score-edit" data-student="{{ st.id }}" data-subject="{{ sub.id }}" data-class-score="{{ sc.class_score }}" data-exam-score="{{ sc.exam_score }}" data-term="{{ sc.term }}" data-year="{{ sc.academic_year }}" data-position="{{ sc.position }}" data-conduct="{{ sc.conduct }}" data-remarks="{{ sc.remarks }}">Edit</button></td></tr>{% endfor %}</table></article></section></div></main>""", title="Examination Scores", students=students, subjects=subjects, scores=scores)

    def period_record(model, student_id, school):
        return model.query.filter_by(student_id=student_id, term=school.term, academic_year=school.academic_year).first()

    @app.route("/attendance", methods=["GET", "POST"])
    @login_required("teacher")
    @school_required
    def attendance():
        user = current_user()
        school = current_school()
        sid = user.school_id
        class_ids = teacher_class_ids(user)
        if request.method == "POST":
            try:
                student_query = Student.query.filter_by(
                    id=int(request.form["student_id"]), school_id=sid)
                student_query = student_query.filter(Student.class_id.in_(
                    class_ids)) if class_ids else student_query.filter(False)
                student = student_query.first()
                if not student:
                    raise ValueError(
                        "Please choose a valid student from your assigned class.")
                rec = Attendance.query.filter_by(student_id=student.id, term=request.form.get("term") or school.term, academic_year=request.form.get("academic_year") or school.academic_year).first(
                ) or Attendance(school_id=sid, student_id=student.id, term=request.form.get("term") or school.term, academic_year=request.form.get("academic_year") or school.academic_year)
                rec.present_days = safe_int(request.form.get("present_days"))
                rec.total_days = safe_int(request.form.get("total_days"))
                if rec.present_days > rec.total_days and rec.total_days:
                    raise ValueError(
                        "Present days cannot be more than total school days.")
                db.session.add(rec)
                db.session.commit()
                flash("Attendance saved.", "success")
            except Exception as exc:
                db.session.rollback()
                flash(str(exc) if isinstance(exc, ValueError)
                      else "Attendance could not be saved. Please check the entries and try again.", "error")
        students_query = db.session.query(Student, User).join(
            User, Student.user_id == User.id).filter(Student.school_id == sid)
        records_query = db.session.query(Attendance, Student, User).join(Student, Attendance.student_id == Student.id).join(
            User, Student.user_id == User.id).filter(Attendance.school_id == sid)
        students_query = students_query.filter(Student.class_id.in_(
            class_ids)) if class_ids else students_query.filter(False)
        records_query = records_query.filter(Student.class_id.in_(
            class_ids)) if class_ids else records_query.filter(False)
        students = students_query.order_by(User.full_name).all()
        records = records_query.order_by(User.full_name).all()
        return simple_period_page("Attendance", students, records, school, "attendance")

    @app.route("/fees", methods=["GET", "POST"])
    @login_required("school_admin", "accountant")
    @school_required
    def fees():
        user = current_user()
        school = current_school()
        sid = user.school_id
        if request.method == "POST":
            try:
                student = Student.query.filter_by(
                    id=int(request.form["student_id"]), school_id=sid).first()
                if not student:
                    raise ValueError("Please choose a valid student.")
                rec = Fee.query.filter_by(student_id=student.id, term=request.form.get("term") or school.term, academic_year=request.form.get("academic_year") or school.academic_year).first(
                ) or Fee(school_id=sid, student_id=student.id, term=request.form.get("term") or school.term, academic_year=request.form.get("academic_year") or school.academic_year)
                rec.amount_due = safe_number(request.form.get("amount_due"))
                rec.amount_paid = safe_number(request.form.get("amount_paid"))
                if rec.amount_due < 0 or rec.amount_paid < 0:
                    raise ValueError("Fee amounts cannot be negative.")
                db.session.add(rec)
                db.session.commit()
                flash("Fee record saved.", "success")
            except Exception as exc:
                db.session.rollback()
                flash(str(exc) if isinstance(exc, ValueError)
                      else "Fee record could not be saved. Please check the entries and try again.", "error")
        students = db.session.query(Student, User).join(User, Student.user_id == User.id).filter(
            Student.school_id == sid).order_by(User.full_name).all()
        records = db.session.query(Fee, Student, User).join(Student, Fee.student_id == Student.id).join(
            User, Student.user_id == User.id).filter(Fee.school_id == sid).order_by(User.full_name).all()
        return simple_period_page("Fees", students, records, school, "fees")

    def simple_period_page(title, students, records, school, kind):
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>{{ title }}</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ csrf() }}<label>Student<select name="student_id" required>{% for st,u in students %}<option value="{{ st.id }}">{{ u.full_name }} - {{ st.admission_no }}</option>{% endfor %}</select></label>{% if kind == 'attendance' %}{{ field('Days Present','present_days','number') }}{{ field('Total School Days','total_days','number') }}{% else %}{{ field('Amount Due','amount_due','number') }}{{ field('Amount Paid','amount_paid','number') }}{% endif %}{{ field('Term','term', value=school.term) }}{{ field('Academic Year','academic_year', value=school.academic_year) }}<button class="btn green">Save</button></form></article><article class="card"><table>{% if kind == 'attendance' %}<tr><th>Student</th><th>Admission No</th><th>Present</th><th>Term</th></tr>{% for r,st,u in records %}<tr><td>{{ u.full_name }}</td><td>{{ st.admission_no }}</td><td>{{ r.present_days }}/{{ r.total_days }}</td><td>{{ r.term }} {{ r.academic_year }}</td></tr>{% endfor %}{% else %}<tr><th>Student</th><th>Admission No</th><th>Due</th><th>Paid</th><th>Balance</th></tr>{% for r,st,u in records %}<tr><td>{{ u.full_name }}</td><td>{{ st.admission_no }}</td><td>{{ r.amount_due }}</td><td>{{ r.amount_paid }}</td><td>{{ r.amount_due - r.amount_paid }}</td></tr>{% endfor %}{% endif %}</table></article></section></div></main>""", title=title, students=students, records=records, school=school, kind=kind)

    @app.route("/announcements", methods=["GET", "POST"])
    @login_required("school_admin", "teacher", "student", "librarian")
    @school_required
    def announcements():
        user = current_user()
        sid = user.school_id
        if request.method == "POST" and user.role in {"school_admin", "librarian"}:
            db.session.add(Announcement(school_id=sid, title=request.form["title"], body=request.form["body"], audience=request.form.get(
                "audience", "all"), created_by=user.id))
            db.session.commit()
            flash("Notice published.", "success")
        rows = Announcement.query.filter(Announcement.school_id == sid, Announcement.audience.in_(
            ["all", user.role])).order_by(Announcement.created_at.desc()).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid">{% if user.role == 'school_admin' %}<article class="card"><h2>Publish Notice</h2><form method="post" class="grid cols-2">{{ csrf() }}{{ field('Title','title') }}<label>Audience<select name="audience"><option value="all">Everyone</option><option value="teacher">Teachers</option><option value="student">Students</option></select></label><label style="grid-column:1/-1">Message<textarea name="body" required></textarea></label><button class="btn green">Publish</button></form></article>{% endif %}<article class="card"><h2>School Notices</h2><table><tr><th>Title</th><th>Message</th><th>Audience</th><th>Date</th></tr>{% for r in rows %}<tr><td><b>{{ r.title }}</b></td><td>{{ r.body }}</td><td>{{ r.audience|title }}</td><td>{{ fmt_dt(r.created_at, '%Y-%m-%d') }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Notices", rows=rows)

    @app.route("/timetable", methods=["GET", "POST"])
    @login_required("school_admin", "teacher", "student")
    @school_required
    def timetable():
        user = current_user()
        sid = user.school_id
        if request.method == "POST" and user.role == "school_admin":
            db.session.add(Timetable(school_id=sid, class_id=request.form.get("class_id") or None, subject_id=request.form.get("subject_id") or None, teacher_id=request.form.get(
                "teacher_id") or None, day=request.form["day"], start_time=request.form["start_time"], end_time=request.form["end_time"], room=request.form.get("room", "")))
            db.session.commit()
            flash("Timetable period added.", "success")
        classes = ClassRoom.query.filter_by(
            school_id=sid).order_by(ClassRoom.name).all()
        subjects = Subject.query.filter_by(
            school_id=sid).order_by(Subject.name).all()
        teachers = User.query.filter_by(
            school_id=sid, role="teacher").order_by(User.full_name).all()
        rows = db.session.query(Timetable, ClassRoom, Subject, User).outerjoin(ClassRoom, Timetable.class_id == ClassRoom.id).outerjoin(
            Subject, Timetable.subject_id == Subject.id).outerjoin(User, Timetable.teacher_id == User.id).filter(Timetable.school_id == sid).order_by(Timetable.day, Timetable.start_time).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid">{% if user.role == 'school_admin' %}<article class="card"><h2>Build Timetable</h2><form method="post" class="grid cols-4">{{ csrf() }}<label>Class<select name="class_id"><option value="">General</option>{% for c in classes %}<option value="{{ c.id }}">{{ c.name }}</option>{% endfor %}</select></label><label>Subject<select name="subject_id"><option value="">None</option>{% for s in subjects %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}</select></label><label>Teacher<select name="teacher_id"><option value="">None</option>{% for t in teachers %}<option value="{{ t.id }}">{{ t.full_name }}</option>{% endfor %}</select></label><label>Day<select name="day"><option>Monday</option><option>Tuesday</option><option>Wednesday</option><option>Thursday</option><option>Friday</option><option>Saturday</option></select></label>{{ field('Start Time','start_time','time') }}{{ field('End Time','end_time','time') }}{{ field('Room','room') }}<button class="btn green">Add Period</button></form></article>{% endif %}<article class="card"><h2>Timetable</h2><table><tr><th>Day</th><th>Time</th><th>Class</th><th>Subject</th><th>Teacher</th><th>Room</th></tr>{% for r,c,s,t in rows %}<tr><td>{{ r.day }}</td><td>{{ r.start_time }} - {{ r.end_time }}</td><td>{{ c.name if c else 'General' }}</td><td>{{ s.name if s else '-' }}</td><td>{{ t.full_name if t else '-' }}</td><td>{{ r.room }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Timetable", classes=classes, subjects=subjects, teachers=teachers, rows=rows)

    @app.route("/calendar", methods=["GET", "POST"])
    @login_required("school_admin", "teacher", "student")
    @school_required
    def calendar():
        user = current_user()
        sid = user.school_id
        if request.method == "POST" and user.role == "school_admin":
            try:
                db.session.add(SchoolEvent(school_id=sid, title=request.form["title"].strip(), event_date=datetime.strptime(
                    request.form["event_date"], "%Y-%m-%d").date(), audience=request.form.get("audience", "all"), notes=request.form.get("notes", ""), created_by=user.id))
                db.session.commit()
                flash("Calendar event added.", "success")
            except ValueError:
                db.session.rollback()
                flash("Please enter a valid event date.", "error")
        rows = SchoolEvent.query.filter(SchoolEvent.school_id == sid, SchoolEvent.audience.in_(
            ["all", user.role])).order_by(SchoolEvent.event_date.desc()).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid">{% if user.role == 'school_admin' %}<article class="card"><h2>School Calendar</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ csrf() }}{{ field('Event Title','title', required=true) }}{{ field('Date','event_date','date', required=true) }}<label>Audience<select name="audience"><option value="all">Everyone</option><option value="teacher">Teachers</option><option value="student">Students</option></select></label>{{ field('Notes','notes') }}<button class="btn green">Add Event</button></form></article>{% endif %}<article class="card"><h2>Academic Calendar</h2><table><tr><th>Date</th><th>Event</th><th>Audience</th><th>Notes</th></tr>{% for r in rows %}<tr><td>{{ fmt_dt(r.event_date, '%d %B %Y') }}</td><td><b>{{ r.title }}</b></td><td>{{ r.audience|title }}</td><td>{{ r.notes }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="School Calendar", rows=rows)

    @app.route("/library", methods=["GET", "POST"])
    @login_required("school_admin", "teacher", "student", "librarian")
    @school_required
    def library():
        user = current_user()
        sid = user.school_id
        if request.method == "POST" and user.role in {"school_admin", "librarian"}:
            db.session.add(LibraryResource(school_id=sid, title=request.form["title"], category=request.form.get(
                "category", ""), location=request.form.get("location", ""), copies=int(request.form.get("copies") or 1), notes=request.form.get("notes", "")))
            db.session.commit()
            flash("Library resource added.", "success")
        rows = LibraryResource.query.filter_by(
            school_id=sid).order_by(LibraryResource.title).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid">{% if user.role == 'school_admin' %}<article class="card"><h2>Library Resources</h2><form method="post" class="grid cols-3">{{ csrf() }}{{ field('Title','title') }}{{ field('Category','category', placeholder='Textbook, Reader, Device') }}{{ field('Location','location', placeholder='Library shelf A') }}{{ field('Copies','copies','number', value='1') }}{{ field('Notes','notes') }}<button class="btn green">Add Resource</button></form></article>{% endif %}<article class="card"><h2>Library Catalogue</h2><table><tr><th>Title</th><th>Category</th><th>Location</th><th>Copies</th><th>Notes</th></tr>{% for r in rows %}<tr><td>{{ r.title }}</td><td>{{ r.category }}</td><td>{{ r.location }}</td><td>{{ r.copies }}</td><td>{{ r.notes }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="Library", rows=rows)

    @app.route("/communications", methods=["GET", "POST"])
    @login_required("system_admin", "school_admin")
    def communications():
        user = current_user()
        if request.method == "POST":
            channel = request.form.get("channel", "sms")
            audience = request.form.get("audience", "all")
            recipients = audience_recipients(
                user.school_id, audience, channel, request.form.get("recipient", ""))
            if not recipients:
                flash(
                    "No matching contacts found. Add phone numbers or email addresses before sending.", "error")
                return redirect(url_for("communications"))
            sent_count = 0
            for recipient in recipients:
                item = Communication(school_id=user.school_id, channel=channel, audience=audience, recipient=recipient, subject=request.form.get(
                    "subject", ""), message=request.form["message"], status="queued", created_by=user.id)
                item.status = deliver_communication(item)
                sent_count += item.status == "sent"
                db.session.add(item)
            log_action("bulk_communication",
                       f"{channel} to {audience}: {len(recipients)} recipients")
            db.session.commit()
            flash(
                f"Message processed for {len(recipients)} recipient(s); {sent_count} sent through the configured service.", "success")
        query = Communication.query
        if user.role != "system_admin":
            query = query.filter_by(school_id=user.school_id)
        rows = query.order_by(Communication.created_at.desc()).limit(60).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card"><h2>SMS & Email Communication</h2>{% for category, message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" class="grid cols-3">{{ csrf() }}<label>Channel<select name="channel"><option value="sms">SMS</option><option value="email">Email</option></select></label><label>Audience<select name="audience"><option value="all">Everyone</option><option value="student">Students</option><option value="parent">Parents</option><option value="teacher">Teachers</option></select></label>{{ field('Recipient','recipient', placeholder='Phone or email, optional') }}{{ field('Subject','subject') }}<label style="grid-column:1/-1">Message<textarea name="message" required></textarea></label><button class="btn green">Send / Record Message</button></form><p class="muted">Email sends when SMTP settings are configured. SMS sends when SMS_API_URL is configured.</p></article><article class="card"><h2>Recent Communication</h2><table><tr><th>Date</th><th>Channel</th><th>Audience</th><th>Subject</th><th>Status</th></tr>{% for r in rows %}<tr><td>{{ fmt_dt(r.created_at, '%Y-%m-%d %H:%M') }}</td><td>{{ r.channel|upper }}</td><td>{{ r.audience|title }}</td><td>{{ r.subject or r.message[:45] }}</td><td>{{ r.status|title }}</td></tr>{% endfor %}</table></article></section></div></main>""", title="SMS & Email", rows=rows)

    @app.route("/audit-log")
    @login_required("system_admin")
    def audit_logs():
        user = current_user()
        query = AuditLog.query
        if user.role != "system_admin":
            query = query.filter_by(school_id=user.school_id)
        rows = query.order_by(AuditLog.created_at.desc()).limit(100).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="card"><h2>Audit Log</h2><table><tr><th>Date</th><th>User</th><th>Action</th><th>Details</th><th>IP</th></tr>{% for r in rows %}<tr><td>{{ fmt_dt(r.created_at, '%Y-%m-%d %H:%M') }}</td><td>{{ r.username }}</td><td>{{ r.action }}</td><td>{{ r.details }}</td><td>{{ r.ip_address }}</td></tr>{% endfor %}</table></section></div></main>""", title="Audit Log", rows=rows)

    @app.route("/parent")
    @login_required("parent")
    @school_required
    def parent_portal():
        user = current_user()
        school = current_school()
        links = db.session.query(ParentStudent, Student, User, ClassRoom).join(Student, ParentStudent.student_id == Student.id).join(User, Student.user_id == User.id).outerjoin(
            ClassRoom, Student.class_id == ClassRoom.id).filter(ParentStudent.school_id == user.school_id, ParentStudent.parent_id == user.id, Student.school_id == user.school_id).order_by(User.full_name).all()
        children = []
        for link, student, child_user, class_group in links:
            score_rows = db.session.query(Score, Subject).join(Subject, Score.subject_id == Subject.id).filter(
                Score.school_id == user.school_id, Score.student_id == student.id, Score.term == school.term, Score.academic_year == school.academic_year).order_by(Subject.name).all()
            attendance = Attendance.query.filter_by(
                school_id=user.school_id, student_id=student.id, term=school.term, academic_year=school.academic_year).first()
            fee = Fee.query.filter_by(school_id=user.school_id, student_id=student.id,
                                      term=school.term, academic_year=school.academic_year).first()
            average = round(sum(score.class_score + score.exam_score for score,
                            _ in score_rows) / len(score_rows), 1) if score_rows else 0
            paid = bool(report_payment_for(user.id, student.id, school))
            children.append({"link": link, "student": student, "user": child_user, "class_group": class_group,
                            "scores": score_rows if paid else [], "attendance": attendance, "fee": fee,
                            "average": average if paid else 0, "report_paid": paid})
        notices = Announcement.query.filter(Announcement.school_id == user.school_id, Announcement.audience.in_(
            ["all", "parent"])).order_by(Announcement.created_at.desc()).limit(6).all()
        events = SchoolEvent.query.filter(SchoolEvent.school_id == user.school_id, SchoolEvent.audience.in_(
            ["all", "parent"]), SchoolEvent.event_date >= datetime.utcnow().date()).order_by(SchoolEvent.event_date).limit(6).all()
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card dashboard-hero"><span class="dashboard-kicker">Parent Portal</span><h2>Welcome, {{ user.full_name }}</h2><p class="muted">Secure access to your linked children's school information.</p></article>{% for child in children %}<article class="card"><div class="table-head"><div><h2>{{ child.user.full_name }}</h2><p class="muted">{{ child.student.admission_no }} · {{ child.class_group.name if child.class_group else 'No class' }} · {{ child.link.relationship }}</p></div><span class="status-pill">{{ 'Report unlocked' if child.report_paid else 'Report locked' }}</span></div><div class="grid cols-3"><div><b>Attendance</b><p>{{ child.attendance.present_days if child.attendance else 0 }} / {{ child.attendance.total_days if child.attendance else 0 }} days</p></div><div><b>Fee balance</b><p>GHS {{ '%.2f'|format((child.fee.amount_due-child.fee.amount_paid) if child.fee else 0) }}</p></div><div><b>Current period</b><p>{{ school.academic_year }} · {{ school.term }}</p></div></div>{% if child.report_paid %}<a class="btn" href="{{ url_for('parent_report',student_id=child.student.id) }}">View report card</a>{% else %}<p class="muted">Pay the report access fee for this child and current term to view or print the report.</p><a class="btn green" href="{{ url_for('parent_report_payment',student_id=child.student.id) }}">Pay to unlock report</a>{% endif %}</article>{% else %}<article class="card empty-state"><b>No children linked</b><span>Ask the school administrator to link your account to your child.</span></article>{% endfor %}<div class="grid cols-2"><article class="card"><h2>Announcements</h2>{% for item in notices %}<p><b>{{ item.title }}</b><br><span class="muted">{{ item.body }}</span></p>{% else %}<p>No announcements.</p>{% endfor %}</article><article class="card"><h2>Upcoming events</h2>{% for item in events %}<p><b>{{ item.title }}</b><br><span class="muted">{{ fmt_dt(item.event_date,'%d %B %Y') }}</span></p>{% else %}<p>No upcoming events.</p>{% endfor %}</article></div></section></div></main>""", title="Parent Portal", children=children, notices=notices, events=events)
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card dashboard-hero"><span class="dashboard-kicker">Parent Portal</span><h2>Welcome, {{ user.full_name }}</h2><p class="muted">A secure, read-only view of the children linked to your account.</p></article>{% for child in children %}<article class="card"><div class="table-head"><div><h2>{{ child.user.full_name }}</h2><p class="muted">{{ child.student.admission_no }} · {{ child.class_group.name if child.class_group else 'No class' }} · {{ child.link.relationship }}</p></div><span class="badge">Average {{ child.average }}%</span></div><div class="grid cols-3"><div><b>Attendance</b><p>{{ child.attendance.present_days if child.attendance else 0 }} / {{ child.attendance.total_days if child.attendance else 0 }} days</p></div><div><b>Fee balance</b><p>GH₵ {{ '%.2f'|format((child.fee.amount_due-child.fee.amount_paid) if child.fee else 0) }}</p></div><div><b>Current period</b><p>{{ school.academic_year }} · {{ school.term }}</p></div></div><table><tr><th>Subject</th><th>Class</th><th>Exam</th><th>Total</th><th>Grade</th></tr>{% for score, subject in child.scores %}<tr><td>{{ subject.name }}</td><td>{{ score.class_score }}</td><td>{{ score.exam_score }}</td><td>{{ score.class_score + score.exam_score }}</td><td>{{ grade(score.class_score + score.exam_score) }}</td></tr>{% else %}<tr><td colspan="5"><div class="empty-state"><b>No published results</b><span>Results for this period will appear here.</span></div></td></tr>{% endfor %}</table></article>{% else %}<article class="card empty-state"><b>No children linked</b><span>Ask the school administrator to link your parent account to your child.</span></article>{% endfor %}<div class="grid cols-2"><article class="card"><h2>Announcements</h2>{% for item in notices %}<p><b>{{ item.title }}</b><br><span class="muted">{{ item.body }}</span></p>{% else %}<div class="empty-state"><b>No announcements</b></div>{% endfor %}</article><article class="card"><h2>Upcoming events</h2>{% for item in events %}<p><b>{{ item.title }}</b><br><span class="muted">{{ fmt_dt(item.event_date, '%d %B %Y') }}</span></p>{% else %}<div class="empty-state"><b>No upcoming events</b></div>{% endfor %}</article></div></section></div></main>""", title="Parent Portal", children=children, notices=notices, events=events)

    def linked_parent_student(parent: User, student_id: int):
        return db.session.query(Student, User).join(
            User, Student.user_id == User.id
        ).join(
            ParentStudent, ParentStudent.student_id == Student.id
        ).filter(
            ParentStudent.parent_id == parent.id,
            ParentStudent.school_id == parent.school_id,
            Student.id == student_id,
            Student.school_id == parent.school_id,
        ).first()

    @app.route("/parent/report/<int:student_id>")
    @login_required("parent")
    @school_required
    def parent_report(student_id):
        parent = current_user()
        school = current_school()
        linked = linked_parent_student(parent, student_id)
        if not linked:
            abort(404)
        if not report_payment_for(parent.id, student_id, school):
            return redirect(url_for("parent_report_payment", student_id=student_id))
        student, child_user = linked
        log_action("view_paid_parent_report",
                   f"Viewed {student.admission_no} for {school.term} {school.academic_year}")
        db.session.commit()
        return render(REPORT_CARD_PAGE, title="Academic Report Card",
                      **build_report_context(student, school, child_user))

    @app.route("/parent/report/<int:student_id>/payment")
    @login_required("parent")
    @school_required
    def parent_report_payment(student_id):
        parent = current_user()
        school = current_school()
        linked = linked_parent_student(parent, student_id)
        if not linked:
            abort(404)
        if report_payment_for(parent.id, student_id, school):
            return redirect(url_for("parent_report", student_id=student_id))
        student, child_user = linked
        fee = Decimal(report_fee_subunit()) / 100
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="grid"><article class="card" style="max-width:620px"><span class="dashboard-kicker dark">Secure report access</span><h2>Unlock {{ child.full_name }}'s report card</h2><p class="muted">{{ school.academic_year }} · {{ school.term }}</p><div class="kpi-card"><div class="kpi-icon">◈</div><div><span>Amount due</span><strong>{{ currency }} {{ '%.2f'|format(fee) }}</strong><small>One payment unlocks this child's current-term report.</small></div></div>{% for category,message in get_flashed_messages(with_categories=true) %}<div class="flash {{ category }}">{{ message }}</div>{% endfor %}<form method="post" action="{{ url_for('initialize_parent_report_payment',student_id=student.id) }}">{{ csrf() }}<button class="btn green" type="submit">Pay Now with Paystack</button></form><p class="muted">Payment is verified securely on the server. Your card or MoMo details are handled by Paystack and are never stored here.</p></article></section></div></main>""", title="Report Payment", student=student, child=child_user, fee=float(fee), currency=Config.PAYSTACK_CURRENCY)

    @app.route("/parent/report/<int:student_id>/paystack", methods=["POST"])
    @login_required("parent")
    @school_required
    def initialize_parent_report_payment(student_id):
        parent = current_user()
        school = current_school()
        linked = linked_parent_student(parent, student_id)
        if not linked:
            abort(404)
        if report_payment_for(parent.id, student_id, school):
            return redirect(url_for("parent_report", student_id=student_id))
        if not parent.email:
            flash("A parent email address is required for Paystack. Ask the school administrator to add it.", "error")
            return redirect(url_for("parent_report_payment", student_id=student_id))
        amount = report_fee_subunit()
        reference = f"RPT-{school.id}-{parent.id}-{student_id}-{uuid4().hex}"
        payment = ParentReportPayment(
            school_id=school.id, parent_id=parent.id, student_id=student_id,
            academic_year=school.academic_year, term=school.term,
            reference=reference, amount_subunit=amount,
            currency=Config.PAYSTACK_CURRENCY, status="pending")
        db.session.add(payment)
        db.session.commit()
        try:
            result = paystack_request("/transaction/initialize", "POST", {
                "email": parent.email,
                "amount": str(amount),
                "currency": Config.PAYSTACK_CURRENCY,
                "reference": reference,
                "callback_url": url_for("paystack_callback", _external=True),
                "channels": ["mobile_money", "card"],
                "metadata": {
                    "payment_id": payment.id, "school_id": school.id,
                    "parent_id": parent.id, "student_id": student_id,
                    "academic_year": school.academic_year, "term": school.term,
                },
            })
            authorization_url = (result.get("data") or {}).get("authorization_url", "")
            parsed = urlparse(authorization_url)
            if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith("paystack.com"):
                raise RuntimeError("Paystack returned an invalid checkout address")
            payment.authorization_url = authorization_url
            payment.updated_at = datetime.utcnow()
            db.session.commit()
            return redirect(authorization_url)
        except RuntimeError:
            payment.status = "failed"
            payment.updated_at = datetime.utcnow()
            db.session.commit()
            flash("Payment could not be started. Please try again shortly.", "error")
            return redirect(url_for("parent_report_payment", student_id=student_id))

    @app.route("/payments/paystack/callback")
    def paystack_callback():
        reference = request.args.get("reference", "").strip()
        payment = ParentReportPayment.query.filter_by(reference=reference).first()
        if not payment:
            abort(404)
        try:
            verified = verify_report_payment(payment)
        except RuntimeError:
            verified = False
        user = current_user()
        if verified and user and user.role == "parent" and user.id == payment.parent_id:
            flash("Payment verified. The report card is now unlocked.", "success")
            return redirect(url_for("parent_report", student_id=payment.student_id))
        if verified:
            flash("Payment verified. Sign in to view the report card.", "success")
            return redirect(url_for("login", portal="parent"))
        flash("Payment was not completed or could not be verified.", "error")
        return redirect(url_for("parent_report_payment", student_id=payment.student_id)) if user else redirect(url_for("login", portal="parent"))

    @app.route("/payments/paystack/webhook", methods=["POST"])
    def paystack_webhook():
        secret = Config.PAYSTACK_SECRET_KEY
        if not secret:
            return Response(status=503)
        raw_body = request.get_data(cache=False)
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
        supplied = request.headers.get("x-paystack-signature", "")
        if not hmac.compare_digest(expected, supplied):
            return Response(status=401)
        try:
            event_data = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return Response(status=400)
        if event_data.get("event") == "charge.success":
            reference = str((event_data.get("data") or {}).get("reference") or "")
            payment = ParentReportPayment.query.filter_by(reference=reference).first()
            if payment and payment.status != "success":
                try:
                    verify_report_payment(payment)
                except RuntimeError:
                    return Response(status=503)
        return Response(status=200)

    @app.route("/my-results.pdf")
    @login_required("student")
    @school_required
    def student_results_pdf():
        user = current_user()
        school = current_school()
        student = Student.query.filter_by(user_id=user.id).first()
        student_class = db.session.get(
            ClassRoom, student.class_id) if student and student.class_id else None
        rows = db.session.query(Score, Subject).join(Subject, Score.subject_id == Subject.id).filter(
            Score.student_id == student.id).order_by(Subject.name).all() if student else []
        buffer = BytesIO()
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        pdf = canvas.Canvas(buffer, pagesize=A4)
        y = A4[1] - 55
        if school.crest and (UPLOAD_DIR / school.crest).exists():
            pdf.drawImage(str(UPLOAD_DIR / school.crest), 45, y - 30,
                          width=55, height=55, preserveAspectRatio=True, mask="auto")
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(115 if school.crest else 45, y, school.name)
        y -= 24
        pdf.setFont("Helvetica", 11)
        pdf.drawString(45, y, f"Terminal Report Card - {user.full_name}")
        y -= 20
        pdf.drawString(
            45, y, f"Admission No: {student.admission_no if student else '-'}   Class: {student_class.name if student_class else '-'}")
        y -= 18
        pdf.drawString(
            45, y, f"Term: {school.term}   Academic Year: {school.academic_year}")
        y -= 34
        pdf.setFont("Helvetica-Bold", 10)
        for x, label in [(45, "Subject"), (215, "Class"), (280, "Exam"), (345, "Total"), (410, "Grade")]:
            pdf.drawString(x, y, label)
        y -= 18
        pdf.setFont("Helvetica", 10)
        for sc, sub in rows:
            total = sc.class_score + sc.exam_score
            if y < 70:
                pdf.showPage()
                y = A4[1] - 55
            pdf.drawString(45, y, sub.name[:28])
            pdf.drawString(215, y, str(sc.class_score))
            pdf.drawString(280, y, str(sc.exam_score))
            pdf.drawString(345, y, str(total))
            pdf.drawString(410, y, grade(total))
            y -= 17
        y -= 18
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(
            45, y, f"Promotion: {student.promotion_note or 'Not promoted'}")
        y -= 45
        if school.head_signature and (UPLOAD_DIR / school.head_signature).exists():
            pdf.drawImage(str(UPLOAD_DIR / school.head_signature), 45, y,
                          width=120, height=40, preserveAspectRatio=True, mask="auto")
        pdf.drawString(
            45, y - 12, f"{school.head_title or 'Head Teacher'}: {school.head_name or ''}")
        pdf.save()
        log_action("download_result_pdf", "Student downloaded result PDF")
        db.session.commit()
        buffer.seek(0)
        return Response(buffer.read(), mimetype="application/pdf", headers={"Content-Disposition": f"attachment; filename={student.admission_no if student else 'student'}_result.pdf"})

    @app.route("/my-results")
    @login_required("student")
    @school_required
    def student_results():
        user = current_user()
        school = current_school()
        student = Student.query.filter_by(user_id=user.id).first()
        if not student:
            abort(404)
        return render(REPORT_CARD_PAGE, title="Academic Report Card",
                      **build_report_context(student, school, user))
        rows = db.session.query(Score, Subject).join(Subject, Score.subject_id == Subject.id).filter(
            Score.student_id == student.id).order_by(Subject.name).all() if student else []
        attendance = period_record(
            Attendance, student.id, school) if student else None
        fees = period_record(Fee, student.id, school) if student else None
        total = sum((sc.class_score + sc.exam_score) for sc, _ in rows)
        average = round(total / len(rows), 2) if rows else 0
        conduct = next((sc.conduct for sc, _ in rows if sc.conduct), "")
        position = next((sc.position for sc, _ in rows if sc.position), "")
        overall = grade_info(average)
        detail = StudentReportDetail.query.filter_by(student_id=student.id, term=school.term, academic_year=school.academic_year).first() if student else None
        term_summary = []
        for term_name in ["Term 1", "Term 2", "Term 3"]:
            term_scores = Score.query.filter_by(student_id=student.id, term=term_name, academic_year=school.academic_year).all() if student else []
            term_total = round(sum(score.class_score + score.exam_score for score in term_scores), 2)
            term_summary.append({"total": term_total, "average": round(term_total / len(term_scores), 2) if term_scores else 0})
        yearly_total = round(sum(item["total"] for item in term_summary), 2)
        non_empty_terms = [item for item in term_summary if item["total"]]
        yearly_average = round(sum(item["average"] for item in non_empty_terms) / len(non_empty_terms), 2) if non_empty_terms else 0
        fee_breakdown_total = round(sum([detail.arrears, detail.tuition_fees, detail.pta_dues, detail.medical_dues, detail.building_fund]), 2) if detail else 0
        return render(REPORT_CARD_PAGE, title="Academic Report Card", student=student, student_class=db.session.get(ClassRoom, student.class_id) if student and student.class_id else None, rows=rows, attendance=attendance, fees=fees, average=average, report_total=total, conduct=conduct, position=position, overall=overall, detail=detail, term_summary=term_summary, yearly_total=yearly_total, yearly_average=yearly_average, fee_breakdown_total=fee_breakdown_total)
        return render("""<main class="wrap"><div class="layout">""" + SIDEBAR + """<section class="card report-card terminal"><div class="actions no-print" style="justify-content:flex-end;margin-bottom:12px"><button class="btn" onclick="window.print()">Print Result</button><a class="btn ghost" href="{{ url_for('student_results_pdf') }}">Download PDF</a></div><div class="report-top">{% if school.crest %}<img src="{{ url_for('uploads', filename=school.crest) }}" alt="School crest">{% else %}<span></span>{% endif %}<div><h2>{{ school.name }}</h2><p>{{ school.address }}</p><p>{{ school.phone }} {{ school.email }}</p><p>{{ school.motto }}</p><div class="terminal-title">Terminal Report</div></div><span></span></div><div class="terminal-student">&lt;&lt;{{ user.full_name|upper }}&gt;&gt;</div><div class="terminal-meta"><span><b>CLASS:</b> {{ student_class.name if student_class else '-' }}</span><span><b>ACADEMIC YEAR:</b> {{ school.academic_year or '-' }}</span><span><b>POSITION IN CLASS:</b> {{ position or '-' }}</span><span><b>ACADEMIC TERM:</b> {{ school.term or '-' }}</span><span><b>NEXT TERM RE-OPENS:</b> -</span><span><b>NUMBER ON ROLL:</b> -</span></div><table class="subjects"><tr><th>Subjects</th><th>Class Score<br>(50%)</th><th>Exam Score<br>(50%)</th><th>Total Score<br>(100%)</th><th>Grade</th><th>Grade Meaning</th><th>Teacher</th></tr>{% for sc,sub in rows %}{% set subject_total=sc.class_score+sc.exam_score %}{% set info=grade_info(subject_total) %}<tr><td>{{ sub.name }}</td><td>{{ sc.class_score }}</td><td>{{ sc.exam_score }}</td><td>{{ subject_total }}</td><td>{{ info.grade }}</td><td>{{ info.interpretation }}</td><td>{{ sc.remarks }}</td></tr>{% endfor %}{% if not rows %}<tr><td colspan="7">No results have been entered yet.</td></tr>{% endif %}</table><table class="remarks" style="margin-top:18px"><tr><td><b>INTEREST</b></td><td></td></tr><tr><td><b>CONDUCT</b></td><td>{{ conduct or 'Good' }}</td></tr><tr><td><b>PROMOTION STATUS</b></td><td>{{ student.promotion_note or 'Not promoted' }}</td></tr><tr><td><b>ATTITUDE</b></td><td></td></tr><tr><td><b>CLASS TEACHER'S REMARK</b></td><td>{{ overall.interpretation }}</td></tr><tr><td><b>ACADEMIC REMARK</b></td><td>Average: {{ average }}% | Attendance: {{ attendance.present_days if attendance else 0 }}/{{ attendance.total_days if attendance else 0 }} | Fee Balance: {{ ((fees.amount_due - fees.amount_paid) if fees else 0) }}</td></tr></table><div style="margin-top:24px"><b>HEADTEACHER'S SIGNATURE</b>{% if school.head_signature %}<br><img class="signature-img" src="{{ url_for('uploads', filename=school.head_signature) }}" alt="signature">{% else %}<div class="signature-line"></div>{% endif %}</div><table class="grading-key" style="margin-top:28px"><tr><th>80 - 100</th><th>70 - 79</th><th>65 - 69</th><th>60 - 64</th><th>55 - 59</th><th>50 - 54</th><th>45 - 49</th><th>40 - 44</th><th>0 - 39</th></tr><tr><td>A1</td><td>B2</td><td>B3</td><td>C4</td><td>C5</td><td>C6</td><td>D7</td><td>E8</td><td>F9</td></tr><tr><td>Excellent</td><td>Very Good</td><td>Good</td><td>Credit</td><td>Credit</td><td>Credit</td><td>Pass</td><td>Pass</td><td>Fail</td></tr></table><p class="powered">Powered by Smart Schools SMS</p></section></div></main>""", title="My Results", student=student, student_class=db.session.get(ClassRoom, student.class_id) if student and student.class_id else None, rows=rows, attendance=attendance, fees=fees, average=average, report_total=total, conduct=conduct, position=position, overall=overall)


app = create_app()

with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(
        os.getenv("PORT", "5000")), debug=Config.DEBUG)
