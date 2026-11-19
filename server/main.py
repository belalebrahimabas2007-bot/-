"""Enjaz API — phase 1: grade-scoped curricula and persistent database-ready backend.

DATABASE_URL must point to a durable PostgreSQL database before real use.
SQLite fallback is strictly for local/preview development.
"""
import os
import re
import hashlib
import secrets
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, String, Text,
    DateTime, ForeignKey, UniqueConstraint, select, insert, update, delete,
    func, and_
)
from sqlalchemy.exc import IntegrityError

GRADE_NAMES = {"ninth": "الصف التاسع", "bac_science": "البكالوريا العلمي", "bac_literature": "البكالوريا الأدبي"}
Grade = Literal["ninth", "bac_science", "bac_literature"]
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES = bool(DATABASE_URL)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://"):]
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///" + os.getenv("ENGAZ_DB_PATH", os.path.join(os.path.dirname(__file__), "engaz_stage1.sqlite3"))
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800,
                       connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite:") else {})
metadata = MetaData()

users = Table("users", metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(40), unique=True, nullable=False),
    Column("email", String(255), unique=True, nullable=True),
    Column("salt", String(64), nullable=False),
    Column("password_hash", String(128), nullable=False),
    Column("role", String(20), nullable=False, default="student"),
    Column("grade", String(30), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False))
sessions = Table("sessions", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("token_hash", String(64), unique=True, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False))
academic_years = Table("academic_years", metadata,
    Column("id", Integer, primary_key=True),
    Column("label", String(50), nullable=False, unique=True),
    Column("active", Integer, nullable=False, default=1),
    Column("start_date", String(10), nullable=True),
    Column("exam_end_date", String(10), nullable=True))
subjects = Table("subjects", metadata,
    Column("id", Integer, primary_key=True),
    Column("grade", String(30), nullable=False),
    Column("title", String(120), nullable=False),
    Column("position", Integer, nullable=False, default=0),
    Column("academic_year_id", Integer, ForeignKey("academic_years.id"), nullable=False),
    UniqueConstraint("academic_year_id", "grade", "title", name="uq_subject_year_grade_title"))
lessons = Table("lessons", metadata,
    Column("id", Integer, primary_key=True),
    Column("subject_id", Integer, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
    Column("title", String(150), nullable=False),
    Column("position", Integer, nullable=False, default=0))
progress = Table("progress", metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("lesson_id", Integer, ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True),
    Column("completed_at", DateTime(timezone=True), nullable=False))
reviews = Table("reviews", metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("lesson_id", Integer, ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True),
    Column("stage", Integer, primary_key=True),
    Column("due_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("rating", String(50), nullable=True))

activation_requests = Table("activation_requests", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("academic_year_id", Integer, ForeignKey("academic_years.id"), nullable=False),
    Column("status", String(20), nullable=False, default="pending"),
    Column("payment_reference", String(150), nullable=True),
    Column("reason", String(255), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=True))
activation_codes = Table("activation_codes", metadata,
    Column("id", Integer, primary_key=True),
    Column("request_id", Integer, ForeignKey("activation_requests.id"), unique=True, nullable=False),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("code_hash", String(64), unique=True, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True), nullable=True))
subscriptions = Table("subscriptions", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("academic_year_id", Integer, ForeignKey("academic_years.id"), nullable=False),
    Column("active", Integer, nullable=False, default=1),
    Column("activated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "academic_year_id", name="uq_subscription_year"))
study_sessions = Table("study_sessions", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("lesson_id", Integer, ForeignKey("lessons.id"), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True), nullable=True),
    Column("duration_seconds", Integer, nullable=False, default=0))
notifications_table = Table("notifications", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("title", String(120), nullable=False),
    Column("body", Text, nullable=False),
    Column("read_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False))
year_archives = Table("year_archives", metadata,
    Column("id", Integer, primary_key=True),
    Column("academic_year_id", Integer, ForeignKey("academic_years.id"), unique=True, nullable=False),
    Column("snapshot", Text, nullable=False),
    Column("archived_at", DateTime(timezone=True), nullable=False))
admin_permissions = Table("admin_permissions", metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("permission", String(60), primary_key=True))
metadata.create_all(engine)
with engine.begin() as conn:
    if not conn.execute(select(academic_years.c.id)).first():
        conn.execute(insert(academic_years).values(
            label=os.getenv("ENGAZ_ACADEMIC_YEAR", "2026-2027"),
            active=1))

def utcnow():
    return datetime.now(timezone.utc)

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 310000).hex()

def admin_seed():
    username = os.getenv("ENGAZ_ADMIN_USERNAME", "").strip()
    password = os.getenv("ENGAZ_ADMIN_PASSWORD", "")
    if not username or len(password) < 12:
        return
    with engine.begin() as c:
        if not c.execute(select(users.c.id).where(users.c.role == "admin")).first():
            salt = secrets.token_hex(16)
            c.execute(insert(users).values(username=username, salt=salt,
                password_hash=password_hash(password, salt), role="admin", grade=None, created_at=utcnow()))

admin_seed()
app = FastAPI(title="Enjaz API — development")
origins = [x.strip() for x in os.getenv("ENGAZ_ALLOWED_ORIGINS",
    "https://engaz-student-preview.onrender.com,https://engaz-admin-preview.onrender.com,"
    "http://localhost:5500,http://127.0.0.1:5500").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"])

class Credentials(BaseModel):
    username: str
    password: str

class Registration(Credentials):
    grade: Grade
    email: str | None = None

class SubjectInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    grade: Grade
    position: int | None = Field(default=None, ge=0)

class SubjectEdit(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    position: int | None = Field(default=None, ge=0)

class LessonInput(BaseModel):
    subject_id: int
    title: str = Field(min_length=1, max_length=150)
    position: int | None = Field(default=None, ge=0)

class LessonEdit(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=150)
    position: int | None = Field(default=None, ge=0)

class ReviewRating(BaseModel):
    rating: str

def auth(authorization, role=None):
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "يلزم تسجيل الدخول")
    hashed = hashlib.sha256(token.encode()).hexdigest()
    with engine.connect() as c:
        row = c.execute(select(users).join(sessions, sessions.c.user_id == users.c.id)
            .where(and_(sessions.c.token_hash == hashed, sessions.c.expires_at > utcnow()))).mappings().first()
    if not row:
        raise HTTPException(401, "انتهت الجلسة، سجّل الدخول مجدداً")
    if role and row["role"] != role:
        raise HTTPException(403, "غير مصرح لهذا الحساب")
    return dict(row)

def active_year(c):
    return c.execute(select(academic_years.c.id).where(academic_years.c.active == 1)).scalar_one_or_none()

def clean_title(value):
    result = value.strip()
    if not result:
        raise HTTPException(400, "الاسم لا يمكن أن يكون فارغاً")
    return result

def subject_access(c, subject_id, user):
    row = c.execute(select(subjects).where(subjects.c.id == subject_id)).mappings().first()
    if not row or (user["role"] == "student" and row["grade"] != user["grade"]):
        raise HTTPException(404, "المادة غير موجودة")
    return row

def lesson_access(c, lesson_id, user):
    row = c.execute(select(lessons, subjects.c.grade, subjects.c.academic_year_id)
        .join(subjects, lessons.c.subject_id == subjects.c.id)
        .where(lessons.c.id == lesson_id)).mappings().first()
    if not row or (user["role"] == "student" and row["grade"] != user["grade"]):
        raise HTTPException(404, "الدرس غير موجود")
    return row

@app.get("/api/health")
def health():
    return {"ok": True, "mode": "development", "storage": "postgres" if IS_POSTGRES else "sqlite_preview",
            "durable_storage": IS_POSTGRES}

@app.get("/api/grades")
def grades():
    return [{"id": k, "name": v} for k, v in GRADE_NAMES.items()]

@app.post("/api/register")
def register(data: Registration):
    username = data.username.strip()
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{3,40}", username) or len(data.password) < 10:
        raise HTTPException(400, "اسم المستخدم أو كلمة المرور غير صالحين")
    email = data.email.strip().lower() if data.email else None
    if email and (len(email) > 255 or "@" not in email):
        raise HTTPException(400, "البريد الإلكتروني غير صالح")
    salt = secrets.token_hex(16)
    try:
        with engine.begin() as c:
            c.execute(insert(users).values(username=username, email=email, salt=salt,
                password_hash=password_hash(data.password, salt), role="student",
                grade=data.grade, created_at=utcnow()))
    except IntegrityError:
        raise HTTPException(409, "اسم المستخدم أو البريد الإلكتروني مستخدم")
    return {"ok": True, "message": "تم إنشاء الحساب، يلزم تفعيل الاشتراك قبل استخدام خدمات الدراسة"}

@app.post("/api/login")
def login(data: Credentials):
    with engine.begin() as c:
        u = c.execute(select(users).where(users.c.username == data.username.strip())).mappings().first()
        if not u or not secrets.compare_digest(password_hash(data.password, u["salt"]), u["password_hash"]):
            raise HTTPException(401, "بيانات الدخول غير صحيحة")
        token = secrets.token_urlsafe(40)
        c.execute(insert(sessions).values(user_id=u["id"],
            token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=utcnow()+timedelta(days=14)))
    return {"token": token, "user": {"id": u["id"], "username": u["username"],
        "role": u["role"], "grade": u["grade"]}}

@app.post("/api/logout")
def logout(authorization: str | None = Header(default=None)):
    auth(authorization)
    hashed = hashlib.sha256(authorization.removeprefix("Bearer ").strip().encode()).hexdigest()
    with engine.begin() as c:
        c.execute(delete(sessions).where(sessions.c.token_hash == hashed))
    return {"ok": True}

@app.get("/api/me")
def me(authorization: str | None = Header(default=None)):
    u = auth(authorization)
    return {"id": u["id"], "username": u["username"], "role": u["role"],
            "grade": u["grade"], "email": u["email"]}

@app.get("/api/subjects")
def list_subjects(grade: Grade | None = Query(default=None),
                  authorization: str | None = Header(default=None)):
    user = auth(authorization)
    with engine.connect() as c:
        year = active_year(c)
        if year is None:
            return []
        chosen = user["grade"] if user["role"] == "student" else grade
        stmt = select(subjects).where(subjects.c.academic_year_id == year)
        if chosen:
            stmt = stmt.where(subjects.c.grade == chosen)
        rows = [dict(row) for row in c.execute(stmt.order_by(subjects.c.grade, subjects.c.position, subjects.c.id)).mappings()]
        for s in rows:
            lesson_rows = c.execute(select(lessons).where(lessons.c.subject_id == s["id"])
                .order_by(lessons.c.position, lessons.c.id)).mappings()
            s["lessons"] = []
            for item in lesson_rows:
                l = dict(item)
                l["completed"] = bool(c.execute(select(progress.c.lesson_id).where(and_(
                    progress.c.user_id == user["id"], progress.c.lesson_id == l["id"]))).first()) if user["role"] == "student" else False
                s["lessons"].append(l)
    return rows

@app.get("/api/admin/overview")
def admin_overview(authorization: str | None = Header(default=None)):
    auth(authorization, "admin")
    with engine.connect() as c:
        year = active_year(c)
        student_count = c.execute(select(func.count()).select_from(users).where(users.c.role == "student")).scalar_one()
        subject_count = c.execute(select(func.count()).select_from(subjects).where(subjects.c.academic_year_id == year)).scalar_one() if year else 0
        lesson_count = c.execute(select(func.count()).select_from(lessons).join(subjects, lessons.c.subject_id == subjects.c.id).where(subjects.c.academic_year_id == year)).scalar_one() if year else 0
        completed_count = c.execute(select(func.count()).select_from(progress).join(lessons, progress.c.lesson_id == lessons.c.id).join(subjects, lessons.c.subject_id == subjects.c.id).where(subjects.c.academic_year_id == year)).scalar_one() if year else 0
    return {"students": student_count, "subjects": subject_count, "lessons": lesson_count, "completed_lessons": completed_count}

@app.get("/api/admin/students")
def admin_students(authorization: str | None = Header(default=None)):
    auth(authorization, "admin")
    with engine.connect() as c:
        rows = c.execute(select(users.c.id, users.c.username, users.c.grade, users.c.created_at)
            .where(users.c.role == "student").order_by(users.c.id.desc()).limit(200)).mappings()
        return [{"id": row["id"], "username": row["username"], "grade": row["grade"],
                 "created_at": row["created_at"].isoformat()} for row in rows]


class ActivationRequestInput(BaseModel):
    payment_reference: str = Field(min_length=1, max_length=150)

class ActivationDecision(BaseModel):
    approve: bool
    reason: str | None = None

class ActivationCodeInput(BaseModel):
    code: str = Field(min_length=6, max_length=64)

class StudySessionInput(BaseModel):
    lesson_id: int | None = None
    duration_seconds: int = Field(ge=1, le=43200)

class NotificationInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=2000)
    user_id: int | None = None

class PermissionInput(BaseModel):
    permission: str = Field(pattern="^(curricula|students|activations|notifications|reports|settings)$")
    enabled: bool

def is_main_admin(user):
    with engine.connect() as c:
        first = c.execute(select(users.c.id).where(users.c.role == "admin").order_by(users.c.id)).scalar_one_or_none()
    return user["role"] == "admin" and user["id"] == first

def require_permission(user, permission):
    if user["role"] != "admin":
        raise HTTPException(403, "صلاحيات مسؤول مطلوبة")
    if is_main_admin(user):
        return
    with engine.connect() as c:
        permitted = c.execute(select(admin_permissions.c.user_id).where(and_(
            admin_permissions.c.user_id == user["id"], admin_permissions.c.permission == permission))).first()
    if not permitted:
        raise HTTPException(403, "ليس لديك صلاحية لهذه العملية")

def push_notice(c, user_id, title, body):
    c.execute(insert(notifications_table).values(user_id=user_id, title=title, body=body, created_at=utcnow()))

@app.post("/api/activation/request")
def request_activation(data: ActivationRequestInput, authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    with engine.begin() as c:
        year = active_year(c)
        if not year:
            raise HTTPException(409, "لا توجد سنة دراسية نشطة")
        existing = c.execute(select(activation_requests.c.id).where(and_(
            activation_requests.c.user_id == user["id"], activation_requests.c.academic_year_id == year,
            activation_requests.c.status.in_(["pending", "approved"])))).first()
        if existing:
            raise HTTPException(409, "لديك طلب قيد المعالجة أو موافق عليه")
        row = c.execute(insert(activation_requests).values(user_id=user["id"],
            academic_year_id=year, status="pending", payment_reference=data.payment_reference.strip(),
            created_at=utcnow()))
        return {"ok": True, "request_id": row.inserted_primary_key[0]}

@app.get("/api/activation/status")
def activation_status(authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    with engine.connect() as c:
        year = active_year(c)
        active = c.execute(select(subscriptions.c.id).where(and_(
            subscriptions.c.user_id == user["id"], subscriptions.c.academic_year_id == year,
            subscriptions.c.active == 1))).first() if year else None
        requests = c.execute(select(activation_requests.c.id, activation_requests.c.status,
            activation_requests.c.reason, activation_requests.c.created_at).where(
            activation_requests.c.user_id == user["id"]).order_by(
            activation_requests.c.id.desc()).limit(20)).mappings()
        return {"active": bool(active), "requests": [{"id": x["id"], "status": x["status"],
            "reason": x["reason"], "created_at": x["created_at"].isoformat()} for x in requests]}

@app.get("/api/admin/activations")
def admin_activations(authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    require_permission(user, "activations")
    with engine.connect() as c:
        rows = c.execute(select(activation_requests, users.c.username, users.c.grade).join(
            users, users.c.id == activation_requests.c.user_id).order_by(
            activation_requests.c.id.desc()).limit(200)).mappings()
        return [{"id": x["id"], "username": x["username"], "grade": x["grade"],
            "status": x["status"], "payment_reference": x["payment_reference"],
            "reason": x["reason"], "created_at": x["created_at"].isoformat()} for x in rows]

@app.post("/api/admin/activations/{request_id}/decide")
def decide_activation(request_id: int, data: ActivationDecision, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    require_permission(user, "activations")
    with engine.begin() as c:
        row = c.execute(select(activation_requests).where(
            activation_requests.c.id == request_id)).mappings().first()
        if not row or row["status"] != "pending":
            raise HTTPException(409, "الطلب غير موجود أو تمت معالجته")
        if data.approve:
            code = secrets.token_urlsafe(18)
            c.execute(insert(activation_codes).values(request_id=request_id, user_id=row["user_id"],
                code_hash=hashlib.sha256(code.encode()).hexdigest(), created_at=utcnow()))
            c.execute(update(activation_requests).where(activation_requests.c.id == request_id).values(
                status="approved", decided_at=utcnow()))
            push_notice(c, row["user_id"], "تمت الموافقة على طلبك", "تم إصدار كود التفعيل، تواصل مع الإدارة لاستلامه.")
            return {"ok": True, "code": code, "note": "احفظ الكود وأرسله للطالب؛ لن يظهر مرة ثانية"}
        reason = (data.reason or "لم يتم الدفع عبر شام كاش").strip()
        c.execute(update(activation_requests).where(activation_requests.c.id == request_id).values(
            status="rejected", reason=reason, decided_at=utcnow()))
        push_notice(c, row["user_id"], "تم رفض طلب التفعيل", reason)
        return {"ok": True}

@app.post("/api/activation/redeem")
def redeem_activation(data: ActivationCodeInput, authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    code_hash = hashlib.sha256(data.code.strip().encode()).hexdigest()
    with engine.begin() as c:
        row = c.execute(select(activation_codes, activation_requests.c.academic_year_id).join(
            activation_requests, activation_requests.c.id == activation_codes.c.request_id).where(
            activation_codes.c.code_hash == code_hash)).mappings().first()
        if not row or row["user_id"] != user["id"] or row["used_at"] is not None:
            raise HTTPException(400, "الكود غير صالح أو مستخدم أو ليس لهذا الحساب")
        if row["academic_year_id"] != active_year(c):
            raise HTTPException(409, "الكود لا يخص السنة الدراسية الحالية")
        c.execute(update(activation_codes).where(activation_codes.c.id == row["id"]).values(used_at=utcnow()))
        existing = c.execute(select(subscriptions.c.id).where(and_(
            subscriptions.c.user_id == user["id"],
            subscriptions.c.academic_year_id == row["academic_year_id"]))).first()
        if existing:
            c.execute(update(subscriptions).where(subscriptions.c.id == existing[0]).values(active=1))
        else:
            c.execute(insert(subscriptions).values(user_id=user["id"],
                academic_year_id=row["academic_year_id"], active=1, activated_at=utcnow()))
        push_notice(c, user["id"], "تم تفعيل اشتراك إنجاز", "يمكنك الآن متابعة دراستك.")
    return {"ok": True}

@app.post("/api/study-sessions")
def record_study_session(data: StudySessionInput, authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    with engine.begin() as c:
        if data.lesson_id is not None:
            lesson_access(c, data.lesson_id, user)
        now = utcnow()
        c.execute(insert(study_sessions).values(user_id=user["id"], lesson_id=data.lesson_id,
            started_at=now-timedelta(seconds=data.duration_seconds), ended_at=now,
            duration_seconds=data.duration_seconds))
    return {"ok": True}

@app.get("/api/study-sessions/stats")
def study_session_stats(authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    with engine.connect() as c:
        total = c.execute(select(func.coalesce(func.sum(study_sessions.c.duration_seconds), 0)).where(
            study_sessions.c.user_id == user["id"])).scalar_one()
    return {"total_seconds": int(total)}

@app.get("/api/notifications")
def list_notifications(authorization: str | None = Header(default=None)):
    user = auth(authorization)
    with engine.connect() as c:
        rows = c.execute(select(notifications_table).where(notifications_table.c.user_id == user["id"])
            .order_by(notifications_table.c.id.desc()).limit(200)).mappings()
        return [{"id": x["id"], "title": x["title"], "body": x["body"],
            "read": bool(x["read_at"]), "created_at": x["created_at"].isoformat()} for x in rows]

@app.post("/api/notifications/{notification_id}/read")
def read_notification(notification_id: int, authorization: str | None = Header(default=None)):
    user = auth(authorization)
    with engine.begin() as c:
        c.execute(update(notifications_table).where(and_(
            notifications_table.c.id == notification_id, notifications_table.c.user_id == user["id"]))
            .values(read_at=utcnow()))
    return {"ok": True}

@app.post("/api/admin/notifications")
def send_notification(data: NotificationInput, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    require_permission(user, "notifications")
    with engine.begin() as c:
        if data.user_id:
            target = c.execute(select(users.c.id).where(users.c.id == data.user_id)).first()
            if not target:
                raise HTTPException(404, "الحساب غير موجود")
            recipients = [data.user_id]
        else:
            recipients = list(c.execute(select(users.c.id).where(users.c.role == "student")).scalars())
        for uid in recipients:
            push_notice(c, uid, data.title.strip(), data.body.strip())
    return {"ok": True, "recipients": len(recipients)}

@app.get("/api/admin/archives")
def list_archives(authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    if not is_main_admin(user):
        raise HTTPException(403, "الأرشيف للمسؤول الرئيسي فقط")
    with engine.connect() as c:
        rows = c.execute(select(year_archives, academic_years.c.label).join(
            academic_years, year_archives.c.academic_year_id == academic_years.c.id)
            .order_by(year_archives.c.id.desc())).mappings()
        return [{"id": x["id"], "year": x["label"], "snapshot": json.loads(x["snapshot"]),
            "archived_at": x["archived_at"].isoformat()} for x in rows]

@app.post("/api/admin/archive-current-year")
def archive_current_year(authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    if not is_main_admin(user):
        raise HTTPException(403, "الأرشفة للمسؤول الرئيسي فقط")
    with engine.begin() as c:
        year = active_year(c)
        if not year:
            raise HTTPException(409, "لا توجد سنة دراسية نشطة")
        counts = {"students": c.execute(select(func.count()).select_from(users).where(users.c.role == "student")).scalar_one(),
            "subjects": c.execute(select(func.count()).select_from(subjects).where(subjects.c.academic_year_id == year)).scalar_one(),
            "completed_lessons": c.execute(select(func.count()).select_from(progress).join(
                lessons, progress.c.lesson_id == lessons.c.id).join(
                subjects, lessons.c.subject_id == subjects.c.id).where(
                subjects.c.academic_year_id == year)).scalar_one()}
        c.execute(insert(year_archives).values(academic_year_id=year,
            snapshot=json.dumps(counts, ensure_ascii=False), archived_at=utcnow()))
        c.execute(update(academic_years).where(academic_years.c.id == year).values(active=0))
        c.execute(update(subscriptions).where(subscriptions.c.academic_year_id == year).values(active=0))
    return {"ok": True, "snapshot": counts}

class NewAcademicYearInput(BaseModel):
    label: str = Field(min_length=4, max_length=50)
    start_date: str | None = Field(default=None, pattern=r"^\\d{4}-\\d{2}-\\d{2}$")
    exam_end_date: str | None = Field(default=None, pattern=r"^\\d{4}-\\d{2}-\\d{2}$")

@app.post("/api/admin/academic-years")
def create_academic_year(data: NewAcademicYearInput, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    if not is_main_admin(user):
        raise HTTPException(403, "فتح السنة الدراسية للمسؤول الرئيسي فقط")
    with engine.begin() as c:
        if active_year(c) is not None:
            raise HTTPException(409, "يجب إغلاق وأرشفة السنة الحالية قبل فتح سنة جديدة")
        try:
            row = c.execute(insert(academic_years).values(label=data.label.strip(), active=1,
                start_date=data.start_date, exam_end_date=data.exam_end_date))
        except IntegrityError:
            raise HTTPException(409, "هذه السنة موجودة مسبقاً")
    return {"ok": True, "id": row.inserted_primary_key[0]}

class AdminCreation(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=12, max_length=128)

@app.get("/api/admin/admins")
def list_admin_accounts(authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    if not is_main_admin(user):
        raise HTTPException(403, "إدارة المسؤولين للرئيسي فقط")
    with engine.connect() as c:
        rows = c.execute(select(users.c.id, users.c.username).where(
            users.c.role == "admin").order_by(users.c.id)).mappings()
        return [{"id": x["id"], "username": x["username"],
                 "main": x["id"] == user["id"]} for x in rows]

@app.post("/api/admin/admins")
def create_admin_account(data: AdminCreation, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    if not is_main_admin(user):
        raise HTTPException(403, "إضافة المسؤولين للرئيسي فقط")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{3,40}", data.username.strip()):
        raise HTTPException(400, "اسم المستخدم غير صالح")
    salt = secrets.token_hex(16)
    with engine.begin() as c:
        try:
            row = c.execute(insert(users).values(username=data.username.strip(), salt=salt,
                password_hash=password_hash(data.password, salt), role="admin", grade=None,
                created_at=utcnow()))
        except IntegrityError:
            raise HTTPException(409, "اسم المستخدم مستخدم مسبقاً")
    return {"ok": True, "id": row.inserted_primary_key[0]}

@app.get("/api/admin/permissions/{admin_id}")
def list_permissions(admin_id: int, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    if not is_main_admin(user):
        raise HTTPException(403, "الصلاحيات للمسؤول الرئيسي فقط")
    with engine.connect() as c:
        target = c.execute(select(users.c.id).where(and_(users.c.id == admin_id, users.c.role == "admin"))).first()
        if not target:
            raise HTTPException(404, "المسؤول غير موجود")
        return list(c.execute(select(admin_permissions.c.permission).where(
            admin_permissions.c.user_id == admin_id)).scalars())

@app.put("/api/admin/permissions/{admin_id}")
def set_permission(admin_id: int, data: PermissionInput, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    if not is_main_admin(user) or admin_id == user["id"]:
        raise HTTPException(403, "الصلاحيات للمسؤول الرئيسي فقط")
    with engine.begin() as c:
        target = c.execute(select(users.c.id).where(and_(users.c.id == admin_id, users.c.role == "admin"))).first()
        if not target:
            raise HTTPException(404, "المسؤول غير موجود")
        c.execute(delete(admin_permissions).where(and_(admin_permissions.c.user_id == admin_id,
            admin_permissions.c.permission == data.permission)))
        if data.enabled:
            c.execute(insert(admin_permissions).values(user_id=admin_id, permission=data.permission))
    return {"ok": True}

@app.post("/api/subjects")
def add_subject(data: SubjectInput, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    require_permission(user, "curricula")
    with engine.begin() as c:
        year = active_year(c)
        if year is None:
            raise HTTPException(409, "لا توجد سنة دراسية نشطة")
        position = data.position
        if position is None:
            position = c.execute(select(func.coalesce(func.max(subjects.c.position), 0)+1)
                .where(and_(subjects.c.academic_year_id == year, subjects.c.grade == data.grade))).scalar_one()
        try:
            result = c.execute(insert(subjects).values(title=clean_title(data.title),
                grade=data.grade, position=position, academic_year_id=year))
            return {"ok": True, "id": result.inserted_primary_key[0]}
        except IntegrityError:
            raise HTTPException(409, "هذه المادة موجودة مسبقاً في هذا الصف")

@app.put("/api/subjects/{subject_id}")
def edit_subject(subject_id: int, data: SubjectEdit, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    require_permission(user, "curricula")
    values = data.model_dump(exclude_none=True)
    if "title" in values:
        values["title"] = clean_title(values["title"])
    if not values:
        raise HTTPException(400, "لم تحدد أي تعديل")
    with engine.begin() as c:
        subject_access(c, subject_id, user)
        try:
            c.execute(update(subjects).where(subjects.c.id == subject_id).values(**values))
        except IntegrityError:
            raise HTTPException(409, "اسم المادة موجود مسبقاً")
    return {"ok": True}

@app.post("/api/lessons")
def add_lesson(data: LessonInput, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    require_permission(user, "curricula")
    with engine.begin() as c:
        subject_access(c, data.subject_id, user)
        position = data.position
        if position is None:
            position = c.execute(select(func.coalesce(func.max(lessons.c.position), 0)+1)
                .where(lessons.c.subject_id == data.subject_id)).scalar_one()
        result = c.execute(insert(lessons).values(subject_id=data.subject_id,
            title=clean_title(data.title), position=position))
        return {"ok": True, "id": result.inserted_primary_key[0]}

@app.put("/api/lessons/{lesson_id}")
def edit_lesson(lesson_id: int, data: LessonEdit, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    require_permission(user, "curricula")
    values = data.model_dump(exclude_none=True)
    if "title" in values:
        values["title"] = clean_title(values["title"])
    if not values:
        raise HTTPException(400, "لم تحدد أي تعديل")
    with engine.begin() as c:
        lesson_access(c, lesson_id, user)
        c.execute(update(lessons).where(lessons.c.id == lesson_id).values(**values))
    return {"ok": True}

@app.delete("/api/lessons/{lesson_id}")
def delete_lesson(lesson_id: int, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    require_permission(user, "curricula")
    with engine.begin() as c:
        lesson_access(c, lesson_id, user)
        has_progress = c.execute(select(progress.c.lesson_id).where(progress.c.lesson_id == lesson_id).limit(1)).first()
        has_reviews = c.execute(select(reviews.c.lesson_id).where(reviews.c.lesson_id == lesson_id).limit(1)).first()
        if has_progress or has_reviews:
            raise HTTPException(409, "لا يمكن حذف درس لديه إنجازات أو مراجعات محفوظة للطلاب")
        c.execute(delete(lessons).where(lessons.c.id == lesson_id))
    return {"ok": True}

@app.delete("/api/subjects/{subject_id}")
def delete_subject(subject_id: int, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    require_permission(user, "curricula")
    with engine.begin() as c:
        subject_access(c, subject_id, user)
        has_lessons = c.execute(select(lessons.c.id).where(lessons.c.subject_id == subject_id).limit(1)).first()
        if has_lessons:
            raise HTTPException(409, "احذف دروس المادة أولاً؛ لا يمكن حذف مادة تحتوي دروساً")
        c.execute(delete(subjects).where(subjects.c.id == subject_id))
    return {"ok": True}

@app.post("/api/lessons/{lesson_id}/complete")
def complete_lesson(lesson_id: int, authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    now = utcnow()
    with engine.begin() as c:
        lesson = lesson_access(c, lesson_id, user)
        if lesson["academic_year_id"] != active_year(c):
            raise HTTPException(409, "هذا الدرس ليس من السنة الدراسية النشطة")
        if c.execute(select(progress.c.lesson_id).where(and_(
            progress.c.user_id == user["id"], progress.c.lesson_id == lesson_id))).first():
            return {"ok": True, "already_completed": True}
        c.execute(insert(progress).values(user_id=user["id"], lesson_id=lesson_id, completed_at=now))
        c.execute(insert(reviews).values(user_id=user["id"], lesson_id=lesson_id,
            stage=1, due_at=now+timedelta(days=3)))
    return {"ok": True, "already_completed": False}

@app.get("/api/stats")
def student_stats(authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    with engine.connect() as c:
        year = active_year(c)
        if year is None:
            return {"total": 0, "completed": 0}
        lesson_ids = select(lessons.c.id).join(subjects, lessons.c.subject_id == subjects.c.id).where(
            and_(subjects.c.grade == user["grade"], subjects.c.academic_year_id == year))
        total = c.execute(select(func.count()).select_from(lessons).where(lessons.c.id.in_(lesson_ids))).scalar_one()
        completed = c.execute(select(func.count()).select_from(progress).where(
            and_(progress.c.user_id == user["id"], progress.c.lesson_id.in_(lesson_ids)))).scalar_one()
    return {"total": total, "completed": completed}

@app.get("/api/reviews")
def list_reviews(authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    with engine.connect() as c:
        stmt = select(reviews, lessons.c.title.label("lesson_title"),
            subjects.c.title.label("subject_title")).join(lessons, reviews.c.lesson_id == lessons.c.id
            ).join(subjects, lessons.c.subject_id == subjects.c.id).where(
            and_(reviews.c.user_id == user["id"], subjects.c.grade == user["grade"])
            ).order_by(reviews.c.completed_at.is_not(None), reviews.c.due_at)
        result = []
        for row in c.execute(stmt).mappings():
            r = dict(row)
            for field in ("due_at", "completed_at"):
                if r.get(field):
                    r[field] = r[field].isoformat()
            result.append(r)
    return result

@app.post("/api/reviews/{lesson_id}/{stage}/complete")
def complete_review(lesson_id: int, stage: int, data: ReviewRating,
                    authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    if stage not in (1, 2):
        raise HTTPException(400, "مرحلة مراجعة غير صالحة")
    delays = {"لم أتذكر": 3, "تذكرت بصعوبة": 5, "تذكرت معظم المعلومات": 7, "أتقنت الدرس": 10}
    if data.rating not in delays:
        raise HTTPException(400, "التقييم غير صالح")
    now = utcnow()
    with engine.begin() as c:
        lesson_access(c, lesson_id, user)
        row = c.execute(select(reviews).where(and_(reviews.c.user_id == user["id"],
            reviews.c.lesson_id == lesson_id, reviews.c.stage == stage))).mappings().first()
        if not row:
            raise HTTPException(404, "المراجعة غير موجودة")
        if row["completed_at"]:
            raise HTTPException(409, "المراجعة مكتملة سابقاً")
        c.execute(update(reviews).where(and_(reviews.c.user_id == user["id"],
            reviews.c.lesson_id == lesson_id, reviews.c.stage == stage)
            ).values(completed_at=now, rating=data.rating))
        if stage == 1:
            c.execute(insert(reviews).values(user_id=user["id"], lesson_id=lesson_id,
                stage=2, due_at=now+timedelta(days=delays[data.rating])))
    return {"ok": True, "next_review_days": delays[data.rating] if stage == 1 else None}
