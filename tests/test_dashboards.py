import os
import hashlib
import hmac
import json
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("FLASK_DEBUG", "1")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "TestBootstrap@123")
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db").replace("\\", "/")

import run
from werkzeug.security import generate_password_hash


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = run.app.app_context()
        cls.context.push()
        cls.school = run.School(name="Dashboard Test School", academic_year="2026/2027", term="Term 1", onboarded=True)
        run.db.session.add(cls.school)
        run.db.session.flush()
        cls.users = {}
        for role in ["system_admin", "school_admin", "teacher", "student", "accountant", "registrar", "receptionist", "librarian"]:
            account = run.User(school_id=None if role == "system_admin" else cls.school.id, role=role, full_name=role.replace("_", " ").title(), username=f"dashboard_{role}", password_hash=generate_password_hash("Test@12345"), must_change_password=False)
            run.db.session.add(account)
            cls.users[role] = account
        run.db.session.commit()
        student_account = cls.users["student"]
        run.db.session.add(run.Student(school_id=cls.school.id, user_id=student_account.id, admission_no="TEST001"))
        run.db.session.commit()
        cls.student = run.Student.query.filter_by(user_id=student_account.id).first()
        cls.parent = run.User(school_id=cls.school.id, role="parent", full_name="Test Parent",
                              username="dashboard_parent", email="parent@example.com",
                              password_hash=generate_password_hash("Test@12345"), must_change_password=False)
        run.db.session.add(cls.parent)
        run.db.session.flush()
        run.db.session.add(run.ParentStudent(
            school_id=cls.school.id, parent_id=cls.parent.id, student_id=cls.student.id))
        run.db.session.commit()
        run.Config.PAYSTACK_SECRET_KEY = "sk_test_for_unit_tests_only"
        run.Config.PAYSTACK_PUBLIC_KEY = "pk_test_for_unit_tests_only"
        run.Config.PARENT_REPORT_FEE = "10.00"
        run.Config.PAYSTACK_CURRENCY = "GHS"

    @classmethod
    def tearDownClass(cls):
        run.db.session.remove()
        cls.context.pop()

    def test_every_staff_dashboard_renders_reference_layout(self):
        client = run.app.test_client()
        for role, account in self.users.items():
            with self.subTest(role=role):
                with client.session_transaction() as session:
                    session["user_id"] = account.id
                    session["_csrf"] = "test-csrf"
                response = client.get("/dashboard")
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn("kpi-grid", html)
                if role == "student":
                    self.assertIn("Recent Results", html)
                    self.assertIn("Fee Balance", html)
                    self.assertIn("Quick Links", html)
                else:
                    self.assertIn("Quick Actions", html)
                    self.assertIn("line-chart", html)
                    self.assertIn("Fee Collection", html)

    def test_logo_is_packaged(self):
        response = run.app.test_client().get("/static/smart-school-logo.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")

    def test_students_page_renders_for_admin_and_teacher(self):
        client = run.app.test_client()
        for role in ["school_admin", "teacher"]:
            with self.subTest(role=role):
                with client.session_transaction() as session:
                    session["user_id"] = self.users[role].id
                    session["_csrf"] = "test-csrf"
                response = client.get("/students")
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def test_report_includes_class_and_promotion_status(self):
        student = run.Student.query.filter_by(user_id=self.users["student"].id).first()
        class_group = run.ClassRoom(school_id=self.school.id, name="JHS 2")
        run.db.session.add(class_group)
        run.db.session.flush()
        student.class_id = class_group.id
        student.promotion_note = "Promoted from JHS 1 to JHS 2"
        run.db.session.commit()
        Payment = run.app.feature_models["StudentReportPayment"]
        run.db.session.add(Payment(
            school_id=self.school.id, user_id=self.users["student"].id, student_id=student.id,
            academic_year=self.school.academic_year, term=self.school.term,
            reference="student-report-existing-test", amount_subunit=1000,
            currency="GHS", status="success"))
        run.db.session.commit()
        client = run.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.users["student"].id
            session["_csrf"] = "test-csrf"
        html = client.get("/my-results").get_data(as_text=True)
        self.assertIn("JHS 2", html)
        self.assertIn("Promoted from JHS 1 to JHS 2", html)

    def test_student_report_is_locked_without_payment(self):
        Payment = run.app.feature_models["StudentReportPayment"]
        Payment.query.filter_by(user_id=self.users["student"].id).delete()
        run.db.session.commit()
        client = run.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.users["student"].id
            session["_csrf"] = "test-csrf"
        response = client.get("/my-results")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/student/report/payment", response.location)
        pdf = client.get("/my-results.pdf")
        self.assertEqual(pdf.status_code, 302)

    def test_admin_forgot_password_uses_generic_response(self):
        self.users["school_admin"].email = "admin-reset@example.com"
        run.db.session.commit()
        client = run.app.test_client()
        with client.session_transaction() as session:
            session["_csrf"] = "test-csrf"
        with patch("smart_features._send_resend") as sender:
            response = client.post("/forgot-password", data={
                "_csrf": "test-csrf", "email": "admin-reset@example.com"
            }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("If that administrator email exists", response.get_data(as_text=True))
        sender.assert_called_once()

    def test_parent_report_is_locked_until_verified_payment(self):
        client = run.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.parent.id
            session["_csrf"] = "test-csrf"
        response = client.get(f"/parent/report/{self.student.id}")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/payment", response.location)

        with patch("run.paystack_request") as paystack:
            paystack.return_value = {"status": True, "data": {
                "authorization_url": "https://checkout.paystack.com/test-access",
                "reference": "ignored",
            }}
            initialized = client.post(
                f"/parent/report/{self.student.id}/paystack",
                data={"_csrf": "test-csrf"})
        self.assertEqual(initialized.status_code, 302)
        self.assertTrue(initialized.location.startswith("https://checkout.paystack.com/"))
        payment = run.ParentReportPayment.query.filter_by(
            parent_id=self.parent.id, student_id=self.student.id).order_by(
            run.ParentReportPayment.id.desc()).first()
        self.assertEqual(payment.status, "pending")

        with patch("run.paystack_request") as paystack:
            paystack.return_value = {"status": True, "data": {
                "status": "success", "reference": payment.reference,
                "amount": 1000, "currency": "GHS", "id": 12345,
            }}
            verified = client.get(
                f"/payments/paystack/callback?reference={payment.reference}",
                follow_redirects=True)
        self.assertEqual(verified.status_code, 200)
        self.assertIn("ACADEMIC REPORT CARD", verified.get_data(as_text=True))
        self.assertEqual(payment.status, "success")

    def test_paystack_webhook_requires_valid_signature(self):
        body = json.dumps({"event": "charge.success", "data": {
            "reference": "unknown-reference"}}).encode()
        client = run.app.test_client()
        self.assertEqual(client.post("/payments/paystack/webhook", data=body,
                                    content_type="application/json").status_code, 401)
        signature = hmac.new(run.Config.PAYSTACK_SECRET_KEY.encode(),
                             body, hashlib.sha512).hexdigest()
        response = client.post("/payments/paystack/webhook", data=body,
                               content_type="application/json",
                               headers={"x-paystack-signature": signature})
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
