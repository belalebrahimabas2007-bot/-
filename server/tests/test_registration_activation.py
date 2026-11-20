"""Regression checks for the student registration and Sham Cash activation flow."""
import os
import tempfile
import unittest
from unittest import mock
from datetime import timedelta
from fastapi import HTTPException

_temp = tempfile.TemporaryDirectory()
os.environ["ENGAZ_DB_PATH"] = os.path.join(_temp.name, "test.sqlite3")
os.environ.pop("DATABASE_URL", None)
os.environ.pop("ENGAZ_ADMIN_USERNAME", None)
os.environ.pop("ENGAZ_ADMIN_PASSWORD", None)

from server import main as app  # noqa: E402

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/4d8AAAAASUVORK5CYII="


class RegistrationActivationTests(unittest.TestCase):
    def setUp(self):
        app.metadata.drop_all(app.engine)
        app.metadata.create_all(app.engine)
        with app.engine.begin() as c:
            c.execute(app.insert(app.academic_years).values(label="2026-2027", active=1))
            for name, role in [("admin", "admin"), ("student", "student")]:
                salt = "a" * 32
                user_id = c.execute(app.insert(app.users).values(
                    username=name, email=name+"@example.test", salt=salt,
                    password_hash=app.password_hash("strongpassword", salt),
                    role=role, grade="ninth" if role == "student" else None,
                    created_at=app.utcnow())).inserted_primary_key[0]
                token = name + "_test_token"
                c.execute(app.insert(app.sessions).values(
                    user_id=user_id,
                    token_hash=app.hashlib.sha256(token.encode()).hexdigest(),
                    expires_at=app.utcnow()+timedelta(days=1)))
        self.admin_auth = "Bearer admin_test_token"
        self.student_auth = "Bearer student_test_token"

    def test_payment_setup_snapshot_and_one_use_code(self):
        app.set_grade_price("ninth", app.GradePriceInput(amount=1000), self.admin_auth)
        with self.assertRaises(HTTPException) as exc:
            app.request_activation(app.ActivationRequestInput(), self.student_auth)
        self.assertEqual(exc.exception.status_code, 409)

        app.update_payment_info(app.PaymentSettingsInput(
            sham_cash_number="00963123456", qr_data_url=PNG), self.admin_auth)
        request = app.request_activation(app.ActivationRequestInput(), self.student_auth)
        self.assertFalse(request.get("already_pending", False))
        duplicate = app.request_activation(app.ActivationRequestInput(), self.student_auth)
        self.assertEqual(duplicate["request_id"], request["request_id"])
        self.assertTrue(duplicate["already_pending"])

        app.set_grade_price("ninth", app.GradePriceInput(amount=2000), self.admin_auth)
        status = app.activation_status(self.student_auth)
        self.assertEqual(status["requests"][0]["price"]["amount"], 1000)

        decision = app.decide_activation(
            request["request_id"], app.ActivationDecision(approve=True), self.admin_auth)
        with self.assertRaises(HTTPException):
            app.redeem_activation(app.ActivationCodeInput(code="invalidcode"), self.student_auth)
        app.redeem_activation(app.ActivationCodeInput(code=decision["code"]), self.student_auth)
        self.assertTrue(app.activation_status(self.student_auth)["active"])
        with self.assertRaises(HTTPException):
            app.redeem_activation(app.ActivationCodeInput(code=decision["code"]), self.student_auth)

    def test_registration_resend_invalidates_old_code(self):
        registration = app.Registration(
            username="new_student", password="123456", email="new@example.test",
            grade="ninth", full_name="طالب تجريبي", governorate="دمشق",
            birth_year=2009, gender="male")
        with (mock.patch.object(app.secrets, "randbelow", side_effect=[123456, 654321]),
              mock.patch.object(app, "send_registration_email") as send):
            app.register(registration)
            with self.assertRaises(HTTPException) as exc:
                app.resend_registration(app.ResendRegistration(email=registration.email))
            self.assertEqual(exc.exception.status_code, 429)
            with app.engine.begin() as c:
                c.execute(app.update(app.registration_challenges).values(
                    resend_at=app.utcnow()-timedelta(seconds=1)))
            app.resend_registration(app.ResendRegistration(email=registration.email))
            wrong = app.verify_registration(app.VerifyRegistration(
                email=registration.email, code="123456"))
            self.assertFalse(wrong["ok"])
            result = app.verify_registration(app.VerifyRegistration(
                email=registration.email, code="654321"))
            self.assertTrue(result["ok"])
            self.assertEqual(send.call_count, 2)

    def test_smtp_failure_does_not_leave_unusable_registration(self):
        registration = app.Registration(
            username="another_student", password="123456", email="another@example.test",
            grade="ninth", full_name="طالب آخر", governorate="حمص",
            birth_year=2008, gender="female")
        with mock.patch.object(app, "send_registration_email", side_effect=HTTPException(503, "SMTP down")):
            with self.assertRaises(HTTPException):
                app.register(registration)
        with app.engine.connect() as c:
            self.assertIsNone(c.execute(app.select(app.registration_challenges)).first())


if __name__ == "__main__":
    unittest.main()
