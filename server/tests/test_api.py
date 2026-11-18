import os
import tempfile
import unittest
from pathlib import Path

tmp = tempfile.TemporaryDirectory()
os.environ["ENGAZ_DB_PATH"] = str(Path(tmp.name) / "test.sqlite3")
os.environ["ENGAZ_ADMIN_USERNAME"] = "testadmin"
os.environ["ENGAZ_ADMIN_PASSWORD"] = "test-password-at-least-12"
from fastapi.testclient import TestClient
from server.main import app

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def login(self, username, password):
        r = self.client.post("/api/login", json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return {"Authorization": "Bearer " + r.json()["token"]}

    def test_student_review_cycle(self):
        username = "student_" + __import__("secrets").token_hex(5)
        password = "student-long-password"
        r = self.client.post("/api/register", json={"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        admin = self.login("testadmin", "test-password-at-least-12")
        self.assertEqual(self.client.post("/api/subjects", json={"title": "رياضيات"}, headers=admin).status_code, 200)
        subjects = self.client.get("/api/subjects", headers=admin).json()
        subject_id = subjects[-1]["id"]
        self.assertEqual(self.client.post("/api/lessons", json={"subject_id": subject_id, "title": "درس تجريبي"}, headers=admin).status_code, 200)
        lesson_id = self.client.get("/api/subjects", headers=admin).json()[-1]["lessons"][-1]["id"]
        student = self.login(username, password)
        self.assertEqual(self.client.post(f"/api/lessons/{lesson_id}/complete", headers=student).status_code, 200)
        reviews = self.client.get("/api/reviews", headers=student).json()
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["stage"], 1)
        self.assertEqual(self.client.post(f"/api/reviews/{lesson_id}/1/complete", json={"rating": "أتقنت الدرس"}, headers=student).status_code, 200)
        reviews = self.client.get("/api/reviews", headers=student).json()
        self.assertEqual(len(reviews), 2)
        self.assertEqual(self.client.post(f"/api/reviews/{lesson_id}/2/complete", json={"rating": "أتقنت الدرس"}, headers=student).status_code, 200)
        self.assertEqual(len(self.client.get("/api/reviews", headers=student).json()), 2)

if __name__ == "__main__":
    unittest.main()
