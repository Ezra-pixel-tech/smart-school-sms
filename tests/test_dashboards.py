import os
import tempfile
import unittest

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
        client = run.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.users["student"].id
            session["_csrf"] = "test-csrf"
        html = client.get("/my-results").get_data(as_text=True)
        self.assertIn("JHS 2", html)
        self.assertIn("Promoted from JHS 1 to JHS 2", html)


if __name__ == "__main__":
    unittest.main()
