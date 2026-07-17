from datetime import datetime
from io import BytesIO

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import bcrypt, db
from app.models import AuditLog, Communication, Score, School, Student, User

auth = Blueprint("auth", __name__)


def log_action(action, details=""):
    log = AuditLog(
        user_id=current_user.id if current_user.is_authenticated else None,
        username=current_user.username if current_user.is_authenticated else "",
        action=action,
        details=details,
        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
    )
    db.session.add(log)


def password_matches(user, password):
    stored = user.password_hash or ""
    if stored.startswith("$2") or stored.startswith("bcrypt:"):
        try:
            return bcrypt.check_password_hash(stored, password)
        except ValueError:
            return False
    return check_password_hash(stored, password)


def role_home(user):
    if user.role == "student":
        return "auth.student_dashboard"
    return "auth.dashboard"


def current_school():
    if current_user.is_authenticated and current_user.school_id:
        school = School.query.get(current_user.school_id)
        if school:
            return school
    return School.query.first()


def student_report_context(student_id=None):
    student = None
    if current_user.role == "student":
        student = Student.query.filter_by(user_id=current_user.id).first()
    elif student_id:
        student = Student.query.get_or_404(student_id)

    scores = []
    if student:
        scores = Score.query.filter_by(student_id=student.id).all()

    total = sum(score.total for score in scores)
    average = round(total / len(scores), 2) if scores else 0
    chart_labels = [score.subject.name if score.subject else "Subject" for score in scores]
    chart_values = [score.total for score in scores]

    return {
        "school": current_school(),
        "student": student,
        "scores": scores,
        "average": average,
        "chart_labels": chart_labels,
        "chart_values": chart_values,
        "generated_at": datetime.now().strftime("%d %b %Y %I:%M %p"),
    }


@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()

        if user and user.active and password_matches(user, password):
            login_user(user)
            log_action("login", f"{user.role} signed in")
            db.session.commit()
            return redirect(url_for(role_home(user)))

        log_action("failed_login", f"Failed login for username: {username}")
        db.session.commit()
        flash("Invalid username or password.", "danger")

    return render_template("auth/login.html")


@auth.route("/logout")
@login_required
def logout():
    log_action("logout", "User signed out")
    db.session.commit()
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))


@auth.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip()
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        user = User.query.filter_by(username=username).first()

        if not user or (email and user.email and user.email.lower() != email.lower()):
            flash("We could not find a matching account.", "danger")
        elif len(new_password) < 6:
            flash("Use at least 6 characters for the new password.", "danger")
        elif new_password != confirm_password:
            flash("The new passwords do not match.", "danger")
        else:
            user.password_hash = generate_password_hash(new_password)
            user.must_change_password = False
            log_action("password_reset", f"Password reset for {username}")
            db.session.commit()
            flash("Password reset successfully. You can now log in.", "success")
            return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html")


@auth.route("/dashboard")
@login_required
def dashboard():
    if current_user.role == "student":
        return redirect(url_for("auth.student_dashboard"))

    school_id = current_user.school_id
    students = Student.query.filter_by(school_id=school_id).all() if school_id else Student.query.all()
    scores = Score.query.filter_by(school_id=school_id).all() if school_id else Score.query.all()
    users = User.query.filter_by(school_id=school_id).all() if school_id else User.query.all()
    communications = Communication.query.order_by(Communication.id.desc()).limit(8).all()
    audits = AuditLog.query.order_by(AuditLog.id.desc()).limit(12).all()

    averages = {}
    for score in scores:
        subject_name = score.subject.name if score.subject else "Subject"
        averages.setdefault(subject_name, []).append(score.total)
    chart_labels = list(averages.keys())
    chart_values = [round(sum(values) / len(values), 2) for values in averages.values()]

    return render_template(
        "dashboard.html",
        school=current_school(),
        students=students,
        users=users,
        scores=scores,
        communications=communications,
        audits=audits,
        chart_labels=chart_labels,
        chart_values=chart_values,
    )


@auth.route("/student")
@login_required
def student_dashboard():
    context = student_report_context()
    if not context["student"]:
        flash("No student profile is linked to this account yet.", "danger")
    return render_template("student/dashboard.html", **context)


@auth.route("/student/report")
@login_required
def student_report():
    context = student_report_context(request.args.get("student_id", type=int))
    log_action("view_report", "Student report viewed")
    db.session.commit()
    return render_template("student/report.html", **context)


@auth.route("/student/report.pdf")
@login_required
def student_report_pdf():
    context = student_report_context(request.args.get("student_id", type=int))
    student = context["student"]
    if not student:
        flash("No student profile is available for PDF export.", "danger")
        return redirect(url_for("auth.student_dashboard"))

    buffer = BytesIO()
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        pdf = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        y = height - 60
        school_name = context["school"].name if context["school"] else "Smart Schools SMS"
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(50, y, school_name)
        y -= 28
        pdf.setFont("Helvetica", 11)
        pdf.drawString(50, y, f"Result report for {student.user.display_name}")
        y -= 22
        pdf.drawString(50, y, f"Admission No: {student.admission_no}    Average: {context['average']}")
        y -= 32
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(50, y, "Subject")
        pdf.drawString(220, y, "Class")
        pdf.drawString(290, y, "Exam")
        pdf.drawString(360, y, "Total")
        pdf.drawString(430, y, "Grade")
        y -= 18
        pdf.setFont("Helvetica", 10)
        for score in context["scores"]:
            if y < 80:
                pdf.showPage()
                y = height - 60
            pdf.drawString(50, y, score.subject.name if score.subject else "Subject")
            pdf.drawString(220, y, str(score.class_score or 0))
            pdf.drawString(290, y, str(score.exam_score or 0))
            pdf.drawString(360, y, str(score.total))
            pdf.drawString(430, y, score.grade)
            y -= 18
        pdf.save()
    except Exception:
        return Response("PDF generation is not available on this installation.", status=503)

    log_action("download_report_pdf", f"PDF downloaded for student {student.id}")
    db.session.commit()
    buffer.seek(0)
    filename = f"{student.admission_no}_result_report.pdf"
    return Response(
        buffer.read(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@auth.route("/students/<int:student_id>/login-slip")
@login_required
def login_slip(student_id):
    if current_user.role not in {"system_admin", "school_admin", "teacher"}:
        flash("You do not have permission to print login slips.", "danger")
        return redirect(url_for("auth.student_dashboard"))

    student = Student.query.get_or_404(student_id)
    log_action("print_login_slip", f"Login slip opened for student {student.id}")
    db.session.commit()
    return render_template("student/login_slip.html", student=student, school=current_school())


@auth.route("/communications", methods=["POST"])
@login_required
def create_communication():
    if current_user.role == "student":
        flash("Only staff can send communication.", "danger")
        return redirect(url_for("auth.student_dashboard"))

    message = (request.form.get("message") or "").strip()
    if not message:
        flash("Please enter a message before sending.", "danger")
        return redirect(url_for("auth.dashboard"))

    item = Communication(
        school_id=current_user.school_id,
        channel=request.form.get("channel") or "email",
        audience=request.form.get("audience") or "all",
        recipient=request.form.get("recipient") or "",
        subject=request.form.get("subject") or "",
        message=message,
        status="recorded",
        created_by=current_user.id,
    )
    db.session.add(item)
    log_action("communication_recorded", f"{item.channel} to {item.audience}: {item.subject}")
    db.session.commit()
    flash("Communication recorded successfully.", "success")
    return redirect(url_for("auth.dashboard"))
