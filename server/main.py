"""Enjaz API — phase 1: grade-scoped curricula and persistent database-ready backend.

DATABASE_URL must point to a durable PostgreSQL database before real use.
SQLite fallback is strictly for local/preview development.
"""
import os
import re
import hashlib
import secrets
import json
import logging
import smtplib
import base64
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from email.message import EmailMessage
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

grade_prices = Table("grade_prices", metadata,
    Column("grade", String(30), primary_key=True),
    Column("amount", Integer, nullable=False),
    Column("currency", String(10), nullable=False, default="SYP"),
    Column("updated_at", DateTime(timezone=True), nullable=False))
activation_request_prices = Table("activation_request_prices", metadata,
    Column("request_id", Integer, ForeignKey("activation_requests.id", ondelete="CASCADE"), primary_key=True),
    Column("amount", Integer, nullable=False),
    Column("currency", String(10), nullable=False))
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
user_profiles = Table("user_profiles", metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("full_name", String(150), nullable=False),
    Column("governorate", String(50), nullable=False),
    Column("birth_year", Integer, nullable=False),
    Column("gender", String(10), nullable=False),
    Column("school", String(180), nullable=True))
registration_challenges = Table("registration_challenges", metadata,
    Column("id", Integer, primary_key=True),
    Column("email", String(255), unique=True, nullable=False),
    Column("username", String(40), nullable=False),
    Column("payload", Text, nullable=False),
    Column("code_hash", String(64), nullable=False),
    Column("nonce", String(32), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("resend_at", DateTime(timezone=True), nullable=False),
    Column("attempts", Integer, nullable=False, default=0))
app_migrations = Table("app_migrations", metadata,
    Column("key", String(100), primary_key=True),
    Column("applied_at", DateTime(timezone=True), nullable=False))
payment_settings = Table("payment_settings", metadata,
    Column("id", Integer, primary_key=True),
    Column("sham_cash_number", String(100), nullable=False, default=""),
    Column("qr_data_url", Text, nullable=True),
    Column("whatsapp_number", String(25), nullable=False, default="963939791604"),
    Column("updated_at", DateTime(timezone=True), nullable=False))
payment_settings_history = Table("payment_settings_history", metadata,
    Column("id", Integer, primary_key=True),
    Column("admin_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("old_number", String(100), nullable=False),
    Column("new_number", String(100), nullable=False),
    Column("qr_changed", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False))
metadata.create_all(engine)
with engine.begin() as conn:
    if not conn.execute(select(academic_years.c.id)).first():
        conn.execute(insert(academic_years).values(
            label=os.getenv("ENGAZ_ACADEMIC_YEAR", "2026-2027"),
            active=1))

def utcnow():
    return datetime.now(timezone.utc)

def apply_initial_subscription_price():
    """Apply the owner's 50 USD price once; future admin edits remain authoritative."""
    key = "initial_50_usd_all_grades_2026_11_23"
    with engine.begin() as c:
        if c.execute(select(app_migrations.c.key).where(app_migrations.c.key == key)).first():
            return
        now = utcnow()
        for grade in GRADE_NAMES:
            if c.execute(select(grade_prices.c.grade).where(grade_prices.c.grade == grade)).first():
                c.execute(update(grade_prices).where(grade_prices.c.grade == grade).values(
                    amount=50, currency="USD", updated_at=now))
            else:
                c.execute(insert(grade_prices).values(
                    grade=grade, amount=50, currency="USD", updated_at=now))
        c.execute(insert(app_migrations).values(key=key, applied_at=now))


apply_initial_subscription_price()


def as_utc(value):
    """SQLite may return naive datetimes even for timezone-aware columns."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

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

class Registration(BaseModel):
    phone: str = Field(min_length=9, max_length=30)
    password: str = Field(min_length=6, max_length=128)
    grade: Grade
    full_name: str = Field(min_length=3, max_length=150)
    governorate: str
    birth_year: int = Field(ge=1950, le=2020)
    gender: Literal["male", "female"]
    school: str | None = Field(default=None, max_length=180)

class VerifyRegistration(BaseModel):
    email: str
    code: str = Field(pattern=r"^\d{6}$")

class ResendRegistration(BaseModel):
    email: str

GOVERNORATES = {"دمشق", "ريف دمشق", "حلب", "حمص", "حماة", "اللاذقية", "طرطوس",
                "إدلب", "درعا", "السويداء", "القنيطرة", "دير الزور", "الرقة", "الحسكة"}

def email_service_configured():
    """True only when an actual mail delivery provider has been configured."""
    api_key = os.getenv("ENGAZ_RESEND_API_KEY", "").strip()
    api_sender = os.getenv("ENGAZ_EMAIL_FROM", "").strip()
    smtp_fields = ("ENGAZ_SMTP_HOST", "ENGAZ_SMTP_FROM",
                   "ENGAZ_SMTP_USERNAME", "ENGAZ_SMTP_PASSWORD")
    return bool(api_key and api_sender) or all(os.getenv(k, "").strip() for k in smtp_fields)


def send_registration_email(address, code):
    # HTTPS email API works on hosting providers that restrict outbound SMTP.
    api_key = os.getenv("ENGAZ_RESEND_API_KEY", "").strip()
    api_sender = os.getenv("ENGAZ_EMAIL_FROM", "").strip()
    if api_key and api_sender:
        payload = {
            "from": api_sender,
            "to": [address],
            "subject": "رمز التحقق - إنجاز",
            "text": "رمز التحقق لإنشاء حساب إنجاز: " + code +
                    "\nالرمز صالح لمدة 10 دقائق. لا تشاركه مع أحد.",
        }
        request = Request("https://api.resend.com/emails",
                          data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": "Bearer " + api_key,
                                   "Content-Type": "application/json",
                                   "Accept": "application/json",
                                   "User-Agent": "Enjaz-Student-App/1.0"},
                          method="POST")
        try:
            with urlopen(request, timeout=12) as response:
                if response.status not in (200, 201, 202):
                    raise HTTPException(503, "تعذر إرسال رسالة التحقق؛ تحقق من إعدادات البريد")
        except HTTPError as exc:
            # Report actionable categories without exposing provider responses,
            # recipient addresses, or authentication secrets.
            raw_error = exc.read(4096)
            try:
                provider_error = json.loads(raw_error.decode("utf-8"))
                provider_message = str(provider_error.get("message", "")).lower() if isinstance(provider_error, dict) else ""
                provider_name = str(provider_error.get("name", "")) if isinstance(provider_error, dict) else ""
            except (ValueError, UnicodeError, AttributeError):
                provider_message = ""
                provider_name = ""
            # Only metadata is logged; never log email addresses, keys, codes,
            # raw provider responses, or user-provided message text.
            logging.getLogger("enjaz.email").warning(
                "Resend delivery failed status=%s content_type=%s body_bytes=%s error_name=%s",
                exc.code,
                (exc.headers.get("Content-Type", "") if exc.headers else "")[:60],
                len(raw_error),
                re.sub(r"[^a-zA-Z0-9_-]", "", provider_name)[:40],
            )
            # The provider may mention account/recipient emails in its error.
            # Redact these and any API-token-looking strings before showing a
            # short diagnostic to the user.
            safe_message = re.sub(
                r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                "[email]", provider_message)
            safe_message = re.sub(r"re_[A-Za-z0-9_-]+", "[key]", safe_message)
            safe_message = re.sub(r"[<>\x00-\x1f]", " ", safe_message)[:180]
            if exc.code == 401:
                reason = "مفتاح Resend غير صالح؛ راجع ENGAZ_RESEND_API_KEY في Render"
            elif ("testing email" in provider_message or
                  "test email" in provider_message or
                  "only send" in provider_message or
                  "only be used" in provider_message or
                  "own email address" in provider_message or
                  "your own email" in provider_message or
                  ("recipient" in provider_message and "verify" in provider_message)):
                reason = "بريد Resend التجريبي يسمح بالإرسال فقط إلى البريد المرتبط بحساب Resend"
            elif ("domain" in provider_message or "sender" in provider_message or
                  "from address" in provider_message):
                reason = "عنوان المرسل غير مسموح أو النطاق غير موثّق في Resend"
            elif exc.code == 429:
                reason = "تجاوزت حد الإرسال في Resend؛ حاول لاحقاً"
            elif exc.code == 403:
                reason = ("رفض Resend الإرسال (403): " + safe_message) if safe_message else "رفض Resend الإرسال (403)؛ تحقق من البريد المستلم وصلاحيات المفتاح"
            else:
                reason = f"رفض Resend إرسال البريد (رمز الخطأ {exc.code})"
            raise HTTPException(503, reason) from None
        except (URLError, TimeoutError, OSError):
            raise HTTPException(503, "تعذر الاتصال بخدمة Resend؛ حاول لاحقاً")
        return

    host = os.getenv("ENGAZ_SMTP_HOST", "").strip()
    sender = os.getenv("ENGAZ_SMTP_FROM", "").strip()
    username = os.getenv("ENGAZ_SMTP_USERNAME", "").strip()
    password = os.getenv("ENGAZ_SMTP_PASSWORD", "")
    if not all([host, sender, username, password]):
        raise HTTPException(503, "التحقق بالبريد غير متاح حالياً؛ يجب إعداد خدمة إرسال البريد")
    port = int(os.getenv("ENGAZ_SMTP_PORT", "587"))
    message = EmailMessage()
    message["Subject"] = "رمز التحقق - إنجاز"
    message["From"] = sender
    message["To"] = address
    message.set_content("رمز التحقق لإنشاء حساب إنجاز: " + code + "\nالرمز صالح لمدة 10 دقائق. لا تشاركه مع أحد.")
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=12) as smtp:
                smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=12) as smtp:
                smtp.starttls()
                smtp.login(username, password)
                smtp.send_message(message)
    except Exception:
        raise HTTPException(503, "تعذر إرسال رسالة التحقق؛ حاول لاحقاً")

def normalize_syrian_phone(value):
    """Accept Syrian mobile numbers and store as +9639XXXXXXXX."""
    digits = str(value).strip().translate(str.maketrans(
        "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
    digits = re.sub(r"[\s()\-]", "", digits)
    if digits.startswith("00963"):
        digits = "+963" + digits[5:]
    elif digits.startswith("963"):
        digits = "+" + digits
    elif digits.startswith("09"):
        digits = "+963" + digits[1:]
    elif digits.startswith("9"):
        digits = "+963" + digits
    if not re.fullmatch(r"\+9639[0-9]{8}", digits):
        raise HTTPException(400, "أدخل رقم موبايل سوري صحيح يبدأ بـ +9639 ويتبعه 8 أرقام")
    return digits


def validate_registration(data):
    phone = normalize_syrian_phone(data.phone)
    if data.governorate not in GOVERNORATES:
        raise HTTPException(400, "اختر محافظة سورية صحيحة")
    if not data.full_name.strip() or len(data.full_name.strip()) < 3:
        raise HTTPException(400, "أدخل الاسم الكامل")
    return phone


def create_registration_challenge(c, email, username, payload):
    code = f"{secrets.randbelow(1000000):06d}"
    nonce = secrets.token_hex(16)
    now = utcnow()
    c.execute(delete(registration_challenges).where(
        (registration_challenges.c.email == email) | (registration_challenges.c.username == username)))
    c.execute(insert(registration_challenges).values(email=email, username=username,
        payload=json.dumps(payload, ensure_ascii=False), nonce=nonce,
        code_hash=hashlib.sha256((nonce+code).encode()).hexdigest(),
        expires_at=now+timedelta(minutes=10), resend_at=now+timedelta(seconds=60), attempts=0))
    return code

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

def require_student_subscription(user):
    if user["role"] != "student":
        raise HTTPException(403, "حساب طالب مطلوب")
    with engine.connect() as c:
        year = active_year(c)
        valid = c.execute(select(subscriptions.c.id).where(and_(
            subscriptions.c.user_id == user["id"],
            subscriptions.c.academic_year_id == year,
            subscriptions.c.active == 1))).first() if year else None
    if not valid:
        raise HTTPException(403, "الاشتراك غير مفعل، أدخل كود التفعيل من إعدادات الحساب")

def active_year(c):
    return c.execute(select(academic_years.c.id).where(academic_years.c.active == 1)).scalar_one_or_none()

def clean_title(value):
    result = value.strip()
    if not result:
        raise HTTPException(400, "الاسم لا يمكن أن يكون فارغاً")
    return result

def subject_access(c, subject_id, user):
    row = c.execute(select(subjects).where(subjects.c.id == subject_id)).mappings().first()
    if not row or (user["role"] == "student" and (row["grade"] != user["grade"] or row["academic_year_id"] != active_year(c))):
        raise HTTPException(404, "المادة غير موجودة")
    return row

def lesson_access(c, lesson_id, user):
    row = c.execute(select(lessons, subjects.c.grade, subjects.c.academic_year_id)
        .join(subjects, lessons.c.subject_id == subjects.c.id)
        .where(lessons.c.id == lesson_id)).mappings().first()
    if not row or (user["role"] == "student" and (row["grade"] != user["grade"] or row["academic_year_id"] != active_year(c))):
        raise HTTPException(404, "الدرس غير موجود")
    return row

@app.get("/api/health")
def health():
    return {"ok": True, "mode": "development", "storage": "postgres" if IS_POSTGRES else "sqlite_preview",
            "durable_storage": IS_POSTGRES, "email_verification_available": bool(email_service_configured()),
            "registration_requires_email_verification": False}

@app.get("/api/grades")
def grades():
    return [{"id": k, "name": v} for k, v in GRADE_NAMES.items()]

@app.post("/api/register")
def register(data: Registration):
    """Register a student using a Syrian phone number, without SMS or email OTP."""
    phone = validate_registration(data)
    salt = secrets.token_hex(16)
    try:
        with engine.begin() as c:
            # Phone is the student login identifier; admin usernames stay unchanged.
            if c.execute(select(users.c.id).where(users.c.username == phone)).first():
                raise HTTPException(409, "رقم الهاتف مسجل مسبقاً")
            inserted = c.execute(insert(users).values(
                username=phone, email=None, salt=salt,
                password_hash=password_hash(data.password, salt),
                role="student", grade=data.grade, created_at=utcnow()))
            c.execute(insert(user_profiles).values(
                user_id=inserted.inserted_primary_key[0],
                full_name=data.full_name.strip(),
                governorate=data.governorate, birth_year=data.birth_year,
                gender=data.gender, school=(data.school or "").strip()[:180]))
    except IntegrityError:
        raise HTTPException(409, "رقم الهاتف مسجل مسبقاً") from None
    return {"ok": True, "message": "تم إنشاء حسابك بنجاح؛ يمكنك تسجيل الدخول برقم هاتفك",
            "requires_email_verification": False, "requires_phone_verification": False}


@app.post("/api/register/resend")
def resend_registration(data: ResendRegistration):
    raise HTTPException(410, "لم يعد التسجيل يحتاج إلى رمز تحقق بالبريد الإلكتروني")


@app.post("/api/register/verify")
def verify_registration(data: VerifyRegistration):
    raise HTTPException(410, "لم يعد التسجيل يحتاج إلى رمز تحقق بالبريد الإلكتروني")


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
    with engine.connect() as c:
        profile = c.execute(select(user_profiles).where(user_profiles.c.user_id == u["id"])).mappings().first()
    return {"id": u["id"], "username": u["username"], "role": u["role"],
            "grade": u["grade"], "email": u["email"],
            "phone": u["username"] if u["role"] == "student" and u["username"].startswith("+963") else None,
            "profile": dict(profile) if profile else None}

@app.get("/api/subjects")
def list_subjects(grade: Grade | None = Query(default=None),
                  authorization: str | None = Header(default=None)):
    user = auth(authorization)
    if user["role"] == "student":
        require_student_subscription(user)
    else:
        require_permission(user, "curricula")
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
    user = auth(authorization, "admin")
    require_permission(user, "reports")
    with engine.connect() as c:
        year = active_year(c)
        student_count = c.execute(select(func.count()).select_from(users).where(users.c.role == "student")).scalar_one()
        subject_count = c.execute(select(func.count()).select_from(subjects).where(subjects.c.academic_year_id == year)).scalar_one() if year else 0
        lesson_count = c.execute(select(func.count()).select_from(lessons).join(subjects, lessons.c.subject_id == subjects.c.id).where(subjects.c.academic_year_id == year)).scalar_one() if year else 0
        completed_count = c.execute(select(func.count()).select_from(progress).join(lessons, progress.c.lesson_id == lessons.c.id).join(subjects, lessons.c.subject_id == subjects.c.id).where(subjects.c.academic_year_id == year)).scalar_one() if year else 0
    return {"students": student_count, "subjects": subject_count, "lessons": lesson_count, "completed_lessons": completed_count}

@app.get("/api/admin/students")
def admin_students(authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    require_permission(user, "students")
    with engine.connect() as c:
        rows = c.execute(select(users.c.id, users.c.username, users.c.grade, users.c.created_at)
            .where(users.c.role == "student").order_by(users.c.id.desc()).limit(200)).mappings()
        return [{"id": row["id"], "username": row["username"], "grade": row["grade"],
                 "created_at": row["created_at"].isoformat()} for row in rows]


class GradePriceInput(BaseModel):
    amount: int = Field(ge=0, le=1000000000)
    currency: Literal["SYP", "TRY", "USD"] = "SYP"

@app.get("/api/prices")
def list_grade_prices(authorization: str | None = Header(default=None)):
    auth(authorization)
    with engine.connect() as c:
        rows = c.execute(select(grade_prices)).mappings()
        return {x["grade"]: {"amount": x["amount"], "currency": x["currency"]} for x in rows}

@app.put("/api/admin/prices/{grade}")
def set_grade_price(grade: Grade, data: GradePriceInput, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    if not is_main_admin(user):
        raise HTTPException(403, "تحديد الأسعار للمسؤول الرئيسي فقط")
    with engine.begin() as c:
        existing = c.execute(select(grade_prices.c.grade).where(grade_prices.c.grade == grade)).first()
        if existing:
            c.execute(update(grade_prices).where(grade_prices.c.grade == grade).values(
                amount=data.amount, currency=data.currency, updated_at=utcnow()))
        else:
            c.execute(insert(grade_prices).values(grade=grade, amount=data.amount,
                currency=data.currency, updated_at=utcnow()))
    return {"ok": True}

class ActivationRequestInput(BaseModel):
    payment_reference: str = Field(default="واتساب - إشعار حوالة شام كاش", max_length=150)

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

class PaymentSettingsInput(BaseModel):
    sham_cash_number: str = Field(max_length=100)
    qr_data_url: str | None = Field(default=None, max_length=1500000)

@app.get("/api/payment-info")
def student_payment_info(authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    with engine.connect() as c:
        row = c.execute(select(payment_settings).where(payment_settings.c.id == 1)).mappings().first()
        price = c.execute(select(grade_prices).where(grade_prices.c.grade == user["grade"])).mappings().first()
    return {"sham_cash_number": row["sham_cash_number"] if row else "",
            "qr_data_url": row["qr_data_url"] if row else None,
            "whatsapp_number": "963939791604",
            "price": {"amount": price["amount"], "currency": price["currency"]} if price else None}

@app.get("/api/admin/payment-settings")
def admin_payment_info(authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    require_permission(user, "settings")
    with engine.connect() as c:
        row = c.execute(select(payment_settings).where(payment_settings.c.id == 1)).mappings().first()
        history = c.execute(select(payment_settings_history).order_by(
            payment_settings_history.c.id.desc()).limit(20)).mappings().all()
    return {"sham_cash_number": row["sham_cash_number"] if row else "",
            "qr_data_url": row["qr_data_url"] if row else None,
            "history": [{"old_number": h["old_number"], "new_number": h["new_number"],
                "qr_changed": bool(h["qr_changed"]), "created_at": h["created_at"].isoformat()}
                for h in history]}

@app.put("/api/admin/payment-settings")
def update_payment_info(data: PaymentSettingsInput, authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    if not is_main_admin(user):
        raise HTTPException(403, "إعدادات الدفع للمسؤول الرئيسي فقط")
    number = data.sham_cash_number.strip()
    if data.qr_data_url and not re.fullmatch(r"data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+", data.qr_data_url):
        raise HTTPException(400, "صيغة صورة QR غير صالحة")
    if data.qr_data_url and len(base64.b64decode(data.qr_data_url.split(",")[1])) > 1000000:
        raise HTTPException(413, "حجم صورة QR أكبر من المسموح")
    with engine.begin() as c:
        old = c.execute(select(payment_settings).where(payment_settings.c.id == 1)).mappings().first()
        old_number = old["sham_cash_number"] if old else ""
        old_qr = old["qr_data_url"] if old else None
        if old:
            c.execute(update(payment_settings).where(payment_settings.c.id == 1).values(
                sham_cash_number=number, qr_data_url=data.qr_data_url, updated_at=utcnow()))
        else:
            c.execute(insert(payment_settings).values(id=1, sham_cash_number=number,
                qr_data_url=data.qr_data_url, whatsapp_number="963939791604", updated_at=utcnow()))
        c.execute(insert(payment_settings_history).values(admin_id=user["id"],
            old_number=old_number, new_number=number,
            qr_changed=int(old_qr != data.qr_data_url), created_at=utcnow()))
    return {"ok": True}

def lock_student_writes(connection, user_id):
    # Acquire a row lock on PostgreSQL and the write lock on SQLite.
    connection.execute(update(users).where(users.c.id == user_id).values(role=users.c.role))


@app.post("/api/activation/request")
def request_activation(data: ActivationRequestInput, authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    with engine.begin() as c:
        lock_student_writes(c, user["id"])
        year = active_year(c)
        if not year:
            raise HTTPException(409, "لا توجد سنة دراسية نشطة")
        existing = c.execute(select(activation_requests.c.id, activation_requests.c.status).where(and_(
            activation_requests.c.user_id == user["id"], activation_requests.c.academic_year_id == year,
            activation_requests.c.status.in_(["pending", "approved"])))).first()
        if existing:
            return {"ok": True, "request_id": existing[0], "already_pending": True, "status": existing.status}
        subscribed = c.execute(select(subscriptions.c.id).where(and_(
            subscriptions.c.user_id == user["id"], subscriptions.c.academic_year_id == year,
            subscriptions.c.active == 1))).first()
        if subscribed:
            raise HTTPException(409, "اشتراكك مفعّل بالفعل")
        price = c.execute(select(grade_prices).where(grade_prices.c.grade == user["grade"])).mappings().first()
        if not price or price["amount"] <= 0:
            raise HTTPException(409, "لم تحدد الإدارة سعر الاشتراك لهذا الصف بعد")
        row = c.execute(insert(activation_requests).values(user_id=user["id"],
            academic_year_id=year, status="pending", payment_reference=data.payment_reference.strip(),
            created_at=utcnow()))
        request_id = row.inserted_primary_key[0]
        if price:
            c.execute(insert(activation_request_prices).values(request_id=request_id,
                amount=price["amount"], currency=price["currency"]))
        return {"ok": True, "request_id": request_id, "status": "pending",
                "price": {"amount": price["amount"], "currency": price["currency"]} if price else None}

@app.get("/api/activation/status")
def activation_status(authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    with engine.connect() as c:
        year = active_year(c)
        active = c.execute(select(subscriptions.c.id).where(and_(
            subscriptions.c.user_id == user["id"], subscriptions.c.academic_year_id == year,
            subscriptions.c.active == 1))).first() if year else None
        request_query = (select(activation_requests.c.id, activation_requests.c.status,
            activation_requests.c.reason, activation_requests.c.created_at,
            activation_request_prices.c.amount, activation_request_prices.c.currency)
            .outerjoin(activation_request_prices,
                activation_request_prices.c.request_id == activation_requests.c.id)
            .where(activation_requests.c.user_id == user["id"]).order_by(
            activation_requests.c.id.desc()))
        requests = c.execute(request_query.limit(20)).mappings()
        current = c.execute(request_query.where(activation_requests.c.academic_year_id == year)
            .limit(1)).mappings().first() if year else None
        return {"active": bool(active), "current_request_id": current["id"] if current else None,
            "current_request_status": current["status"] if current else None, "requests": [{"id": x["id"], "status": x["status"],
            "reason": x["reason"], "created_at": x["created_at"].isoformat(),
            "price": {"amount": x["amount"], "currency": x["currency"]}
                if x["amount"] is not None else None} for x in requests]}

@app.get("/api/admin/activations")
def admin_activations(authorization: str | None = Header(default=None)):
    user = auth(authorization, "admin")
    require_permission(user, "activations")
    with engine.connect() as c:
        rows = c.execute(select(activation_requests, users.c.username, users.c.grade,
            user_profiles.c.full_name).join(
            users, users.c.id == activation_requests.c.user_id).outerjoin(
            user_profiles, user_profiles.c.user_id == users.c.id).order_by(
            activation_requests.c.id.desc()).limit(200)).mappings()
        result = []
        for x in rows:
            price = c.execute(select(activation_request_prices).where(
                activation_request_prices.c.request_id == x["id"])).mappings().first()
            result.append({"id": x["id"], "username": x["username"],
                "full_name": x["full_name"], "grade": x["grade"],
                "status": x["status"], "payment_reference": x["payment_reference"],
                "reason": x["reason"], "created_at": x["created_at"].isoformat(),
                "price": {"amount": price["amount"], "currency": price["currency"]} if price else None})
        return result

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
    require_student_subscription(user)
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
    require_student_subscription(user)
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
        per_student = []
        for student in c.execute(select(users.c.id, users.c.username, users.c.grade).where(
                users.c.role == "student")).mappings():
            completed = c.execute(select(func.count()).select_from(progress).join(
                lessons, progress.c.lesson_id == lessons.c.id).join(
                subjects, lessons.c.subject_id == subjects.c.id).where(and_(
                progress.c.user_id == student["id"], subjects.c.academic_year_id == year))).scalar_one()
            seconds = c.execute(select(func.coalesce(func.sum(study_sessions.c.duration_seconds), 0)).where(
                study_sessions.c.user_id == student["id"])).scalar_one()
            per_student.append({"username": student["username"], "grade": student["grade"],
                "completed_lessons": completed, "study_seconds": int(seconds)})
        counts["students_report"] = per_student
        c.execute(insert(year_archives).values(academic_year_id=year,
            snapshot=json.dumps(counts, ensure_ascii=False), archived_at=utcnow()))
        c.execute(update(academic_years).where(academic_years.c.id == year).values(active=0))
        c.execute(update(subscriptions).where(subscriptions.c.academic_year_id == year).values(active=0))
    return {"ok": True, "snapshot": counts}

class NewAcademicYearInput(BaseModel):
    label: str = Field(min_length=4, max_length=50)
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    exam_end_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

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

def utc_iso(value):
    # SQLite returns naive timestamps even for timezone-aware columns.
    return (value.astimezone(timezone.utc) if value.tzinfo
            else value.replace(tzinfo=timezone.utc)).isoformat()


@app.get("/api/plan/today")
def today_plan(hours: float = Query(default=3, ge=0.5, le=16),
               authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    require_student_subscription(user)
    max_tasks = max(2, min(30, int(hours * 2)))
    with engine.connect() as c:
        year = active_year(c)
        if not year:
            return {"new_lessons": [], "reviews": [], "hours": hours}
        review_rows = c.execute(select(reviews.c.lesson_id, reviews.c.stage,
            reviews.c.due_at, lessons.c.title.label("title"), subjects.c.title.label("subject"))
            .join(lessons, lessons.c.id == reviews.c.lesson_id)
            .join(subjects, subjects.c.id == lessons.c.subject_id)
            .where(and_(reviews.c.user_id == user["id"], reviews.c.completed_at.is_(None),
                reviews.c.due_at <= utcnow(), subjects.c.grade == user["grade"],
                subjects.c.academic_year_id == year))
            .order_by(reviews.c.due_at, reviews.c.stage, reviews.c.lesson_id)).mappings().all()
        lesson_rows = c.execute(select(lessons.c.id, lessons.c.title,
            subjects.c.title.label("subject")).join(subjects, subjects.c.id == lessons.c.subject_id)
            .where(and_(subjects.c.grade == user["grade"], subjects.c.academic_year_id == year,
                ~lessons.c.id.in_(select(progress.c.lesson_id).where(progress.c.user_id == user["id"]))))
            .order_by(subjects.c.position, lessons.c.position, lessons.c.id)).mappings().all()
    def diverse(rows, limit):
        # Keep each subject's order and give other subjects a turn when possible.
        chosen, remaining = [], list(rows)
        while remaining and len(chosen) < limit:
            seen, deferred = set(), []
            for item in remaining:
                if item["subject"] in seen:
                    deferred.append(item)
                    continue
                chosen.append(dict(item))
                seen.add(item["subject"])
                if len(chosen) == limit:
                    break
            remaining = deferred
        return chosen

    # Start with a balanced split and use any spare slots for the other task type.
    new_limit = min(len(lesson_rows), max_tasks // 2)
    review_limit = min(len(review_rows), max_tasks - new_limit)
    new_limit = min(len(lesson_rows), max_tasks - review_limit)
    return {"hours": hours, "new_lessons": diverse(lesson_rows, new_limit),
            "reviews": [{**item, "due_at": utc_iso(item["due_at"])}
                        for item in diverse(review_rows, review_limit)]}

@app.post("/api/lessons/{lesson_id}/complete")
def complete_lesson(lesson_id: int, authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    require_student_subscription(user)
    now = utcnow()
    with engine.begin() as c:
        lock_student_writes(c, user["id"])
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
    require_student_subscription(user)
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
    require_student_subscription(user)
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
                    r[field] = utc_iso(r[field])
            result.append(r)
    return result

@app.post("/api/reviews/{lesson_id}/{stage}/complete")
def complete_review(lesson_id: int, stage: int, data: ReviewRating,
                    authorization: str | None = Header(default=None)):
    user = auth(authorization, "student")
    require_student_subscription(user)
    if stage not in (1, 2):
        raise HTTPException(400, "مرحلة مراجعة غير صالحة")
    delays = {"لم أتذكر": 3, "تذكرت بصعوبة": 5, "تذكرت معظم المعلومات": 7, "أتقنت الدرس": 10}
    if data.rating not in delays:
        raise HTTPException(400, "التقييم غير صالح")
    now = utcnow()
    with engine.begin() as c:
        lock_student_writes(c, user["id"])
        lesson = lesson_access(c, lesson_id, user)
        if lesson["academic_year_id"] != active_year(c):
            raise HTTPException(409, "هذا الدرس ليس من السنة الدراسية النشطة")
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
