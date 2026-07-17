from datetime import datetime

from flask_login import UserMixin

from app.extensions import db, login_manager


def now_text():
    return datetime.utcnow().isoformat(timespec="seconds")


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, nullable=True)
    role = db.Column(db.String(40), nullable=False)
    full_name = db.Column(db.String(160), nullable=False, default="")
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(120), default="")
    phone = db.Column(db.String(40), default="")
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.String(40), default=now_text)
    must_change_password = db.Column(db.Boolean, default=True, nullable=False)

    @property
    def is_active(self):
        return bool(self.active)

    @property
    def display_name(self):
        return self.full_name or self.username


class School(db.Model):
    __tablename__ = "schools"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(180), nullable=False)
    motto = db.Column(db.String(255), default="")
    crest = db.Column(db.String(255), default="")
    address = db.Column(db.String(255), default="")
    phone = db.Column(db.String(40), default="")
    email = db.Column(db.String(120), default="")
    academic_year = db.Column(db.String(40), default="")
    term = db.Column(db.String(40), default="")
    onboarded = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.String(40), default=now_text)
    head_name = db.Column(db.String(160), default="")
    head_title = db.Column(db.String(100), default="Head of School")
    head_signature = db.Column(db.String(260), default="")


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"))
    admission_no = db.Column(db.String(80), nullable=False)
    guardian_name = db.Column(db.String(160), default="")
    guardian_phone = db.Column(db.String(40), default="")

    user = db.relationship("User", backref="student_profile", foreign_keys=[user_id])
    school = db.relationship("School", foreign_keys=[school_id])
    class_group = db.relationship("ClassGroup", foreign_keys=[class_id])


class ClassGroup(db.Model):
    __tablename__ = "classes"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    teacher_id = db.Column(db.Integer)


class Subject(db.Model):
    __tablename__ = "subjects"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(30), default="")
    teacher_id = db.Column(db.Integer)


class Score(db.Model):
    __tablename__ = "scores"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    class_score = db.Column(db.Float, default=0)
    exam_score = db.Column(db.Float, default=0)
    remarks = db.Column(db.String(255), default="")
    term = db.Column(db.String(40), default="")
    academic_year = db.Column(db.String(40), default="")
    teacher_id = db.Column(db.Integer)
    updated_at = db.Column(db.String(40), default=now_text)
    conduct = db.Column(db.String(120), default="")
    position = db.Column(db.String(40), default="")

    student = db.relationship("Student", backref="scores")
    subject = db.relationship("Subject")

    @property
    def total(self):
        return float(self.class_score or 0) + float(self.exam_score or 0)

    @property
    def grade(self):
        total = self.total
        if total >= 80:
            return "A"
        if total >= 70:
            return "B"
        if total >= 60:
            return "C"
        if total >= 50:
            return "D"
        return "F"


class Communication(db.Model):
    __tablename__ = "communications"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer)
    channel = db.Column(db.String(20), nullable=False)
    audience = db.Column(db.String(40), nullable=False)
    recipient = db.Column(db.String(160), default="")
    subject = db.Column(db.String(180), default="")
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(40), default="queued")
    created_by = db.Column(db.Integer)
    created_at = db.Column(db.String(40), default=now_text)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    username = db.Column(db.String(100), default="")
    action = db.Column(db.String(120), nullable=False)
    details = db.Column(db.Text, default="")
    ip_address = db.Column(db.String(80), default="")
    created_at = db.Column(db.String(40), default=now_text)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
