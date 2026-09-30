# =====================================================================
#   𝗔𝗕𝗗𝗢𝗨𝗨 𝗩𝗜𝗣 𝗛𝗢𝗦𝗧𝗜𝗡𝗚  —  Final Version (No Telegram)
# =====================================================================
import os, sys, json, sqlite3, secrets, subprocess, signal, shutil, re
import zipfile, threading, time, logging
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template_string, request, redirect,
                   url_for, session, flash, jsonify)

# ================= CONFIG =================
SITE_NAME     = "𝗔𝗕𝗗𝗢𝗨𝗨 𝗩𝗜𝗣 𝗛𝗢𝗦𝗧𝗜𝗡𝗚"
MAX_UPLOAD_MB = 200

# 🔐 بيانات الأدمن الثابتة (كما طلبت)
ADMIN_USER = "ABDOUUU"
ADMIN_PASS = "ABDOUUU-VIP-100"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger("ABDOUUU")

BASE_DIR = Path("panel_data")
DB_PATH  = BASE_DIR / "panel.db"
UPLOADS  = BASE_DIR / "uploads"
LOGS     = BASE_DIR / "logs"
BOTS     = BASE_DIR / "bots"
for d in (UPLOADS, LOGS, BOTS):
    d.mkdir(parents=True, exist_ok=True)

running_bots = {}
_lock = threading.Lock()

# ================= DATABASE =================
def db():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.executescript("""
        -- حسابات المستخدمين اللي ينشئها الأدمن
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            days INTEGER DEFAULT 30,
            active INTEGER DEFAULT 1,
            created_at TEXT,
            expires_at TEXT
        );
        -- جلسات المستخدمين بعد تسجيل الدخول
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT,
            expires_at TEXT,
            active INTEGER DEFAULT 1
        );
        -- بوتات المستخدمين
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            entry TEXT NOT NULL,
            folder TEXT NOT NULL,
            status TEXT DEFAULT 'stopped',
            created_at TEXT
        );
        """)
init_db()

# ================= HELPERS =================
def now():  return datetime.utcnow()
def iso(dt): return dt.isoformat()
def parse(s): return datetime.fromisoformat(s) if s else None
def safe(name):
    return re.sub(r"[^A-Za-z0-9._\-]", "_", Path(name).name)[:120] or "f"

# ---------- User management (Admin only) ----------
def create_user(username, password, days):
    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            return None, "اسم المستخدم موجود مسبقاً"
        c.execute(
            "INSERT INTO users(username,password,days,created_at,expires_at) "
            "VALUES(?,?,?,?,?)",
            (username, password, days, iso(now()), iso(now()+timedelta(days=days)))
        )
    return username, "ok"

def validate_user(username, password):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE username=? AND password=?",
                        (username, password)).fetchone()
    if not row: return False, "بيانات خاطئة", None
    if not row["active"]: return False, "الحساب معطّل", None
    exp = parse(row["expires_at"])
    if exp and now() > exp: return False, "انتهت صلاحية الحساب", None
    return True, "ok", row

def create_user_session(user_row):
    tok = secrets.token_urlsafe(32)
    with db() as c:
        c.execute(
            "INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
            (tok, user_row["id"], iso(now()), user_row["expires_at"])
        )
    return tok

def get_user_session(token):
    if not token: return None
    with db() as c:
        row = c.execute("SELECT * FROM sessions WHERE token=? AND active=1",
                        (token,)).fetchone()
    if not row: return None
    exp = parse(row["expires_at"])
    if exp and now() > exp: return None
    return row

def user_required(f):
    @wraps(f)
    def w(*a, **k):
        if not get_user_session(session.get("token")):
            session.pop("token", None)
            return redirect(url_for("index"))
        return f(*a, **k)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return f(*a, **k)
    return w

# ================= PROCESS MGMT =================
def start_bot(user_id, bot_id):
    with db() as c:
        row = c.execute("SELECT * FROM bots WHERE id=? AND user_id=?",
                        (bot_id, user_id)).fetchone()
    if not row: return False, "البوت غير موجود"
    folder = BOTS / row["folder"]
    entry  = folder / row["entry"]
    if not entry.exists(): return False, "ملف التشغيل مفقود"
    req = folder / "requirements.txt"
    if req.exists():
        try:
            subprocess.run([sys.executable, "-m", "pip", "install",
                            "--no-cache-dir", "-r", str(req)],
                           capture_output=True, timeout=900)
        except Exception: pass
    lf = open(LOGS / f"bot_{bot_id}.log", "a", encoding="utf-8")
    lf.write(f"\n\n=== {now()} | تشغيل {row['name']} ===\n")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.Popen(
            [sys.executable, str(entry.resolve())],
            cwd=str(folder), stdout=lf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env, text=True,
            start_new_session=(os.name != "nt")
        )
    except Exception as e:
        return False, f"خطأ: {e}"
    with _lock: running_bots[bot_id] = proc
    with db() as c: c.execute("UPDATE bots SET status='running' WHERE id=?", (bot_id,))
    return True, "تم التشغيل"

def stop_bot(user_id, bot_id):
    with _lock: proc = running_bots.get(bot_id)
    if proc and proc.poll() is None:
        try:
            if os.name == "nt": proc.terminate()
            else: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception: pass
    with _lock: running_bots.pop(bot_id, None)
    with db() as c: c.execute("UPDATE bots SET status='stopped' WHERE id=?", (bot_id,))
    return True, "تم الإيقاف"

def read_log(bot_id, lines=250):
    p = LOGS / f"bot_{bot_id}.log"
    if not p.exists(): return "— لا توجد سجلات —"
    d = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(d[-lines:])

# =====================================================================
#                       FLASK WEB APP
# =====================================================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# ---------------- BASE TEMPLATE (تصميم قوي) ----------------
BASE_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>""" + SITE_NAME + """</title>
<style>
:root{
  --bg:#000; --card:#0b120e; --green:#00ff88; --green2:#00b866;
  --border:#153b28; --text:#fff; --muted:#8fb0a0; --danger:#ff4466;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:'Segoe UI',Tahoma,sans-serif;
  background:radial-gradient(circle at 50% 0%,#04160d 0%,#000 70%);
  color:var(--text);min-height:100vh;display:flex;flex-direction:column;
  overflow-x:hidden;
}
body::before{
  content:"";position:fixed;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,#00ff88,transparent);
  animation:scan 4s linear infinite;z-index:100;opacity:.6;
}
@keyframes scan{0%{transform:translateY(0)}100%{transform:translateY(100vh)}}
a{color:var(--green);text-decoration:none;transition:.2s}
a:hover{opacity:.8}
.nav{
  background:rgba(0,0,0,.92);border-bottom:1px solid var(--border);
  padding:14px 22px;display:flex;justify-content:space-between;
  align-items:center;position:sticky;top:0;z-index:60;
  backdrop-filter:blur(14px);
}
.logo{
  font-size:19px;font-weight:800;color:var(--green);letter-spacing:1px;
  text-shadow:0 0 14px rgba(0,255,136,.6),0 0 30px rgba(0,255,136,.3);
}
.nav-links{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.nav-links a{color:var(--muted);font-size:13px;font-weight:600}
.nav-links a:hover{color:var(--green)}
.container{flex:1;max-width:1100px;width:100%;margin:0 auto;padding:26px 18px}
.card{
  background:linear-gradient(145deg,#0b120e,#040806);
  border:1px solid var(--border);border-radius:16px;padding:24px;
  margin-bottom:20px;
  box-shadow:0 0 40px rgba(0,255,136,.06),inset 0 1px 0 rgba(0,255,136,.08);
}
h1,h2,h3{color:var(--green);margin-bottom:14px;letter-spacing:.3px}
h1{font-size:26px;text-shadow:0 0 20px rgba(0,255,136,.3)}
h2{font-size:20px}
p{color:var(--muted);line-height:1.75}
.btn{
  display:inline-block;background:linear-gradient(135deg,#00ff88,#00a85a);
  color:#000!important;font-weight:800;padding:11px 24px;border:none;
  border-radius:10px;cursor:pointer;font-size:14px;transition:.25s;
  text-align:center;box-shadow:0 0 18px rgba(0,255,136,.3);
  letter-spacing:.5px;
}
.btn:hover{transform:translateY(-2px);box-shadow:0 10px 28px rgba(0,255,136,.55)}
.btn:active{transform:translateY(0)}
.btn.danger{background:linear-gradient(135deg,#ff4466,#aa0022);color:#fff!important;box-shadow:0 0 18px rgba(255,68,102,.3)}
.btn.gray{background:#122019;color:#fff!important;border:1px solid var(--border);box-shadow:none}
.btn.gray:hover{border-color:var(--green)}
.btn.small{padding:7px 14px;font-size:12px;border-radius:7px}
input,select{
  width:100%;padding:13px 15px;margin:6px 0 16px;background:#050b07;
  border:1px solid var(--border);color:#fff;border-radius:10px;
  font-size:14px;font-family:inherit;letter-spacing:.5px;transition:.2s;
}
input:focus,select:focus{
  outline:none;border-color:var(--green);
  box-shadow:0 0 0 3px rgba(0,255,136,.15);
}
label{color:var(--green);font-size:13px;font-weight:700;letter-spacing:.5px}
.bot-card{
  background:#070d09;border:1px solid var(--border);border-radius:12px;
  padding:16px;margin-bottom:12px;display:flex;justify-content:space-between;
  align-items:center;flex-wrap:wrap;gap:12px;transition:.2s;
}
.bot-card:hover{border-color:rgba(0,255,136,.4)}
.bot-info{flex:1;min-width:200px}
.bot-name{font-size:15px;font-weight:800;color:#fff;margin-bottom:6px}
.bot-meta{font-size:12px;color:var(--muted);display:flex;gap:12px;flex-wrap:wrap}
.status{display:inline-block;padding:3px 11px;border-radius:20px;font-size:11px;font-weight:800}
.status.running{background:rgba(0,255,136,.15);color:var(--green);border:1px solid rgba(0,255,136,.35)}
.status.stopped{background:rgba(255,68,102,.15);color:#ff6688;border:1px solid rgba(255,68,102,.35)}
.status.admin{background:rgba(255,215,0,.15);color:#ffd700;border:1px solid rgba(255,215,0,.4)}
.actions{display:flex;gap:8px;flex-wrap:wrap}
.log-box{
  background:#000;border:1px solid var(--border);border-radius:10px;
  padding:14px;font-family:ui-monospace,monospace;font-size:12px;
  color:#00ff88;max-height:420px;overflow:auto;white-space:pre-wrap;
  word-break:break-all;line-height:1.6;
}
.alert{padding:13px 16px;border-radius:10px;margin-bottom:16px;font-size:13px;font-weight:600;animation:slide .3s}
@keyframes slide{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:none}}
.alert.ok{background:rgba(0,255,136,.12);border:1px solid var(--green);color:var(--green)}
.alert.err{background:rgba(255,68,102,.12);border:1px solid #ff4466;color:#ff8093}
table{width:100%;border-collapse:collapse;font-size:13px}
table th{
  padding:12px;text-align:right;color:var(--green);font-weight:800;
  border-bottom:1px solid var(--border);font-size:12px;
}
table td{padding:12px;border-bottom:1px solid #0d1a12;color:#e8f0ea}
table tr:hover td{background:rgba(0,255,136,.03)}
.code-badge{
  display:inline-block;background:#000;border:1px dashed var(--green);
  border-radius:6px;padding:4px 10px;color:var(--green);
  font-family:monospace;font-size:12px;letter-spacing:.5px;
}
footer{
  background:#000;border-top:1px solid var(--border);padding:28px 18px;
  text-align:center;color:var(--muted);font-size:13px;line-height:1.9;
}
footer .brand{
  color:var(--green);font-weight:800;font-size:17px;letter-spacing:1px;
  text-shadow:0 0 14px rgba(0,255,136,.5);margin-bottom:10px;
}
footer .features{
  display:flex;justify-content:center;flex-wrap:wrap;gap:16px;
  margin-top:16px;font-size:12px;
}
footer .features span{
  padding:5px 13px;background:rgba(0,255,136,.06);
  border:1px solid var(--border);border-radius:20px;color:var(--green);
}
.hero{
  text-align:center;padding:40px 20px;
  background:linear-gradient(135deg,rgba(0,255,136,.05),transparent);
  border:1px solid var(--border);border-radius:20px;margin-bottom:22px;
}
.hero h1{font-size:34px;margin-bottom:10px;letter-spacing:2px}
.hero p{font-size:15px;color:var(--muted);max-width:560px;margin:0 auto;line-height:1.8}
.hero .badge{
  display:inline-block;margin-top:14px;padding:6px 16px;
  background:rgba(0,255,136,.1);border:1px solid var(--green);
  border-radius:20px;color:var(--green);font-size:12px;font-weight:700;
}
@media(max-width:640px){
  .nav{flex-direction:column;gap:10px;padding:12px}
  .hero h1{font-size:24px}
  h1{font-size:22px}
}
</style>
</head>
<body>
<nav class="nav">
  <div class="logo">⚡ """ + SITE_NAME + """</div>
  <div class="nav-links">
    {% if session.is_admin %}
      <a href="{{ url_for('admin_panel') }}">👑 لوحة الأدمن</a>
      <a href="{{ url_for('admin_logout') }}">🚪 خروج</a>
    {% elif session.token %}
      <a href="{{ url_for('dashboard') }}">🏠 الرئيسية</a>
      <a href="{{ url_for('upload') }}">📤 رفع بوت</a>
      <a href="{{ url_for('logout') }}">🚪 خروج</a>
    {% else %}
      <a href="{{ url_for('index') }}">🔐 دخول</a>
    {% endif %}
  </div>
</nav>
<div class="container">
  {% with msgs = get_flashed_messages(with_categories=true) %}
    {% for cat,msg in msgs %}
      <div class="alert {{ 'ok' if cat=='ok' else 'err' }}">{{ msg }}</div>
    {% endfor %}
  {% endwith %}
  {% block content %}{% endblock %}
</div>
<footer>
  <div class="brand">⚡ """ + SITE_NAME + """ ⚡</div>
  <div>منصة استضافة بوتات بايثون — ترفع ملفك، تشغّله، وتتابع السجلات لحظياً.</div>
  <div class="features">
    <span>🔐 دخول آمن</span>
    <span>📤 رفع .py / .zip</span>
    <span>⚙️ تثبيت تلقائي للتبعيات</span>
    <span>📊 سجلات حية</span>
    <span>🚀 تشغيل 24/7</span>
    <span>🛡️ عزل كامل</span>
  </div>
  <div style="margin-top:16px;font-size:11px;color:#5a6a5f">
    © 2025 """ + SITE_NAME + """ — All systems operational.
  </div>
</footer>
</body>
</html>
"""

# ---------------- LOGIN (User) ----------------
LOGIN_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="hero">
  <h1>⚡ """ + SITE_NAME + """ ⚡</h1>
  <p>منصة استضافة بوتات بايثون — ارفع ملفك، شغّله، وتابع السجلات لحظياً.</p>
  <div class="badge">🔐 بوابة الدخول الآمن</div>
</div>

<div class="card" style="max-width:440px;margin:0 auto">
  <h2 style="text-align:center">🔐 تسجيل الدخول</h2>
  <form method="post" style="margin-top:16px">
    <label>اسم المستخدم</label>
    <input name="username" required autofocus autocomplete="off">
    <label>كلمة المرور</label>
    <input name="password" type="password" required autocomplete="off">
    <button class="btn" style="width:100%">🔓 دخول</button>
  </form>
  <p style="margin-top:16px;font-size:12px;text-align:center">
    للحصول على حساب، تواصل مع المسؤول.
  </p>
</div>
{% endblock %}
""")

# ---------------- DASHBOARD (User) ----------------
DASH_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="hero">
  <h1>🏠 مرحباً {{ username }}</h1>
  <p>لوحة تحكم بوتاتك — أدر، شغّل، وتابع كل شيء من هنا.</p>
  <div class="badge">⏳ الصلاحية حتى {{ expires_at[:10] }}</div>
</div>

<div class="card">
  <h2>🤖 بوتاتك ({{ bots|length }})</h2>
  {% if bots %}
    {% for b in bots %}
      <div class="bot-card">
        <div class="bot-info">
          <div class="bot-name">🤖 {{ b['name'] }}</div>
          <div class="bot-meta">
            <span>📄 {{ b['entry'] }}</span>
            <span>🕒 {{ b['created_at'][:19] }}</span>
            <span class="status {{ b['status'] }}">{{ 'شغّال' if b['status']=='running' else 'متوقف' }}</span>
          </div>
        </div>
        <div class="actions">
          {% if b['status']=='running' %}
            <a class="btn danger small" href="{{ url_for('stop', bid=b['id']) }}">⏹ إيقاف</a>
            <a class="btn gray small" href="{{ url_for('restart', bid=b['id']) }}">🔄 إعادة</a>
          {% else %}
            <a class="btn small" href="{{ url_for('start', bid=b['id']) }}">▶️ تشغيل</a>
          {% endif %}
          <a class="btn gray small" href="{{ url_for('logs', bid=b['id']) }}">📜 سجلات</a>
          <a class="btn danger small" href="{{ url_for('delete_bot', bid=b['id']) }}" onclick="return confirm('متأكد؟')">🗑</a>
        </div>
      </div>
    {% endfor %}
  {% else %}
    <p>لا يوجد بوتات بعد. <a href="{{ url_for('upload') }}">ارفع أول بوت</a>.</p>
  {% endif %}
</div>
{% endblock %}
""")

# ---------------- UPLOAD ----------------
UPLOAD_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="card" style="max-width:620px;margin:20px auto">
  <h1>📤 رفع بوت جديد</h1>
  <p>ارفع ملف <b>.py</b> مباشرة، أو ملف <b>.zip</b> يحتوي المشروع كامل.</p>
  <form method="post" enctype="multipart/form-data" style="margin-top:16px">
    <label>اسم البوت</label>
    <input name="name" placeholder="My Bot" required>
    <label>الملف (.py أو .zip)</label>
    <input name="file" type="file" accept=".py,.zip" required>
    <button class="btn" style="width:100%">📤 رفع وتشغيل</button>
  </form>
</div>
{% endblock %}
""")

# ---------------- LOGS ----------------
LOGS_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="card">
  <h2>📜 سجلات: {{ bot['name'] }}</h2>
  <div class="log-box">{{ log }}</div>
  <div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap">
    <a class="btn gray" href="{{ url_for('dashboard') }}">↩ رجوع</a>
    <a class="btn" href="{{ url_for('logs', bid=bot['id']) }}">🔄 تحديث</a>
  </div>
</div>
{% endblock %}
""")

# ---------------- ADMIN LOGIN ----------------
ADMIN_LOGIN_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="hero" style="border-color:#ffd700;background:linear-gradient(135deg,rgba(255,215,0,.08),transparent)">
  <h1 style="color:#ffd700;text-shadow:0 0 20px rgba(255,215,0,.4)">👑 بوابة الأدمن</h1>
  <p>هذه البوابة مخصصة للمسؤول الرئيسي فقط.</p>
  <div class="badge" style="border-color:#ffd700;color:#ffd700;background:rgba(255,215,0,.1)">
    🛡️ منطقة محمية
  </div>
</div>

<div class="card" style="max-width:440px;margin:0 auto;border-color:rgba(255,215,0,.3)">
  <h2 style="text-align:center;color:#ffd700">🔐 دخول المسؤول</h2>
  <form method="post" style="margin-top:16px">
    <label>اسم المستخدم</label>
    <input name="username" required autofocus autocomplete="off">
    <label>كلمة المرور</label>
    <input name="password" type="password" required autocomplete="off">
    <button class="btn" style="width:100%;background:linear-gradient(135deg,#ffd700,#b8860b);color:#000">
      🔓 دخول آمن
    </button>
  </form>
  <p style="margin-top:16px;font-size:12px;text-align:center;color:#8a7a3a">
    ⚠️ محاولات الدخول مُسجَّلة.
  </p>
</div>
{% endblock %}
""")

# ---------------- ADMIN PANEL ----------------
ADMIN_PANEL_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="hero" style="border-color:#ffd700;background:linear-gradient(135deg,rgba(255,215,0,.08),transparent)">
  <h1 style="color:#ffd700;text-shadow:0 0 20px rgba(255,215,0,.4)">👑 لوحة تحكم المسؤول</h1>
  <p>مرحباً <b style="color:#fff">ABDOUUU</b> — تحكم كامل بالنظام.</p>
  <div class="badge" style="border-color:#ffd700;color:#ffd700;background:rgba(255,215,0,.1)">
    🛡️ صلاحيات كاملة
  </div>
</div>

<div class="card">
  <h2>➕ إضافة مستخدم جديد</h2>
  <p style="margin-bottom:14px">أنشئ حساباً جديداً للمستخدم مع تحديد مدة الصلاحية.</p>
  <form method="post" action="{{ url_for('admin_create_user') }}"
        style="display:flex;gap:10px;flex-wrap:wrap;align-items:end">
    <div style="flex:1;min-width:160px">
      <label>اسم المستخدم</label>
      <input name="username" required placeholder="مثال: ahmed" autocomplete="off">
    </div>
    <div style="flex:1;min-width:160px">
      <label>كلمة المرور</label>
      <input name="password" required placeholder="كلمة سر قوية" autocomplete="off">
    </div>
    <div style="flex:1;min-width:110px">
      <label>عدد الأيام</label>
      <input name="days" type="number" value="30" min="1" max="3650">
    </div>
    <button class="btn" style="margin-bottom:16px">➕ إنشاء</button>
  </form>

  {% if new_user %}
    <div style="background:#000;border:1px dashed #00ff88;border-radius:10px;
                padding:14px;margin-top:10px">
      <div style="color:#8fb0a0;font-size:12px;margin-bottom:6px">✅ تم إنشاء الحساب:</div>
      <div style="color:#00ff88;font-size:14px;font-family:monospace">
        👤 {{ new_user.username }} &nbsp;|&nbsp; 🔑 {{ new_user.password }}
      </div>
    </div>
  {% endif %}
</div>

<div class="card">
  <h2>👥 المستخدمون ({{ users|length }})</h2>
  {% if users %}
    <div style="overflow-x:auto">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>اسم المستخدم</th>
          <th>كلمة المرور</th>
          <th>الأيام</th>
          <th>الحالة</th>
          <th>ينتهي في</th>
          <th>إجراء</th>
        </tr>
      </thead>
      <tbody>
        {% for u in users %}
          <tr>
            <td>{{ u['id'] }}</td>
            <td><b style="color:#fff">{{ u['username'] }}</b></td>
            <td><span class="code-badge">{{ u['password'] }}</span></td>
            <td style="text-align:center">{{ u['days'] }}</td>
            <td style="text-align:center">
              {% if u['active'] %}
                <span style="color:#00ff88">🟢 نشط</span>
              {% else %}
                <span style="color:#ff6688">🔴 معطّل</span>
              {% endif %}
            </td>
            <td style="text-align:center;font-size:11px">{{ u['expires_at'][:10] }}</td>
            <td>
              {% if u['active'] %}
                <a class="btn danger small" href="{{ url_for('admin_disable_user', uid=u['id']) }}">🚫</a>
              {% else %}
                <a class="btn small" href="{{ url_for('admin_enable_user', uid=u['id']) }}">✅</a>
              {% endif %}
              <a class="btn danger small" href="{{ url_for('admin_delete_user', uid=u['id']) }}"
                 onclick="return confirm('حذف الحساب نهائياً؟')">🗑</a>
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
    </div>
  {% else %}
    <p>لا يوجد مستخدمون بعد.</p>
  {% endif %}
</div>

<div class="card">
  <h2>🤖 كل البوتات ({{ bots|length }})</h2>
  {% if bots %}
    <div style="overflow-x:auto">
    <table>
      <thead>
        <tr>
          <th>الاسم</th>
          <th>المستخدم</th>
          <th>الحالة</th>
        </tr>
      </thead>
      <tbody>
        {% for b in bots %}
          <tr>
            <td>🤖 {{ b['name'] }}</td>
            <td>{{ b['username'] or '—' }}</td>
            <td>
              <span class="status {{ b['status'] }}">
                {{ 'شغّال' if b['status']=='running' else 'متوقف' }}
              </span>
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
    </div>
  {% else %}
    <p>لا يوجد بوتات.</p>
  {% endif %}
</div>
{% endblock %}
""")

# =====================================================================
#                       ROUTES
# =====================================================================

# ---------- User Login ----------
@app.route("/", methods=["GET"])
def index():
    if get_user_session(session.get("token")):
        return redirect(url_for("dashboard"))
    return render_template_string(LOGIN_HTML)

@app.route("/", methods=["POST"])
def login_post():
    u = request.form.get("username", "").strip()
    p = request.form.get("password", "")
    ok, msg, row = validate_user(u, p)
    if not ok:
        flash(msg, "err")
        return redirect(url_for("index"))
    session["token"] = create_user_session(row)
    session["username"] = row["username"]
    flash(f"✅ مرحباً {row['username']}", "ok")
    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout():
    session.pop("token", None)
    session.pop("username", None)
    return redirect(url_for("index"))

# ---------- User Dashboard ----------
@app.route("/dashboard")
@user_required
def dashboard():
    s = get_user_session(session["token"])
    with db() as c:
        bots = c.execute("SELECT * FROM bots WHERE user_id=? ORDER BY id DESC",
                         (s["user_id"],)).fetchall()
        user = c.execute("SELECT * FROM users WHERE id=?",
                         (s["user_id"],)).fetchone()
    return render_template_string(
        DASH_HTML, bots=bots,
        username=user["username"],
        expires_at=user["expires_at"]
    )

# ---------- Upload ----------
@app.route("/upload", methods=["GET","POST"])
@user_required
def upload():
    s = get_user_session(session["token"])
    if request.method == "POST":
        name = safe(request.form.get("name", "bot"))
        f = request.files.get("file")
        if not f or not f.filename:
            flash("اختر ملفاً.", "err"); return redirect(url_for("upload"))
        fname = safe(f.filename)
        if not fname.lower().endswith((".py",".zip")):
            flash("فقط .py أو .zip.", "err"); return redirect(url_for("upload"))
        user_dir = BOTS / f"u{s['user_id']}_{int(time.time())}"
        user_dir.mkdir(parents=True, exist_ok=True)
        tmp = UPLOADS / fname
        f.save(tmp)
        entry = None
        if fname.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(tmp) as z:
                    root = user_dir.resolve()
                    for info in z.infolist():
                        if not str((user_dir / info.filename).resolve()).startswith(str(root)):
                            raise ValueError("مسار غير آمن")
                    z.extractall(user_dir)
            except Exception as e:
                flash(f"خطأ ZIP: {e}", "err"); return redirect(url_for("upload"))
            for cand in ("bot.py","main.py","app.py","index.py"):
                if (user_dir / cand).exists(): entry = cand; break
            if not entry:
                for p in user_dir.rglob("*.py"):
                    entry = str(p.relative_to(user_dir)); break
        else:
            shutil.copy(tmp, user_dir / fname); entry = fname
        tmp.unlink(missing_ok=True)
        if not entry:
            flash("لم أجد ملف .py.", "err"); return redirect(url_for("upload"))
        with db() as c:
            cur = c.execute(
                "INSERT INTO bots(user_id,name,entry,folder,created_at) VALUES(?,?,?,?,?)",
                (s["user_id"], name, entry, user_dir.name, iso(now()))
            )
            bid = cur.lastrowid
        ok, m = start_bot(s["user_id"], bid)
        flash(("✅ " if ok else "❌ ") + m, "ok" if ok else "err")
        return redirect(url_for("dashboard"))
    return render_template_string(UPLOAD_HTML)

# ---------- Bot controls ----------
@app.route("/start/<int:bid>")
@user_required
def start(bid):
    s = get_user_session(session["token"])
    ok, m = start_bot(s["user_id"], bid); flash(m, "ok" if ok else "err")
    return redirect(url_for("dashboard"))

@app.route("/stop/<int:bid>")
@user_required
def stop(bid):
    s = get_user_session(session["token"])
    ok, m = stop_bot(s["user_id"], bid); flash(m, "ok" if ok else "err")
    return redirect(url_for("dashboard"))

@app.route("/restart/<int:bid>")
@user_required
def restart(bid):
    s = get_user_session(session["token"])
    stop_bot(s["user_id"], bid); time.sleep(1)
    ok, m = start_bot(s["user_id"], bid)
    flash("🔄 " + m, "ok" if ok else "err")
    return redirect(url_for("dashboard"))

@app.route("/logs/<int:bid>")
@user_required
def logs(bid):
    s = get_user_session(session["token"])
    with db() as c:
        bot = c.execute("SELECT * FROM bots WHERE id=? AND user_id=?",
                        (bid, s["user_id"])).fetchone()
    if not bot: return "غير موجود", 404
    return render_template_string(LOGS_HTML, bot=bot, log=read_log(bid))

@app.route("/delete/<int:bid>")
@user_required
def delete_bot(bid):
    s = get_user_session(session["token"])
    stop_bot(s["user_id"], bid)
    with db() as c:
        row = c.execute("SELECT * FROM bots WHERE id=? AND user_id=?",
                        (bid, s["user_id"])).fetchone()
        if row: c.execute("DELETE FROM bots WHERE id=?", (bid,))
    if row: shutil.rmtree(BOTS / row["folder"], ignore_errors=True)
    flash("🗑 تم الحذف", "ok"); return redirect(url_for("dashboard"))

# =====================================================================
#                       ADMIN ROUTES
# =====================================================================

# ---------- Admin Login (بوابة سرية) ----------
@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if session.get("is_admin"):
        return redirect(url_for("admin_panel"))
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        if u == ADMIN_USER and p == ADMIN_PASS:
            session["is_admin"] = True
            log.info("👑 دخول أدمن ناجح")
            flash("👑 مرحباً بك في لوحة الأدمن", "ok")
            return redirect(url_for("admin_panel"))
        log.warning(f"⛔ محاولة دخول أدمن فاشلة | user={u} | ip={request.remote_addr}")
        flash("❌ بيانات دخول خاطئة", "err")
    return render_template_string(ADMIN_LOGIN_HTML)

@app.route("/admin-logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_login"))

# ---------- Admin Panel ----------
@app.route("/admin")
@admin_required
def admin_panel():
    with db() as c:
        users = c.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
        bots = c.execute("""
            SELECT b.*, u.username
            FROM bots b
            LEFT JOIN users u ON u.id = b.user_id
            ORDER BY b.id DESC
        """).fetchall()
    new_user = session.pop("last_new_user", None)
    return render_template_string(
        ADMIN_PANEL_HTML,
        users=users, bots=bots, new_user=new_user
    )

@app.route("/admin/create-user", methods=["POST"])
@admin_required
def admin_create_user():
    try:
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        d = int(request.form.get("days", 30))
        d = max(1, min(d, 3650))
        if not u or not p:
            raise ValueError("اسم أو كلمة مرور فارغة")
    except ValueError as e:
        flash(f"بيانات غير صالحة: {e}", "err")
        return redirect(url_for("admin_panel"))
    name, msg = create_user(u, p, d)
    if not name:
        flash(f"❌ {msg}", "err")
        return redirect(url_for("admin_panel"))
    session["last_new_user"] = {"username": u, "password": p, "days": d}
    flash(f"✅ تم إنشاء المستخدم {u}", "ok")
    return redirect(url_for("admin_panel"))

@app.route("/admin/disable-user/<int:uid>")
@admin_required
def admin_disable_user(uid):
    with db() as c:
        c.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
        c.execute("UPDATE sessions SET active=0 WHERE user_id=?", (uid,))
        # أوقف البوتات
        rows = c.execute("SELECT id FROM bots WHERE user_id=?", (uid,)).fetchall()
        for r in rows:
            stop_bot(uid, r["id"])
            c.execute("UPDATE bots SET status='stopped' WHERE id=?", (r["id"],))
    flash("🚫 تم تعطيل المستخدم وإيقاف بوتاته", "ok")
    return redirect(url_for("admin_panel"))

@app.route("/admin/enable-user/<int:uid>")
@admin_required
def admin_enable_user(uid):
    with db() as c:
        c.execute("UPDATE users SET active=1 WHERE id=?", (uid,))
    flash("✅ تم تفعيل المستخدم", "ok")
    return redirect(url_for("admin_panel"))

@app.route("/admin/delete-user/<int:uid>")
@admin_required
def admin_delete_user(uid):
    with db() as c:
        # أوقف كل البوتات
        rows = c.execute("SELECT id, folder FROM bots WHERE user_id=?", (uid,)).fetchall()
        for r in rows:
            stop_bot(uid, r["id"])
            shutil.rmtree(BOTS / r["folder"], ignore_errors=True)
        c.execute("DELETE FROM bots WHERE user_id=?", (uid,))
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    flash("🗑 تم حذف المستخدم وكل بوتاته", "ok")
    return redirect(url_for("admin_panel"))

# ---------- Health ----------
@app.route("/health")
def health(): return {"ok": True, "site": SITE_NAME}

# ================= MAIN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log.info(f"🌐 {SITE_NAME} — port {port}")
    log.info(f"👑 Admin: {ADMIN_USER}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)