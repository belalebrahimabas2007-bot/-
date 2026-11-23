"""Regression checks for the student registration and Sham Cash activation flow."""
import os
import tempfile
import unittest
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
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

    def test_concurrent_requests_appear_once_in_admin_and_can_retry_after_rejection(self):
        app.set_grade_price("ninth", app.GradePriceInput(amount=50, currency="USD"), self.admin_auth)

        def submit():
            return app.request_activation(app.ActivationRequestInput(), self.student_auth)

        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: submit(), range(4)))

        original, = [result for result in responses if not result.get("already_pending")]
        request_id = original["request_id"]
        self.assertEqual({result["request_id"] for result in responses}, {request_id})
        self.assertEqual(app.activation_status(self.student_auth)["current_request_status"], "pending")
        admin_requests = app.admin_activations(self.admin_auth)
        self.assertEqual([(r["id"], r["status"]) for r in admin_requests], [(request_id, "pending")])

        app.decide_activation(request_id, app.ActivationDecision(approve=False), self.admin_auth)
        self.assertEqual(app.activation_status(self.student_auth)["current_request_status"], "rejected")
        retry = submit()
        self.assertNotEqual(retry["request_id"], request_id)
        self.assertFalse(retry.get("already_pending", False))

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

    def test_https_email_provider_delivery(self):
        from io import BytesIO
        class SuccessResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False

        with mock.patch.dict(os.environ, {
            "ENGAZ_RESEND_API_KEY": "test_key",
            "ENGAZ_EMAIL_FROM": "إنجاز <verify@example.test>",
        }), mock.patch.object(app, "urlopen", return_value=SuccessResponse()) as request:
            self.assertTrue(app.email_service_configured())
            app.send_registration_email("student@example.test", "123456")
            self.assertEqual(request.call_count, 1)
            req = request.call_args.args[0]
            self.assertEqual(req.full_url, "https://api.resend.com/emails")
            payload = app.json.loads(req.data.decode("utf-8"))
            self.assertEqual(payload["to"], ["student@example.test"])
            self.assertIn("123456", payload["text"])

    def test_unconfigured_email_provider_is_unavailable(self):
        with mock.patch.dict(os.environ, {
            "ENGAZ_RESEND_API_KEY": "", "ENGAZ_EMAIL_FROM": "",
            "ENGAZ_SMTP_HOST": "", "ENGAZ_SMTP_FROM": "",
            "ENGAZ_SMTP_USERNAME": "", "ENGAZ_SMTP_PASSWORD": "",
        }):
            self.assertFalse(app.email_service_configured())
            with self.assertRaises(HTTPException) as exc:
                app.send_registration_email("student@example.test", "123456")
            self.assertEqual(exc.exception.status_code, 503)

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
