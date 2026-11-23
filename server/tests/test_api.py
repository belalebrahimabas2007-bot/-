import os
import tempfile
import unittest
from pathlib import Path

tmp = tempfile.TemporaryDirectory()
os.environ["ENGAZ_DB_PATH"] = str(Path(tmp.name) / "test.sqlite3")
os.environ["ENGAZ_ADMIN_USERNAME"] = "testadmin"
os.environ["ENGAZ_ADMIN_PASSWORD"] = "test-password-at-least-12"
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
from server import main
from server.main import app

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def login(self, username, password):
        r = self.client.post("/api/login", json={"username": username, "password": password, "grade": "ninth"})
        self.assertEqual(r.status_code, 200, r.text)
        return {"Authorization": "Bearer " + r.json()["token"]}

    def test_student_review_cycle(self):
        username = "+963912345678"
        password = "student-long-password"
        r = self.client.post("/api/register", json={"phone": username, "password": password, "grade": "ninth",
            "full_name": "طالب تجريبي", "governorate": "دمشق", "birth_year": 2009, "gender": "male"})
        self.assertEqual(r.status_code, 200, r.text)
        admin = self.login("testadmin", "test-password-at-least-12")
        self.assertEqual(self.client.post("/api/subjects", json={"title": "رياضيات", "grade": "ninth"}, headers=admin).status_code, 200)
        subjects = self.client.get("/api/subjects", headers=admin).json()
        subject_id = subjects[-1]["id"]
        self.assertEqual(self.client.post("/api/lessons", json={"subject_id": subject_id, "title": "درس تجريبي"}, headers=admin).status_code, 200)
        lesson_id = self.client.get("/api/subjects", headers=admin).json()[-1]["lessons"][-1]["id"]
        student = self.login(username, password)
        self.assertEqual(self.client.get("/api/subjects", headers=student).status_code, 403)
        request = self.client.post("/api/activation/request",
            json={"payment_reference": "test-payment-reference"}, headers=student)
        self.assertEqual(request.status_code, 200, request.text)
        decision = self.client.post(f"/api/admin/activations/{request.json()['request_id']}/decide",
            json={"approve": True}, headers=admin)
        self.assertEqual(decision.status_code, 200, decision.text)
        redeem = self.client.post("/api/activation/redeem",
            json={"code": decision.json()["code"]}, headers=student)
        self.assertEqual(redeem.status_code, 200, redeem.text)
        self.assertEqual(self.client.post("/api/activation/redeem",
            json={"code": decision.json()["code"]}, headers=student).status_code, 400)
        self.assertTrue(self.client.get("/api/activation/status", headers=student).json()["active"])
        plan = self.client.get("/api/plan/today", headers=student)
        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertIn(lesson_id, [lesson["id"] for lesson in plan.json()["new_lessons"]])
        self.assertEqual(self.client.post("/api/study-sessions",
            json={"duration_seconds": 300}, headers=student).status_code, 200)
        self.assertEqual(self.client.get("/api/study-sessions/stats",
            headers=student).json()["total_seconds"], 300)
        self.assertEqual(self.client.post(f"/api/lessons/{lesson_id}/complete", headers=student).status_code, 200)
        reviews = self.client.get("/api/reviews", headers=student).json()
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["stage"], 1)
        self.assertGreaterEqual(datetime.fromisoformat(reviews[0]["due_at"]) - main.utcnow(),
            timedelta(days=3, minutes=-1))
        plan = self.client.get("/api/plan/today", headers=student).json()
        self.assertNotIn(lesson_id, [lesson["id"] for lesson in plan["new_lessons"]])
        self.assertFalse(plan["reviews"])
        with main.engine.begin() as connection:
            connection.execute(main.update(main.reviews).where(main.reviews.c.lesson_id == lesson_id)
                .values(due_at=main.utcnow()-timedelta(minutes=1)))
        plan = self.client.get("/api/plan/today", headers=student).json()
        self.assertEqual([r["lesson_id"] for r in plan["reviews"]], [lesson_id])
        self.assertEqual(self.client.post(f"/api/reviews/{lesson_id}/1/complete", json={"rating": "أتقنت الدرس"}, headers=student).status_code, 200)
        reviews = self.client.get("/api/reviews", headers=student).json()
        self.assertEqual(len(reviews), 2)
        first = next(row for row in reviews if row["stage"] == 1)
        second = next(row for row in reviews if row["stage"] == 2)
        self.assertEqual(datetime.fromisoformat(second["due_at"]) -
            datetime.fromisoformat(first["completed_at"]), timedelta(days=10))
        self.assertEqual(self.client.post(f"/api/reviews/{lesson_id}/2/complete", json={"rating": "أتقنت الدرس"}, headers=student).status_code, 200)
        self.assertEqual(len(self.client.get("/api/reviews", headers=student).json()), 2)

        # Seed a backlog larger than today's capacity across two subjects.
        with main.engine.begin() as connection:
            user_id = connection.execute(main.select(main.users.c.id).where(
                main.users.c.username == username)).scalar_one()
            year = main.active_year(connection)
            science = connection.execute(main.insert(main.subjects).values(
                grade="ninth", title="العلوم", academic_year_id=year)).inserted_primary_key[0]
            for subject in (subject_id, science):
                for index in range(6):
                    new_id = connection.execute(main.insert(main.lessons).values(
                        subject_id=subject, title=f"درس {index}")).inserted_primary_key[0]
                    if index < 3:
                        connection.execute(main.insert(main.progress).values(
                            user_id=user_id, lesson_id=new_id, completed_at=main.utcnow()))
                        connection.execute(main.insert(main.reviews).values(
                            user_id=user_id, lesson_id=new_id, stage=1,
                            due_at=main.utcnow()-timedelta(days=2-index)))
        plan = self.client.get("/api/plan/today", headers=student).json()
        self.assertEqual((len(plan["new_lessons"]), len(plan["reviews"])), (3, 3))
        self.assertEqual(len({r["subject"] for r in plan["reviews"]}), 2)
        shorter = self.client.get("/api/plan/today?hours=1", headers=student).json()
        self.assertEqual((len(shorter["new_lessons"]), len(shorter["reviews"])), (1, 1))


if __name__ == "__main__":
    unittest.main()
