# =====================================================================
#   𝗔𝗕𝗗𝗢𝗨𝗨 𝗩𝗜𝗣 𝗛𝗢𝗦𝗧𝗜𝗡𝗚
#   Web Hosting Panel + Telegram Code Manager
# =====================================================================
import os, sys, json, sqlite3, secrets, subprocess, signal, shutil, re
import zipfile, threading, time, asyncio, logging
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template_string, request, redirect,
                   url_for, session, flash, jsonify)
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ApplicationBuilder, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes,
                          ConversationHandler, filters)

# ================= CONFIG =================
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "8309622602:AAFS84wr8SFbQuYcken9TDW_N0qLnhsCQ7k")
OWNER_ID   = int(os.environ.get("OWNER_ID", "8046711782") or "0")
CODE_PREFIX = "-ABDOUUU-VIP-TEAM"
SITE_NAME   = "𝗔𝗕𝗗𝗢𝗨𝗨 𝗩𝗜𝗣 𝗛𝗢𝗦𝗧𝗜𝗡𝗚"
MAX_UPLOAD_MB = 200

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger("ABDOUUU")

# ================= PATHS =================
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
        CREATE TABLE IF NOT EXISTS codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            max_users INTEGER DEFAULT 1,
            used_count INTEGER DEFAULT 0,
            days INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1,
            created_at TEXT,
            expires_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            code_id INTEGER,
            created_at TEXT,
            expires_at TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
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

def gen_code():
    rand = secrets.token_hex(4).upper()
    return f"{CODE_PREFIX}-{rand}"

def create_code(max_users, days):
    code = gen_code()
    while True:
        with db() as c:
            if not c.execute("SELECT 1 FROM codes WHERE code=?", (code,)).fetchone():
                break
        code = gen_code()
    with db() as c:
        c.execute(
            "INSERT INTO codes(code,max_users,days,created_at,expires_at) "
            "VALUES(?,?,?,?,?)",
            (code, max_users, days, iso(now()), iso(now()+timedelta(days=days)))
        )
    return code

def validate_code(code):
    """Return (ok, message, code_row)."""
    code = (code or "").strip()
    if not code.startswith(CODE_PREFIX):
        return False, "الكود غير صالح", None
    with db() as c:
        row = c.execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
    if not row:
        return False, "الكود غير موجود", None
    if not row["active"]:
        return False, "تم تعطيل هذا الكود", None
    exp = parse(row["expires_at"])
    if exp and now() > exp:
        return False, "انتهت صلاحية الكود", None
    if row["used_count"] >= row["max_users"]:
        return False, "تم استهلاك كل مستخدمي هذا الكود", None
    return True, "ok", row

def consume_code(code_row):
    with db() as c:
        c.execute("UPDATE codes SET used_count=used_count+1 WHERE id=?",
                  (code_row["id"],))
        tok = secrets.token_urlsafe(32)
        c.execute(
            "INSERT INTO sessions(token,code_id,created_at,expires_at) "
            "VALUES(?,?,?,?)",
            (tok, code_row["id"], iso(now()), code_row["expires_at"])
        )
    return tok

def get_session(token):
    if not token: return None
    with db() as c:
        row = c.execute("SELECT * FROM sessions WHERE token=? AND active=1",
                        (token,)).fetchone()
    if not row: return None
    exp = parse(row["expires_at"])
    if exp and now() > exp: return None
    return row

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not get_session(session.get("token")):
            session.clear()
            return redirect(url_for("index"))
        return f(*a, **k)
    return w

# ================= PROCESS MGMT =================
def start_bot(session_id, bot_id):
    with db() as c:
        row = c.execute("SELECT * FROM bots WHERE id=? AND session_id=?",
                        (bot_id, session_id)).fetchone()
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

    with _lock:
        running_bots[bot_id] = proc
    with db() as c:
        c.execute("UPDATE bots SET status='running' WHERE id=?", (bot_id,))
    return True, "تم التشغيل"

def stop_bot(session_id, bot_id):
    with _lock:
        proc = running_bots.get(bot_id)
    if proc and proc.poll() is None:
        try:
            if os.name == "nt": proc.terminate()
            else: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception: pass
    with _lock:
        running_bots.pop(bot_id, None)
    with db() as c:
        c.execute("UPDATE bots SET status='stopped' WHERE id=?", (bot_id,))
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

BASE_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}""" + SITE_NAME + """{% endblock %}</title>
<style>
:root{
  --bg:#000;--bg2:#07100b;--card:#0b120e;
  --green:#00ff88;--green2:#00b866;
  --border:#153b28;--text:#fff;--muted:#8fb0a0;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:'Segoe UI',Tahoma,sans-serif;
  background:radial-gradient(circle at 50% 0%,#04160d 0%,#000 70%);
  color:var(--text);min-height:100vh;display:flex;flex-direction:column;
}
a{color:var(--green);text-decoration:none}
.nav{
  background:rgba(0,0,0,.9);border-bottom:1px solid var(--border);
  padding:14px 22px;display:flex;justify-content:space-between;
  align-items:center;backdrop-filter:blur(12px);
  position:sticky;top:0;z-index:60;
}
.logo{
  font-size:20px;font-weight:800;color:var(--green);
  letter-spacing:1px;text-shadow:0 0 14px rgba(0,255,136,.55);
}
.nav-links{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.nav-links a{color:var(--muted);font-size:13px;transition:.2s}
.nav-links a:hover{color:var(--green)}
.container{flex:1;max-width:1100px;width:100%;margin:0 auto;padding:26px 18px}
.card{
  background:linear-gradient(145deg,#0b120e,#040806);
  border:1px solid var(--border);border-radius:16px;
  padding:22px;margin-bottom:20px;
  box-shadow:0 0 34px rgba(0,255,136,.05);
}
h1,h2,h3{color:var(--green);margin-bottom:12px;letter-spacing:.5px}
h1{font-size:26px}
h2{font-size:20px}
p{color:var(--muted);line-height:1.75}
.btn{
  display:inline-block;background:linear-gradient(135deg,#00ff88,#00a85a);
  color:#000 !important;font-weight:800;padding:10px 22px;
  border:none;border-radius:9px;cursor:pointer;font-size:14px;
  transition:.2s;text-align:center;box-shadow:0 0 16px rgba(0,255,136,.25);
}
.btn:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,255,136,.45)}
.btn.danger{background:linear-gradient(135deg,#ff4466,#aa0022);color:#fff !important}
.btn.gray{background:#122019;color:#fff !important;border:1px solid var(--border);box-shadow:none}
.btn.small{padding:7px 14px;font-size:12px}
input,textarea,select{
  width:100%;padding:13px 15px;margin:6px 0 16px;
  background:#050b07;border:1px solid var(--border);color:#fff;
  border-radius:9px;font-size:14px;font-family:inherit;letter-spacing:1px;
}
input:focus{outline:none;border-color:var(--green);
  box-shadow:0 0 0 3px rgba(0,255,136,.15)}
label{color:var(--green);font-size:13px;font-weight:700;letter-spacing:.5px}
.bot-card{
  background:#070d09;border:1px solid var(--border);border-radius:12px;
  padding:15px;margin-bottom:12px;display:flex;
  justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;
}
.bot-info{flex:1;min-width:200px}
.bot-name{font-size:15px;font-weight:800;color:#fff;margin-bottom:5px}
.bot-meta{font-size:12px;color:var(--muted);display:flex;gap:12px;flex-wrap:wrap}
.status{display:inline-block;padding:3px 11px;border-radius:20px;font-size:11px;font-weight:800}
.status.running{background:rgba(0,255,136,.15);color:var(--green);border:1px solid rgba(0,255,136,.35)}
.status.stopped{background:rgba(255,68,102,.15);color:#ff6688;border:1px solid rgba(255,68,102,.35)}
.actions{display:flex;gap:8px;flex-wrap:wrap}
.log-box{
  background:#000;border:1px solid var(--border);border-radius:10px;
  padding:14px;font-family:ui-monospace,monospace;font-size:12px;
  color:#00ff88;max-height:420px;overflow:auto;
  white-space:pre-wrap;word-break:break-all;line-height:1.6;
}
.alert{padding:12px 16px;border-radius:9px;margin-bottom:16px;font-size:13px;font-weight:600}
.alert.ok{background:rgba(0,255,136,.12);border:1px solid var(--green);color:var(--green)}
.alert.err{background:rgba(255,68,102,.12);border:1px solid #ff4466;color:#ff8093}
footer{
  background:#000;border-top:1px solid var(--border);
  padding:26px 18px;text-align:center;color:var(--muted);
  font-size:13px;line-height:1.9;
}
footer .brand{
  color:var(--green);font-weight:800;font-size:16px;letter-spacing:1px;
  text-shadow:0 0 10px rgba(0,255,136,.5);margin-bottom:8px;
}
footer .features{
  display:flex;justify-content:center;flex-wrap:wrap;gap:18px;
  margin-top:14px;font-size:12px;color:var(--green);
}
footer .features span{
  padding:5px 12px;background:rgba(0,255,136,.06);
  border:1px solid var(--border);border-radius:20px;
}
.code-box{
  background:#000;border:1px dashed var(--green);border-radius:10px;
  padding:14px;text-align:center;font-family:monospace;
  color:var(--green);font-size:15px;word-break:break-all;
  margin:10px 0;letter-spacing:1px;
}
@media(max-width:640px){.nav{flex-direction:column;gap:10px}}
</style>
</head>
<body>
<nav class="nav">
  <div class="logo">⚡ """ + SITE_NAME + """</div>
  <div class="nav-links">
    {% if session.token %}
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
  <div>منصة استضافة بوتات بايثون بلوحة تحكم كاملة — ترفع ملفك، تشغّله، وتتابع السجلات لحظياً.</div>
  <div class="features">
    <span>🎟️ دخول بالأكواد</span>
    <span>📤 رفع .py / .zip</span>
    <span>⚙️ تثبيت requirements.txt</span>
    <span>📊 سجلات حية</span>
    <span>🚀 تشغيل 24/7</span>
    <span>🔒 عزل كامل لكل مستخدم</span>
  </div>
  <div style="margin-top:14px;font-size:11px;color:#5a6a5f">
    © {{ 2025 }} """ + SITE_NAME + """ — All systems operational.
  </div>
</footer>
</body>
</html>
"""

# ---------- Templates ----------
INDEX_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="card" style="max-width:480px;margin:40px auto;text-align:center">
  <h1 style="font-size:30px">🔐 دخول الموقع</h1>
  <p>أدخل كود الدخول الخاص بك للوصول إلى لوحة الاستضافة.</p>
  <form method="post" style="margin-top:20px">
    <label>كود الدخول</label>
    <input name="code" placeholder="-ABDOUUU-VIP-TEAM-XXXXXXXX"
           required autofocus style="text-align:center;font-size:15px">
    <button class="btn" style="width:100%">🔓 دخول</button>
  </form>
  <p style="margin-top:18px;font-size:12px">
    للحصول على كود، تواصل مع المسؤول عبر تيليجرام.
  </p>
</div>
{% endblock %}
""")

DASH_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="card">
  <h1>🏠 لوحة التحكم</h1>
  <p>مرحباً بك في <b style="color:#fff">""" + SITE_NAME + """</b> — أدر بوتاتك من هنا.</p>
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
          <a class="btn danger small" href="{{ url_for('delete_bot', bid=b['id']) }}"
             onclick="return confirm('متأكد من حذف البوت؟')">🗑 حذف</a>
        </div>
      </div>
    {% endfor %}
  {% else %}
    <p>لا يوجد بوتات بعد. <a href="{{ url_for('upload') }}">ارفع أول بوت</a>.</p>
  {% endif %}
</div>
{% endblock %}
""")

UPLOAD_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="card" style="max-width:620px;margin:20px auto">
  <h1>📤 رفع بوت جديد</h1>
  <p>ارفع ملف <b>.py</b> مباشرة، أو ملف <b>.zip</b> يحتوي المشروع كامل مع requirements.txt</p>
  <form method="post" enctype="multipart/form-data" style="margin-top:16px">
    <label>اسم البوت</label>
    <input name="name" placeholder="My Bot" required>
    <label>الملف (.py أو .zip)</label>
    <input name="file" type="file" accept=".py,.zip" required>
    <button class="btn" style="width:100%">📤 رفع وتشغيل</button>
  </form>
  <p style="margin-top:14px;font-size:12px">
    الحد الأقصى: """ + str(MAX_UPLOAD_MB) + """ ميغا. مع ZIP يتم اكتشاف نقطة الدخول تلقائياً
    (bot.py, main.py, app.py, index.py).
  </p>
</div>
{% endblock %}
""")

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

# ---------- Routes ----------
@app.route("/")
def index():
    if get_session(session.get("token")):
        return redirect(url_for("dashboard"))
    return render_template_string(INDEX_HTML)

@app.route("/", methods=["POST"])
def index_post():
    code = request.form.get("code", "")
    ok, msg, row = validate_code(code)
    if not ok:
        flash(msg, "err")
        return redirect(url_for("index"))
    tok = consume_code(row)
    session["token"] = tok
    flash("✅ تم تسجيل الدخول بنجاح", "ok")
    return redirect(url_for("dashboard"))

@app.route("/dashboard")
@login_required
def dashboard():
    s = get_session(session["token"])
    with db() as c:
        bots = c.execute(
            "SELECT * FROM bots WHERE session_id=? ORDER BY id DESC",
            (s["id"],)
        ).fetchall()
    return render_template_string(DASH_HTML, bots=bots)

@app.route("/upload", methods=["GET","POST"])
@login_required
def upload():
    s = get_session(session["token"])
    if request.method == "POST":
        name = safe(request.form.get("name", "bot"))
        f = request.files.get("file")
        if not f or not f.filename:
            flash("اختر ملفاً.", "err"); return redirect(url_for("upload"))
        fname = safe(f.filename)
        if not fname.lower().endswith((".py",".zip")):
            flash("فقط .py أو .zip مدعومة.", "err"); return redirect(url_for("upload"))

        user_dir = BOTS / f"s{s['id']}_{int(time.time())}"
        user_dir.mkdir(parents=True, exist_ok=True)
        tmp = UPLOADS / fname
        f.save(tmp)

        entry = None
        if fname.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(tmp) as z:
                    root = user_dir.resolve()
                    for info in z.infolist():
                        dest = (user_dir / info.filename).resolve()
                        if not str(dest).startswith(str(root)):
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
            shutil.copy(tmp, user_dir / fname)
            entry = fname
        tmp.unlink(missing_ok=True)

        if not entry:
            flash("لم أجد ملف .py للتشغيل.", "err")
            return redirect(url_for("upload"))

        with db() as c:
            cur = c.execute(
                "INSERT INTO bots(session_id,name,entry,folder,created_at) "
                "VALUES(?,?,?,?,?)",
                (s["id"], name, entry, user_dir.name, iso(now()))
            )
            bid = cur.lastrowid
        ok, m = start_bot(s["id"], bid)
        flash(("✅ " if ok else "❌ ") + m, "ok" if ok else "err")
        return redirect(url_for("dashboard"))
    return render_template_string(UPLOAD_HTML)

@app.route("/start/<int:bid>")
@login_required
def start(bid):
    s = get_session(session["token"])
    ok, m = start_bot(s["id"], bid)
    flash(m, "ok" if ok else "err"); return redirect(url_for("dashboard"))

@app.route("/stop/<int:bid>")
@login_required
def stop(bid):
    s = get_session(session["token"])
    ok, m = stop_bot(s["id"], bid)
    flash(m, "ok" if ok else "err"); return redirect(url_for("dashboard"))

@app.route("/restart/<int:bid>")
@login_required
def restart(bid):
    s = get_session(session["token"])
    stop_bot(s["id"], bid); time.sleep(1)
    ok, m = start_bot(s["id"], bid)
    flash("🔄 " + m, "ok" if ok else "err"); return redirect(url_for("dashboard"))

@app.route("/logs/<int:bid>")
@login_required
def logs(bid):
    s = get_session(session["token"])
    with db() as c:
        bot = c.execute("SELECT * FROM bots WHERE id=? AND session_id=?",
                        (bid, s["id"])).fetchone()
    if not bot: abort(404)
    return render_template_string(LOGS_HTML, bot=bot, log=read_log(bid))

@app.route("/delete/<int:bid>")
@login_required
def delete_bot(bid):
    s = get_session(session["token"])
    stop_bot(s["id"], bid)
    with db() as c:
        row = c.execute("SELECT * FROM bots WHERE id=? AND session_id=?",
                        (bid, s["id"])).fetchone()
        if row: c.execute("DELETE FROM bots WHERE id=?", (bid,))
    if row: shutil.rmtree(BOTS / row["folder"], ignore_errors=True)
    flash("🗑 تم الحذف", "ok"); return redirect(url_for("dashboard"))

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("index"))

@app.route("/health")
def health(): return {"ok": True, "site": SITE_NAME}

# =====================================================================
#                       TELEGRAM BOT
# =====================================================================
ASK_USERS, ASK_DAYS, ASK_DISABLE = range(3)

def is_owner(uid): return uid == OWNER_ID

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎟️ إنشاء كود جديد", callback_data="create")],
        [InlineKeyboardButton("📋 قائمة الأكواد",  callback_data="list")],
        [InlineKeyboardButton("🚫 تعطيل كود",     callback_data="disable")],
        [InlineKeyboardButton("📊 إحصائيات",       callback_data="stats")],
        [InlineKeyboardButton("🔗 رابط الموقع",     callback_data="site")],
    ])

async def bot_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ هذا البوت للمسؤول فقط.")
        return
    text = (
        f"⚡ <b>{SITE_NAME}</b> ⚡\n\n"
        "لوحة تحكم المسؤول — يمكنك إنشاء أكواد الدخول للموقع وإدارتها.\n\n"
        "اختر من الأزرار أدناه 👇"
    )
    await update.message.reply_text(text, parse_mode="HTML",
                                    reply_markup=main_menu_kb())

async def menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_owner(update.effective_user.id):
        await q.edit_message_text("⛔ للمسؤول فقط.")
        return
    d = q.data

    if d == "create":
        await q.edit_message_text(
            "🎟️ <b>إنشاء كود</b>\n\nكم عدد المستخدمين المسموح لهم بهذا الكود؟\n"
            "أرسل رقماً (1 - 10000)",
            parse_mode="HTML"
        )
        return ASK_USERS

    if d == "list":
        with db() as c:
            rows = c.execute("SELECT * FROM codes ORDER BY id DESC LIMIT 30").fetchall()
        if not rows:
            await q.edit_message_text("📭 لا يوجد أكواد.", reply_markup=main_menu_kb())
            return
        lines = ["📋 <b>آخر 30 كود</b>\n"]
        for r in rows:
            status = "🟢" if r["active"] and parse(r["expires_at"]) > now() else "🔴"
            lines.append(
                f"{status} <code>{r['code']}</code>\n"
                f"   👥 {r['used_count']}/{r['max_users']} | "
                f"📅 {r['days']} يوم | ينتهي {r['expires_at'][:10]}"
            )
        await q.edit_message_text("\n\n".join(lines),
                                  parse_mode="HTML",
                                  reply_markup=main_menu_kb())
        return

    if d == "disable":
        with db() as c:
            rows = c.execute(
                "SELECT * FROM codes WHERE active=1 ORDER BY id DESC LIMIT 20"
            ).fetchall()
        if not rows:
            await q.edit_message_text("لا توجد أكواد نشطة.", reply_markup=main_menu_kb())
            return
        kb = [[InlineKeyboardButton(f"🚫 {r['code']}", callback_data=f"dis:{r['id']}")]
              for r in rows]
        kb.append([InlineKeyboardButton("↩️ رجوع", callback_data="menu")])
        await q.edit_message_text("اختر الكود الذي تريد تعطيله:",
                                  reply_markup=InlineKeyboardMarkup(kb))
        return

    if d == "stats":
        with db() as c:
            total_codes = c.execute("SELECT COUNT(*) FROM codes").fetchone()[0]
            active_codes = c.execute(
                "SELECT COUNT(*) FROM codes WHERE active=1").fetchone()[0]
            total_sess = c.execute(
                "SELECT COUNT(*) FROM sessions WHERE active=1").fetchone()[0]
            total_bots = c.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
        text = (
            f"📊 <b>إحصائيات {SITE_NAME}</b>\n\n"
            f"🎟️ إجمالي الأكواد: {total_codes}\n"
            f"🟢 أكواد نشطة: {active_codes}\n"
            f"👥 جلسات فعّالة: {total_sess}\n"
            f"🤖 بوتات مستضافة: {total_bots}"
        )
        await q.edit_message_text(text, parse_mode="HTML",
                                  reply_markup=main_menu_kb())
        return

    if d == "site":
        url = os.environ.get("SITE_URL", "https://your-app.onrender.com")
        await q.edit_message_text(
            f"🔗 رابط الموقع:\n<code>{url}</code>",
            parse_mode="HTML", reply_markup=main_menu_kb()
        )
        return

    if d == "menu":
        await q.edit_message_text("⚡ القائمة الرئيسية:", reply_markup=main_menu_kb())
        return

    if d.startswith("dis:"):
        cid = int(d.split(":")[1])
        with db() as c:
            c.execute("UPDATE codes SET active=0 WHERE id=?", (cid,))
        await q.edit_message_text("🚫 تم تعطيل الكود.",
                                  reply_markup=main_menu_kb())
        return

# ---------- Conversation flow for code creation ----------
async def ask_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return ConversationHandler.END
    try:
        n = int(update.message.text.strip())
        if n < 1 or n > 10000: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ أرسل رقماً بين 1 و 10000")
        return ASK_USERS
    ctx.user_data["max_users"] = n
    await update.message.reply_text("📅 كم عدد الأيام؟ (1 - 3650)")
    return ASK_DAYS

async def ask_days(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return ConversationHandler.END
    try:
        d = int(update.message.text.strip())
        if d < 1 or d > 3650: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ أرسل رقماً بين 1 و 3650")
        return ASK_DAYS
    max_users = ctx.user_data.pop("max_users", 1)
    code = create_code(max_users, d)
    text = (
        f"✅ <b>تم إنشاء الكود بنجاح</b>\n\n"
        f"🎟️ الكود:\n<code>{code}</code>\n\n"
        f"👥 عدد المستخدمين: {max_users}\n"
        f"📅 المدة: {d} يوم\n"
        f"⏳ ينتهي بعد {d} يوم\n\n"
        "📌 انسخ الكود وأرسله للمستخدم."
    )
    await update.message.reply_text(text, parse_mode="HTML",
                                    reply_markup=main_menu_kb())
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء.", reply_markup=main_menu_kb())
    return ConversationHandler.END

def run_telegram_bot():
    """Run the bot in its own asyncio loop."""
    if not BOT_TOKEN:
        log.warning("BOT_TOKEN غير محدد — تم تعطيل بوت تيليجرام.")
        return
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .connect_timeout(60).read_timeout(60)
        .write_timeout(60).pool_timeout(60)
        .build()
    )

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_cb, pattern="^create$")],
        states={
            ASK_USERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_users)],
            ASK_DAYS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_days)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    application.add_handler(CommandHandler("start", bot_start))
    application.add_handler(conv)
    application.add_handler(CallbackQueryHandler(menu_cb))

    log.info("🤖 Telegram bot starting...")
    application.run_polling(drop_pending_updates=True,
                            allowed_updates=Update.ALL_TYPES)

# =====================================================================
#                       MAIN
# =====================================================================
def start_bot_thread():
    t = threading.Thread(target=run_telegram_bot, daemon=True)
    t.start()

# Start bot thread once at import time (works with gunicorn too)
if BOT_TOKEN and OWNER_ID:
    start_bot_thread()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log.info(f"🌐 {SITE_NAME} running on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)