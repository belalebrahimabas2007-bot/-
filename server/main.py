import os, sqlite3, hashlib, secrets
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Engaz Test API")
origins = [x.strip() for x in os.getenv("ENGAZ_ALLOWED_ORIGINS","http://localhost:5500,http://127.0.0.1:5500").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET","POST","DELETE"], allow_headers=["Authorization","Content-Type"])
DB = os.getenv("ENGAZ_DB_PATH", os.path.join(os.path.dirname(__file__),"engaz.sqlite3"))
TOKENS = {}

@contextmanager
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()

def digest(password,salt):
    return hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(salt),250000).hex()

def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, hash TEXT NOT NULL, role TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS subjects(id INTEGER PRIMARY KEY, title TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS lessons(id INTEGER PRIMARY KEY, subject_id INTEGER NOT NULL, title TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS progress(user_id INTEGER NOT NULL, lesson_id INTEGER NOT NULL, PRIMARY KEY(user_id,lesson_id));
        """)
        username = os.getenv("ENGAZ_ADMIN_USERNAME","").strip()
        password = os.getenv("ENGAZ_ADMIN_PASSWORD","")
        if username and password and len(password)>=12 and not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            salt=secrets.token_hex(16)
            c.execute("INSERT INTO users(username,salt,hash,role) VALUES(?,?,?,?)",(username,salt,digest(password,salt),"admin"))
init()

class Credentials(BaseModel):
    username: str
    password: str
class Title(BaseModel):
    title: str
class LessonInput(BaseModel):
    subject_id: int
    title: str

def auth(authorization,role=None):
    token=(authorization or "").removeprefix("Bearer ").strip()
    user=TOKENS.get(token)
    if not user or (role and user["role"]!=role):
        raise HTTPException(403,"غير مصرح")
    return user

@app.get("/api/health")
def health(): return {"ok":True,"mode":"test"}

@app.post("/api/register")
def register(data:Credentials):
    if not 3<=len(data.username)<=40 or len(data.password)<10:
        raise HTTPException(400,"اسم المستخدم أو كلمة المرور غير صالحين")
    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?",(data.username,)).fetchone():
            raise HTTPException(409,"اسم المستخدم مستخدم")
        salt=secrets.token_hex(16)
        c.execute("INSERT INTO users(username,salt,hash,role) VALUES(?,?,?,?)",(data.username,salt,digest(data.password,salt),"student"))
    return {"ok":True}

@app.post("/api/login")
def login(data:Credentials):
    with db() as c:
        u=c.execute("SELECT * FROM users WHERE username=?",(data.username,)).fetchone()
    if not u or not secrets.compare_digest(digest(data.password,u["salt"]),u["hash"]):
        raise HTTPException(401,"بيانات الدخول غير صحيحة")
    token=secrets.token_urlsafe(32)
    TOKENS[token]={"id":u["id"],"username":u["username"],"role":u["role"]}
    return {"token":token,"user":TOKENS[token]}

@app.get("/api/subjects")
def subjects(authorization:str|None=Header(default=None)):
    user=auth(authorization)
    with db() as c:
        rows=[dict(x) for x in c.execute("SELECT * FROM subjects ORDER BY id")]
        for subject in rows:
            lessons=[dict(x) for x in c.execute("SELECT * FROM lessons WHERE subject_id=? ORDER BY id",(subject["id"],))]
            for lesson in lessons:
                lesson["completed"]=bool(c.execute("SELECT 1 FROM progress WHERE user_id=? AND lesson_id=?",(user["id"],lesson["id"])).fetchone())
            subject["lessons"]=lessons
    return rows

@app.post("/api/subjects")
def add_subject(data:Title,authorization:str|None=Header(default=None)):
    auth(authorization,"admin")
    if not data.title.strip(): raise HTTPException(400,"اسم المادة مطلوب")
    with db() as c: c.execute("INSERT INTO subjects(title) VALUES(?)",(data.title.strip(),))
    return {"ok":True}

@app.post("/api/lessons")
def add_lesson(data:LessonInput,authorization:str|None=Header(default=None)):
    auth(authorization,"admin")
    if not data.title.strip(): raise HTTPException(400,"اسم الدرس مطلوب")
    with db() as c:
        if not c.execute("SELECT 1 FROM subjects WHERE id=?",(data.subject_id,)).fetchone():
            raise HTTPException(404,"المادة غير موجودة")
        c.execute("INSERT INTO lessons(subject_id,title) VALUES(?,?)",(data.subject_id,data.title.strip()))
    return {"ok":True}

@app.post("/api/lessons/{lesson_id}/complete")
def complete(lesson_id:int,authorization:str|None=Header(default=None)):
    user=auth(authorization,"student")
    with db() as c:
        if not c.execute("SELECT 1 FROM lessons WHERE id=?",(lesson_id,)).fetchone(): raise HTTPException(404,"الدرس غير موجود")
        c.execute("INSERT OR IGNORE INTO progress(user_id,lesson_id) VALUES(?,?)",(user["id"],lesson_id))
    return {"ok":True}

@app.get("/api/stats")
def stats(authorization:str|None=Header(default=None)):
    user=auth(authorization,"student")
    with db() as c:
        total=c.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]
        done=c.execute("SELECT COUNT(*) FROM progress WHERE user_id=?",(user["id"],)).fetchone()[0]
    return {"total":total,"completed":done}
