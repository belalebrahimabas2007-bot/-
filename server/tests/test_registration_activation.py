"""Regression checks for the student registration and Sham Cash activation flow."""
import os
import tempfile
import unittest
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from fastapi import HTTPException

_import_db = tempfile.TemporaryDirectory()
with mock.patch.dict(os.environ, {
    "DATABASE_URL": "",
    "ENGAZ_DB_PATH": os.path.join(_import_db.name, "import.sqlite3"),
    "ENGAZ_ADMIN_USERNAME": "",
    "ENGAZ_ADMIN_PASSWORD": "",
}):
    from server import main as app

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/4d8AAAAASUVORK5CYII="


class RegistrationActivationTests(unittest.TestCase):
    def setUp(self):
        database = self.enterContext(tempfile.TemporaryDirectory())
        engine = app.create_engine("sqlite:///" + os.path.join(database, "test.sqlite3"))
        self.addCleanup(engine.dispose)
        self.enterContext(mock.patch.object(app, "engine", engine))
        app.metadata.create_all(engine)
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
        with self.assertRaises(HTTPException) as exc:
            app.request_activation(app.ActivationRequestInput(), self.student_auth)
        self.assertEqual(exc.exception.status_code, 409)

        app.set_grade_price("ninth", app.GradePriceInput(amount=50, currency="USD"), self.admin_auth)
        self.assertIsNone(app.student_payment_info(self.student_auth)["qr_data_url"])
        request = app.request_activation(app.ActivationRequestInput(), self.student_auth)
        self.assertEqual(request["price"], {"amount": 50, "currency": "USD"})
        app.update_payment_info(app.PaymentSettingsInput(
            sham_cash_number="00963123456", qr_data_url=PNG), self.admin_auth)
        self.assertEqual(app.student_payment_info(self.student_auth)["qr_data_url"], PNG)
        self.assertFalse(request.get("already_pending", False))
        duplicate = app.request_activation(app.ActivationRequestInput(), self.student_auth)
        self.assertEqual(duplicate["request_id"], request["request_id"])
        self.assertTrue(duplicate["already_pending"])

        app.set_grade_price("ninth", app.GradePriceInput(amount=60, currency="USD"), self.admin_auth)
        status = app.activation_status(self.student_auth)
        self.assertEqual(status["requests"][0]["price"], {"amount": 50, "currency": "USD"})

        decision = app.decide_activation(
            request["request_id"], app.ActivationDecision(approve=True), self.admin_auth)
        with self.assertRaises(HTTPException):
            app.redeem_activation(app.ActivationCodeInput(code="invalidcode"), self.student_auth)
        other = self.registration("+963944444444")
        app.register(other)
        login = app.login(app.Credentials(username=other.phone, password=other.password))
        with self.assertRaises(HTTPException) as exc:
            app.redeem_activation(app.ActivationCodeInput(code=decision["code"]), "Bearer " + login["token"])
        self.assertEqual(exc.exception.status_code, 400)
        self.assertFalse(app.activation_status("Bearer " + login["token"])["active"])
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
        rejected = app.activation_status(self.student_auth)
        self.assertEqual(rejected["current_request_status"], "rejected")
        self.assertEqual(rejected["requests"][0]["reason"], "لم يتم الدفع عبر شام كاش")
        retry = submit()
        self.assertNotEqual(retry["request_id"], request_id)
        self.assertFalse(retry.get("already_pending", False))

    def registration(self, phone):
        return app.Registration(phone=phone, password="strongpassword",
            grade="ninth", full_name="طالب تجريبي", governorate="دمشق",
            birth_year=2009, gender="male")

    def test_phone_registration_and_login_do_not_require_verification(self):
        data = self.registration("0912345678")
        with mock.patch.object(app, "send_registration_email") as send:
            registration = app.register(data)
        self.assertFalse(registration["requires_email_verification"])
        self.assertFalse(registration["requires_phone_verification"])
        send.assert_not_called()
        login = app.login(app.Credentials(username="+963912345678", password=data.password))
        profile = app.me("Bearer " + login["token"])
        self.assertEqual(profile["phone"], "+963912345678")
        self.assertEqual(profile["profile"]["full_name"], data.full_name)
        self.assertFalse(app.activation_status("Bearer " + login["token"])["active"])
        with self.assertRaises(HTTPException) as exc:
            app.login(app.Credentials(username=profile["phone"], password="wrong-password"))
        self.assertEqual(exc.exception.status_code, 401)

    def test_invalid_and_duplicate_phone_registration(self):
        app.register(self.registration("0912345678"))
        for phone, status in [("+963912345678", 409), ("+905551234567", 400)]:
            with self.subTest(phone=phone):
                with self.assertRaises(HTTPException) as exc:
                    app.register(self.registration(phone))
                self.assertEqual(exc.exception.status_code, status)
        with app.engine.connect() as connection:
            matching = connection.execute(app.select(app.users.c.id).where(
                app.users.c.username == "+963912345678")).all()
            self.assertEqual(len(matching), 1)
            self.assertIsNone(connection.execute(app.select(app.registration_challenges)).first())

    def test_https_email_provider_delivery(self):
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


if __name__ == "__main__":
    unittest.main()
