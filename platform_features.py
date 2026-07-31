from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Response, abort, flash, redirect, render_template_string, request, session, url_for
from sqlalchemy import Index, UniqueConstraint, func

from foundation import clean_email, clean_slug, clean_text

_models = {}


def init_platform_models(db):
    if _models:
        return _models

    class Programme(db.Model):
        __tablename__ = "programmes"
        id = db.Column(db.Integer, primary_key=True)
        school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
        name = db.Column(db.String(140), nullable=False)
        code = db.Column(db.String(40), default="")
        department = db.Column(db.String(140), default="")
        active = db.Column(db.Boolean, default=True, nullable=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        __table_args__ = (UniqueConstraint("school_id", "name", name="uq_programme_school_name"),)

    class AcademicYear(db.Model):
        __tablename__ = "academic_years"
        id = db.Column(db.Integer, primary_key=True)
        school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
        name = db.Column(db.String(40), nullable=False)
        starts_on = db.Column(db.Date)
        ends_on = db.Column(db.Date)
        active = db.Column(db.Boolean, default=False, nullable=False, index=True)
        __table_args__ = (UniqueConstraint("school_id", "name", name="uq_academic_year_school"),)

    class AcademicTerm(db.Model):
        __tablename__ = "academic_terms"
        id = db.Column(db.Integer, primary_key=True)
        school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
        academic_year_id = db.Column(
            db.Integer, db.ForeignKey("academic_years.id", ondelete="CASCADE"), nullable=False, index=True
        )
        name = db.Column(db.String(40), nullable=False)
        starts_on = db.Column(db.Date)
        ends_on = db.Column(db.Date)
        active = db.Column(db.Boolean, default=False, nullable=False, index=True)
        __table_args__ = (
            UniqueConstraint("school_id", "academic_year_id", "name", name="uq_academic_term_school_year"),
        )

    class Assignment(db.Model):
        __tablename__ = "assignments"
        id = db.Column(db.Integer, primary_key=True)
        school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
        class_id = db.Column(db.Integer, db.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True)
        subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True)
        teacher_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
        title = db.Column(db.String(180), nullable=False)
        instructions = db.Column(db.Text, default="")
        due_at = db.Column(db.DateTime)
        published_at = db.Column(db.DateTime)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    class Examination(db.Model):
        __tablename__ = "examinations"
        id = db.Column(db.Integer, primary_key=True)
        school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
        academic_year = db.Column(db.String(40), nullable=False, index=True)
        term = db.Column(db.String(40), nullable=False, index=True)
        name = db.Column(db.String(160), nullable=False)
        status = db.Column(db.String(30), default="draft", nullable=False, index=True)
        starts_on = db.Column(db.Date)
        ends_on = db.Column(db.Date)
        created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
        __table_args__ = (
            UniqueConstraint("school_id", "academic_year", "term", "name", name="uq_examination_school_period"),
        )

    class FeeStructure(db.Model):
        __tablename__ = "fee_structures"
        id = db.Column(db.Integer, primary_key=True)
        school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
        class_id = db.Column(db.Integer, db.ForeignKey("classes.id", ondelete="SET NULL"), index=True)
        programme_id = db.Column(db.Integer, db.ForeignKey("programmes.id", ondelete="SET NULL"), index=True)
        academic_year = db.Column(db.String(40), nullable=False, index=True)
        term = db.Column(db.String(40), nullable=False, index=True)
        name = db.Column(db.String(160), nullable=False)
        amount_subunit = db.Column(db.Integer, nullable=False)
        active = db.Column(db.Boolean, default=True, nullable=False)

    class Invoice(db.Model):
        __tablename__ = "invoices"
        id = db.Column(db.Integer, primary_key=True)
        school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
        student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
        invoice_number = db.Column(db.String(100), nullable=False)
        academic_year = db.Column(db.String(40), nullable=False, index=True)
        term = db.Column(db.String(40), nullable=False, index=True)
        amount_subunit = db.Column(db.Integer, nullable=False)
        balance_subunit = db.Column(db.Integer, nullable=False)
        status = db.Column(db.String(30), default="unpaid", nullable=False, index=True)
        due_on = db.Column(db.Date)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        __table_args__ = (UniqueConstraint("school_id", "invoice_number", name="uq_invoice_school_number"),)

    class FinancePayment(db.Model):
        __tablename__ = "finance_payments"
        id = db.Column(db.Integer, primary_key=True)
        school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
        invoice_id = db.Column(
            db.Integer, db.ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False, index=True
        )
        student_id = db.Column(
            db.Integer, db.ForeignKey("students.id", ondelete="RESTRICT"), nullable=False, index=True
        )
        reference = db.Column(db.String(120), nullable=False)
        amount_subunit = db.Column(db.Integer, nullable=False)
        currency = db.Column(db.String(10), default="GHS", nullable=False)
        method = db.Column(db.String(30), default="manual", nullable=False)
        status = db.Column(db.String(30), default="success", nullable=False, index=True)
        reason = db.Column(db.String(260), default="")
        reversal_of_id = db.Column(db.Integer, db.ForeignKey("finance_payments.id", ondelete="RESTRICT"), index=True)
        recorded_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
        paid_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        __table_args__ = (UniqueConstraint("school_id", "reference", name="uq_finance_payment_school_reference"),)

    class DemoRequest(db.Model):
        __tablename__ = "demo_requests"
        id = db.Column(db.Integer, primary_key=True)
        school_name = db.Column(db.String(180), nullable=False)
        contact_name = db.Column(db.String(160), nullable=False)
        email = db.Column(db.String(160), nullable=False, index=True)
        phone = db.Column(db.String(80), default="")
        status = db.Column(db.String(30), default="new", nullable=False, index=True)
        notes = db.Column(db.Text, default="")
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    class Subscription(db.Model):
        __tablename__ = "subscriptions"
        id = db.Column(db.Integer, primary_key=True)
        school_id = db.Column(
            db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
        )
        plan = db.Column(db.String(40), default="trial", nullable=False)
        status = db.Column(db.String(30), default="trial", nullable=False, index=True)
        starts_at = db.Column(db.DateTime, default=datetime.utcnow)
        ends_at = db.Column(db.DateTime)
        student_limit = db.Column(db.Integer, default=0)
        notes = db.Column(db.String(260), default="")

    class SecurityEvent(db.Model):
        __tablename__ = "security_events"
        id = db.Column(db.Integer, primary_key=True)
        school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), index=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
        event_type = db.Column(db.String(80), nullable=False, index=True)
        severity = db.Column(db.String(20), default="info", nullable=False, index=True)
        ip_address = db.Column(db.String(80), default="")
        details = db.Column(db.String(500), default="")
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    class ResultChange(db.Model):
        __tablename__ = "result_changes"
        id = db.Column(db.Integer, primary_key=True)
        school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
        score_id = db.Column(db.Integer, db.ForeignKey("scores.id", ondelete="CASCADE"), nullable=False, index=True)
        changed_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
        old_class_score = db.Column(db.Float)
        old_exam_score = db.Column(db.Float)
        new_class_score = db.Column(db.Float)
        new_exam_score = db.Column(db.Float)
        reason = db.Column(db.String(260), default="")
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    class StudentHistory(db.Model):
        __tablename__ = "student_histories"
        id = db.Column(db.Integer, primary_key=True)
        school_id = db.Column(db.Integer, db.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
        student_id = db.Column(db.Integer, db.ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
        event_type = db.Column(db.String(40), nullable=False, index=True)
        from_class_id = db.Column(db.Integer, index=True)
        to_class_id = db.Column(db.Integer, index=True)
        notes = db.Column(db.String(260), default="")
        recorded_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    Index("ix_assignment_school_due", Assignment.school_id, Assignment.due_at)
    Index("ix_invoice_school_status", Invoice.school_id, Invoice.status)
    Index("ix_payment_school_paid", FinancePayment.school_id, FinancePayment.paid_at)

    _models.update(
        Programme=Programme,
        AcademicYear=AcademicYear,
        AcademicTerm=AcademicTerm,
        Assignment=Assignment,
        Examination=Examination,
        FeeStructure=FeeStructure,
        Invoice=Invoice,
        FinancePayment=FinancePayment,
        DemoRequest=DemoRequest,
        Subscription=Subscription,
        SecurityEvent=SecurityEvent,
        ResultChange=ResultChange,
        StudentHistory=StudentHistory,
    )
    return _models


def register_platform_features(app, ctx):
    db = ctx["db"]
    School, User, Student = ctx["School"], ctx["User"], ctx["Student"]
    ClassRoom, Subject, Score = ctx["ClassRoom"], ctx["Subject"], ctx["Score"]
    current_user, current_school = ctx["current_user"], ctx["current_school"]
    login_required, school_required = ctx["login_required"], ctx["school_required"]
    log_action = ctx["log_action"]
    models = init_platform_models(db)
    Assignment = models["Assignment"]
    FeeStructure = models["FeeStructure"]
    Invoice = models["Invoice"]
    FinancePayment = models["FinancePayment"]
    DemoRequest = models["DemoRequest"]
    Subscription = models["Subscription"]
    SecurityEvent = models["SecurityEvent"]

    def render_page(body, **values):
        return render_template_string(
            ctx["BASE_HTML"].replace("{% block body %}{% endblock %}", body),
            **values,
        )

    @app.context_processor
    def school_theme():
        school = ctx["portal_school"]()
        if not school:
            return {"school_theme_style": ""}
        style = (
            f"--school-primary:{school.primary_color or '#0b2b5c'};"
            f"--school-secondary:{school.secondary_color or '#125edb'};"
            f"--school-accent:{school.accent_color or '#079455'};"
            f"--app-primary:{school.secondary_color or '#125edb'};"
            f"--app-primary-strong:{school.primary_color or '#0b2b5c'};"
            f"--app-success:{school.accent_color or '#079455'};"
        )
        return {"school_theme_style": style}

    @app.route("/school/<slug>")
    def school_landing(slug):
        school = School.query.filter(
            func.lower(School.slug) == slug.lower(),
            School.archived_at.is_(None),
        ).first_or_404()
        if school.status == "suspended":
            return render_page(
                "<main class='login-shell'><section class='card login-card'>"
                "<h2>School portal unavailable</h2>"
                "<p>Please contact Smart School SMS support.</p></section></main>",
                title=school.name,
            ), 403
        return redirect(url_for("login", portal="admin", school=school.slug))

    @app.route("/request-demo", methods=["GET", "POST"])
    def request_demo():
        if request.method == "POST":
            try:
                item = DemoRequest(
                    school_name=clean_text(
                        request.form.get("school_name"), maximum=180, required=True, field="School name"
                    ),
                    contact_name=clean_text(
                        request.form.get("contact_name"), maximum=160, required=True, field="Contact name"
                    ),
                    email=clean_email(request.form.get("email"), required=True),
                    phone=clean_text(request.form.get("phone"), maximum=80),
                )
                db.session.add(item)
                db.session.commit()
                flash("Your demo request has been received.", "success")
                return redirect(url_for("request_demo"))
            except ValueError as exc:
                flash(str(exc), "error")
        body = """<main class="login-shell"><section class="card login-card">
        <h2>Request a Smart School SMS demo</h2>
        {% for category,message in get_flashed_messages(with_categories=true) %}
        <div class="flash {{ category }}">{{ message }}</div>{% endfor %}
        <form method="post">{{ csrf() }}
        {{ field('School name','school_name',required=true) }}
        {{ field('Contact name','contact_name',required=true) }}
        {{ field('Email','email','email',required=true) }}
        {{ field('Phone','phone') }}
        <button class="btn green">Request demo</button></form></section></main>"""
        return render_page(body, title="Request Demo")

    @app.route("/owner", methods=["GET", "POST"])
    @login_required("system_admin")
    def owner_portal():
        if request.method == "POST":
            action = request.form.get("action")
            school = db.session.get(School, int(request.form.get("school_id") or 0))
            if not school:
                abort(404)
            if action == "suspend":
                school.status = "suspended"
            elif action == "reactivate":
                school.status = "active"
            elif action == "archive":
                school.status = "archived"
                school.archived_at = datetime.utcnow()
            elif action == "trial":
                school.status = "trial"
                school.trial_ends_at = datetime.utcnow() + timedelta(days=30)
                subscription = Subscription.query.filter_by(school_id=school.id).first()
                if not subscription:
                    subscription = Subscription(school_id=school.id)
                    db.session.add(subscription)
                subscription.plan = "trial"
                subscription.status = "trial"
                subscription.ends_at = school.trial_ends_at
            else:
                abort(400)
            log_action("owner_school_status", f"{action} school {school.id}")
            db.session.commit()
            return redirect(url_for("owner_portal"))

        query = request.args.get("q", "").strip()
        status_filter = request.args.get("status", "").strip()
        school_query = School.query.filter(School.archived_at.is_(None))
        if query:
            school_query = school_query.filter(School.name.ilike(f"%{query[:100]}%"))
        if status_filter:
            school_query = school_query.filter_by(status=status_filter)
        schools = school_query.order_by(School.created_at.desc()).limit(100).all()
        stats = {
            "Total schools": School.query.count(),
            "Active schools": School.query.filter_by(status="active").count(),
            "Suspended": School.query.filter_by(status="suspended").count(),
            "Trial schools": School.query.filter_by(status="trial").count(),
            "Students": Student.query.count(),
            "Teachers": User.query.filter_by(role="teacher").count(),
            "Parents": User.query.filter_by(role="parent").count(),
            "Payments": FinancePayment.query.filter_by(status="success").count(),
        }
        demos = DemoRequest.query.order_by(DemoRequest.created_at.desc()).limit(10).all()
        security = SecurityEvent.query.order_by(SecurityEvent.created_at.desc()).limit(10).all()
        body = (
            """<main class="wrap"><div class="layout">"""
            + ctx["SIDEBAR"]
            + """
        <section class="grid"><header class="page-heading"><div>
        <h1>Platform Owner</h1><p>Schools, subscriptions, health and security.</p>
        </div><a class="btn ghost" href="{{ url_for('health') }}">System health</a></header>
        <div class="grid cols-4">{% for label,value in stats.items() %}
        <article class="card stat"><span>{{ label }}</span><b>{{ value }}</b></article>
        {% endfor %}</div>
        <article class="card"><h2>School management</h2>
        <form method="get" class="grid cols-3"><label>Search<input name="q" value="{{ request.args.get('q','') }}"></label>
        <label>Status<select name="status"><option value="">All</option>
        {% for value in ['active','trial','suspended'] %}<option value="{{ value }}">{{ value|title }}</option>{% endfor %}
        </select></label><button class="btn">Filter</button></form>
        <table><tr><th>School</th><th>Status</th><th>Onboarding</th><th>Actions</th></tr>
        {% for school in schools %}<tr><td>{{ school.name }}<br><small>{{ school.slug }}</small></td>
        <td>{{ school.status|title }}</td><td>Step {{ school.onboarding_step }}/14</td>
        <td><div class="actions">
        {% for action in ['suspend' if school.status != 'suspended' else 'reactivate','trial','archive'] %}
        <form method="post" data-confirm="Confirm {{ action }} for this school?">{{ csrf() }}
        <input type="hidden" name="school_id" value="{{ school.id }}"><input type="hidden" name="action" value="{{ action }}">
        <button class="btn ghost">{{ action|title }}</button></form>{% endfor %}</div></td></tr>{% endfor %}
        </table></article>
        <div class="grid cols-2"><article class="card"><h2>Demo requests</h2>
        {% for item in demos %}<p><b>{{ item.school_name }}</b><br>{{ item.contact_name }} · {{ item.email }}</p>
        {% else %}<p>No demo requests.</p>{% endfor %}</article>
        <article class="card"><h2>Recent security activity</h2>
        {% for item in security %}<p><b>{{ item.event_type }}</b> · {{ item.severity|upper }}<br>{{ item.details }}</p>
        {% else %}<p>No security events.</p>{% endfor %}</article></div>
        </section></div></main>"""
        )
        return render_page(body, title="Platform Owner", stats=stats, schools=schools, demos=demos, security=security)

    @app.route("/setup/<int:step>", methods=["GET", "POST"])
    @login_required("school_admin")
    @school_required
    def setup_wizard(step):
        if step < 1 or step > 14:
            abort(404)
        school = current_school()
        labels = [
            "School profile",
            "Logo and branding",
            "Academic year and term",
            "Classes, programmes and departments",
            "Subjects",
            "Administrator accounts",
            "Teacher accounts",
            "Student and parent import",
            "Fee structures",
            "Result and grading settings",
            "Attendance settings",
            "Communication settings",
            "Payment settings",
            "Final readiness check",
        ]
        data = json.loads(school.onboarding_data or "{}")
        if request.method == "POST":
            try:
                if step == 1:
                    school.name = clean_text(request.form.get("name"), maximum=180, required=True, field="School name")
                    school.short_name = clean_text(request.form.get("short_name"), maximum=80)
                    school.slug = clean_slug(request.form.get("slug") or school.name)
                    school.email = clean_email(request.form.get("email"))
                    school.phone = clean_text(request.form.get("phone"), maximum=80)
                    school.address = clean_text(request.form.get("address"), maximum=500)
                elif step == 2:
                    for key in ("primary_color", "secondary_color", "accent_color"):
                        value = request.form.get(key, "")
                        if value and not value.startswith("#"):
                            raise ValueError("Brand colours must use hexadecimal format.")
                        setattr(school, key, value or getattr(school, key))
                    school.motto = clean_text(request.form.get("motto"), maximum=220)
                    school.welcome_message = clean_text(request.form.get("welcome_message"), maximum=300)
                elif step == 3:
                    school.academic_year = clean_text(
                        request.form.get("academic_year"), maximum=40, required=True, field="Academic year"
                    )
                    school.term = clean_text(request.form.get("term"), maximum=40, required=True, field="Term")
                    school.timezone = clean_text(request.form.get("timezone"), maximum=80) or "Africa/Accra"
                    school.currency = clean_text(request.form.get("currency"), maximum=10).upper() or "GHS"
                else:
                    data[str(step)] = {
                        key: clean_text(value, maximum=300) for key, value in request.form.items() if key != "_csrf"
                    }
                school.onboarding_data = json.dumps(data)
                school.onboarding_step = max(school.onboarding_step, min(step + 1, 14))
                if step == 14:
                    school.onboarded = True
                    school.status = "active"
                log_action("onboarding_step", f"Completed setup step {step}")
                db.session.commit()
                if step == 14:
                    flash("School portal launched successfully.", "success")
                    return redirect(url_for("dashboard"))
                return redirect(url_for("setup_wizard", step=step + 1))
            except ValueError as exc:
                flash(str(exc), "error")
        body = """<main class="wrap"><section class="grid">
        <header class="page-heading"><div><h1>School Setup</h1>
        <p>Step {{ step }} of 14 · {{ labels[step-1] }}</p></div>
        <span class="period-chip">{{ school.name }}</span></header>
        <article class="card"><div class="progress"><span style="width:{{ step/14*100 }}%"></span></div>
        {% for category,message in get_flashed_messages(with_categories=true) %}
        <div class="flash {{ category }}">{{ message }}</div>{% endfor %}
        <form method="post">{{ csrf() }}
        {% if step == 1 %}
        {{ field('School name','name',value=school.name,required=true) }}
        {{ field('Short name','short_name',value=school.short_name) }}
        {{ field('Portal slug','slug',value=school.slug or '') }}
        {{ field('Email','email','email',value=school.email) }}
        {{ field('Phone','phone',value=school.phone) }}
        <label>Address<textarea name="address">{{ school.address }}</textarea></label>
        {% elif step == 2 %}
        {{ field('Motto','motto',value=school.motto) }}
        {{ field('Primary colour','primary_color','color',value=school.primary_color) }}
        {{ field('Secondary colour','secondary_color','color',value=school.secondary_color) }}
        {{ field('Accent colour','accent_color','color',value=school.accent_color) }}
        {{ field('Welcome message','welcome_message',value=school.welcome_message) }}
        <p><a href="{{ url_for('onboarding') }}">Upload logo, signature and stamp</a></p>
        {% elif step == 3 %}
        {{ field('Academic year','academic_year',value=school.academic_year,required=true) }}
        {{ field('Current term','term',value=school.term,required=true) }}
        {{ field('Time zone','timezone',value=school.timezone) }}
        {{ field('Currency','currency',value=school.currency) }}
        {% else %}
        <p>Complete this area using the linked management page, then mark the step complete.</p>
        {% set links={4:'classes_subjects',5:'classes_subjects',6:'users',7:'teachers',
        8:'bulk_import',9:'finance_center',10:'report_details',11:'attendance',
        12:'communications',13:'finance_center'} %}
        {% if links.get(step) %}<p><a class="btn ghost" href="{{ url_for(links[step]) }}">Open {{ labels[step-1] }}</a></p>{% endif %}
        <label>Readiness notes<textarea name="notes">{{ data.get(step|string,{}).get('notes','') }}</textarea></label>
        {% endif %}
        <div class="actions"><button class="btn green">{{ 'Launch School Portal' if step == 14 else 'Save and continue' }}</button>
        {% if step > 1 %}<a class="btn ghost" href="{{ url_for('setup_wizard',step=step-1) }}">Back</a>{% endif %}</div>
        </form></article></section></main>"""
        return render_page(body, title="School Setup", step=step, labels=labels, school=school, data=data)

    @app.route("/assignments", methods=["GET", "POST"])
    @login_required("school_admin", "teacher", "student", "parent")
    @school_required
    def assignments():
        user, school = current_user(), current_school()
        if request.method == "POST":
            if user.role not in {"school_admin", "teacher"}:
                abort(403)
            class_id = int(request.form.get("class_id") or 0)
            subject_id = int(request.form.get("subject_id") or 0)
            class_group = ClassRoom.query.filter_by(id=class_id, school_id=school.id).first()
            subject = Subject.query.filter_by(id=subject_id, school_id=school.id).first()
            if not class_group or not subject:
                abort(403)
            if user.role == "teacher" and not ctx["teacher_can_access"](
                user, class_id, subject_id, school.academic_year, school.term
            ):
                abort(403)
            item = Assignment(
                school_id=school.id,
                class_id=class_id,
                subject_id=subject_id,
                teacher_id=user.id,
                title=clean_text(request.form.get("title"), maximum=180, required=True, field="Assignment title"),
                instructions=clean_text(request.form.get("instructions"), maximum=5000),
                published_at=datetime.utcnow(),
            )
            db.session.add(item)
            log_action("assignment_created", item.title)
            db.session.commit()
            return redirect(url_for("assignments"))
        rows = Assignment.query.filter_by(school_id=school.id).order_by(Assignment.created_at.desc()).limit(100).all()
        classes = ClassRoom.query.filter_by(school_id=school.id).all()
        subjects = Subject.query.filter_by(school_id=school.id).all()
        body = (
            """<main class="wrap"><div class="layout">"""
            + ctx["SIDEBAR"]
            + """
        <section class="grid"><article class="card"><h1>Assignments</h1>
        {% if user.role in ['school_admin','teacher'] %}<form method="post">{{ csrf() }}
        {{ field('Title','title',required=true) }}
        <label>Class<select name="class_id">{% for item in classes %}<option value="{{ item.id }}">{{ item.name }}</option>{% endfor %}</select></label>
        <label>Subject<select name="subject_id">{% for item in subjects %}<option value="{{ item.id }}">{{ item.name }}</option>{% endfor %}</select></label>
        <label>Instructions<textarea name="instructions"></textarea></label>
        <button class="btn green">Publish assignment</button></form>{% endif %}</article>
        <article class="card"><table><tr><th>Assignment</th><th>Published</th></tr>
        {% for item in rows %}<tr><td><b>{{ item.title }}</b><br>{{ item.instructions }}</td>
        <td>{{ fmt_dt(item.published_at) }}</td></tr>{% else %}<tr><td colspan="2">No assignments.</td></tr>{% endfor %}
        </table></article></section></div></main>"""
        )
        return render_page(body, title="Assignments", rows=rows, classes=classes, subjects=subjects)

    @app.get("/admin/import-template/<record_type>.csv")
    @login_required("school_admin")
    @school_required
    def import_template(record_type):
        headers = {
            "student": [
                "full_name",
                "username",
                "admission_no",
                "class",
                "email",
                "phone",
                "guardian_name",
                "guardian_email",
                "guardian_phone",
                "password",
            ],
            "teacher": ["full_name", "username", "email", "phone", "password"],
            "parent": ["full_name", "username", "email", "phone", "password"],
        }.get(record_type)
        if not headers:
            abort(404)
        output = io.StringIO()
        csv.writer(output).writerow(headers)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={record_type}-import-template.csv"},
        )

    @app.get("/admin/import/<int:job_id>/errors.csv")
    @login_required("school_admin")
    @school_required
    def import_error_report(job_id):
        Job = app.feature_models["BulkImportJob"]
        job = Job.query.filter_by(id=job_id, school_id=current_user().school_id).first_or_404()
        staged = json.loads(job.staged_json or "{}")
        report = json.loads(job.report_json or "{}")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["row", "stage", "error"])
        for item in staged.get("errors", []):
            writer.writerow([item.get("row"), "validation", "; ".join(item.get("errors", []))])
        for item in report.get("runtime_errors", []):
            writer.writerow([item.get("row"), "commit", item.get("error", "")])
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=import-{job.id}-errors.csv"},
        )

    @app.get("/admin/export/<kind>.csv")
    @login_required("school_admin", "accountant")
    @school_required
    def platform_export(kind):
        user, school = current_user(), current_school()
        allowed = {
            "students",
            "teachers",
            "parents",
            "attendance",
            "results",
            "fees",
            "payments",
            "messages",
        }
        if kind not in allowed:
            abort(404)
        if user.role == "accountant" and kind not in {"fees", "payments"}:
            abort(403)

        def safe(value):
            value = str(value or "")
            return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value

        headers: list[str]
        rows: list[list[object]]
        if kind in {"students", "teachers", "parents"}:
            role = "student" if kind == "students" else kind[:-1]
            accounts = User.query.filter_by(school_id=school.id, role=role).order_by(User.full_name).all()
            headers = ["name", "username", "email", "phone", "status"]
            rows = [
                [item.full_name, item.username, item.email, item.phone, "active" if item.active else "disabled"]
                for item in accounts
            ]
        elif kind == "attendance":
            Attendance = ctx["Attendance"]
            items = Attendance.query.filter_by(school_id=school.id).all()
            headers = ["student_id", "date", "status", "present_days", "total_days", "term", "academic_year"]
            rows = [
                [
                    item.student_id,
                    item.attendance_date,
                    item.status,
                    item.present_days,
                    item.total_days,
                    item.term,
                    item.academic_year,
                ]
                for item in items
            ]
        elif kind == "results":
            items = Score.query.filter_by(school_id=school.id).all()
            headers = ["student_id", "subject_id", "class_score", "exam_score", "status", "term", "academic_year"]
            rows = [
                [
                    item.student_id,
                    item.subject_id,
                    item.class_score,
                    item.exam_score,
                    item.workflow_status,
                    item.term,
                    item.academic_year,
                ]
                for item in items
            ]
        elif kind == "fees":
            Fee = ctx["Fee"]
            items = Fee.query.filter_by(school_id=school.id).all()
            headers = ["student_id", "amount_due", "amount_paid", "term", "academic_year"]
            rows = [
                [item.student_id, item.amount_due, item.amount_paid, item.term, item.academic_year] for item in items
            ]
        elif kind == "payments":
            items = FinancePayment.query.filter_by(school_id=school.id).all()
            headers = ["reference", "student_id", "amount", "currency", "method", "status", "paid_at"]
            rows = [
                [
                    item.reference,
                    item.student_id,
                    Decimal(item.amount_subunit) / 100,
                    item.currency,
                    item.method,
                    item.status,
                    item.paid_at,
                ]
                for item in items
            ]
        else:
            Communication = ctx["Communication"]
            items = Communication.query.filter_by(school_id=school.id).all()
            headers = ["channel", "audience", "recipient", "subject", "status", "created_at"]
            rows = [
                [item.channel, item.audience, item.recipient, item.subject, item.status, item.created_at]
                for item in items
            ]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows([[safe(value) for value in row] for row in rows])
        log_action("data_export", f"Exported {kind}: {len(rows)} rows")
        db.session.commit()
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={school.slug}-{kind}.csv"},
        )

    @app.route("/finance", methods=["GET", "POST"])
    @login_required("school_admin", "accountant")
    @school_required
    def finance_center():
        user, school = current_user(), current_school()
        if request.method == "POST":
            action = request.form.get("action")
            if action == "fee_structure":
                amount = Decimal(request.form.get("amount") or "0")
                db.session.add(
                    FeeStructure(
                        school_id=school.id,
                        academic_year=school.academic_year,
                        term=school.term,
                        name=clean_text(request.form.get("name"), maximum=160, required=True, field="Fee name"),
                        amount_subunit=int(amount * 100),
                    )
                )
            elif action == "manual_payment":
                invoice = Invoice.query.filter_by(
                    id=int(request.form.get("invoice_id") or 0), school_id=school.id
                ).first()
                if not invoice:
                    abort(403)
                amount = int(Decimal(request.form.get("amount") or "0") * 100)
                reason = clean_text(request.form.get("reason"), maximum=260, required=True, field="Payment reason")
                reference = f"MAN-{school.id}-{datetime.utcnow():%Y%m%d%H%M%S%f}"
                db.session.add(
                    FinancePayment(
                        school_id=school.id,
                        invoice_id=invoice.id,
                        student_id=invoice.student_id,
                        reference=reference,
                        amount_subunit=amount,
                        currency=school.currency,
                        reason=reason,
                        recorded_by=user.id,
                    )
                )
                invoice.balance_subunit = max(invoice.balance_subunit - amount, 0)
                invoice.status = "paid" if invoice.balance_subunit == 0 else "partial"
            else:
                abort(400)
            log_action("finance_change", f"{action} with recorded reason")
            db.session.commit()
            return redirect(url_for("finance_center"))
        structures = FeeStructure.query.filter_by(school_id=school.id).all()
        invoices = Invoice.query.filter_by(school_id=school.id).order_by(Invoice.created_at.desc()).limit(100).all()
        payments = (
            FinancePayment.query.filter_by(school_id=school.id).order_by(FinancePayment.paid_at.desc()).limit(100).all()
        )
        body = (
            """<main class="wrap"><div class="layout">"""
            + ctx["SIDEBAR"]
            + """
        <section class="grid"><header class="page-heading"><div><h1>Finance Centre</h1>
        <p>Fee structures, invoices, payments and reconciliation.</p></div></header>
        <div class="grid cols-2"><article class="card"><h2>Add fee structure</h2>
        <form method="post">{{ csrf() }}<input type="hidden" name="action" value="fee_structure">
        {{ field('Name','name',required=true) }}{{ field('Amount','amount','number',required=true) }}
        <button class="btn green">Save fee</button></form></article>
        <article class="card"><h2>Record manual payment</h2>
        <form method="post">{{ csrf() }}<input type="hidden" name="action" value="manual_payment">
        <label>Invoice<select name="invoice_id">{% for item in invoices %}<option value="{{ item.id }}">{{ item.invoice_number }}</option>{% endfor %}</select></label>
        {{ field('Amount','amount','number',required=true) }}{{ field('Reason','reason',required=true) }}
        <button class="btn green">Record payment</button></form></article></div>
        <article class="card"><h2>Payments</h2><table><tr><th>Reference</th><th>Amount</th><th>Method</th><th>Date</th></tr>
        {% for item in payments %}<tr><td>{{ item.reference }}</td><td>{{ item.currency }} {{ '%.2f'|format(item.amount_subunit/100) }}</td>
        <td>{{ item.method|title }}</td><td>{{ fmt_dt(item.paid_at) }}</td></tr>{% else %}<tr><td colspan="4">No payments.</td></tr>{% endfor %}</table></article>
        </section></div></main>"""
        )
        return render_page(body, title="Finance", structures=structures, invoices=invoices, payments=payments)

    @app.post("/logout-all")
    @login_required()
    def logout_all_devices():
        user = current_user()
        user.session_version = (user.session_version or 1) + 1
        db.session.add(
            SecurityEvent(
                school_id=user.school_id,
                user_id=user.id,
                event_type="logout_all_devices",
                severity="info",
                ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
            )
        )
        db.session.commit()
        session.clear()
        flash("All sessions have been signed out.", "success")
        return redirect(url_for("login"))

    app.platform_models = models
