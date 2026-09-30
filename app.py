# =====================================================================
#   𝗔𝗕𝗗𝗢𝗨𝗨 𝗩𝗜𝗣 𝗛𝗢𝗦𝗧𝗜𝗡𝗚  —  Wispbyte-Style Full Hosting Panel
# =====================================================================
import os, sys, json, sqlite3, secrets, subprocess, signal, shutil, re
import zipfile, threading, time, logging, io
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template_string, request, redirect,
                   url_for, session, flash, jsonify, send_file)

# ================= CONFIG =================
SITE_NAME     = "ABDOUU VIP HOSTING"
MAX_UPLOAD_MB = 500
MAX_FILES     = 15
ADMIN_USER    = "ABDOUUU"
ADMIN_PASS    = "ABDOUUU-VIP-100"

# سر الأدمن — من هذا الرابط يدخل بدون كلمة سر
ADMIN_SECRET_PATH = "admin-master-abdouu"

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
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            days INTEGER DEFAULT 30,
            active INTEGER DEFAULT 1,
            created_at TEXT,
            expires_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT,
            expires_at TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            folder TEXT NOT NULL,
            entry TEXT,
            status TEXT DEFAULT 'stopped',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS project_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            is_entry INTEGER DEFAULT 0,
            created_at TEXT
        );
        """)
init_db()

# ================= HELPERS =================
def now():  return datetime.utcnow()
def iso(dt): return dt.isoformat()
def parse(s): return datetime.fromisoformat(s) if s else None
def safe(name):
    return re.sub(r"[^A-Za-z0-9._\-]", "_", Path(name).name)[:160] or "f"

def human_size(n):
    for u in ["B","KB","MB","GB"]:
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"

# ---------- Users ----------
def create_user(username, password, days):
    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            return None, "اسم المستخدم موجود"
        c.execute("INSERT INTO users(username,password,days,created_at,expires_at) VALUES(?,?,?,?,?)",
                  (username, password, days, iso(now()), iso(now()+timedelta(days=days))))
    return username, "ok"

def validate_user(u, p):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE username=? AND password=?", (u,p)).fetchone()
    if not row: return False, "بيانات خاطئة", None
    if not row["active"]: return False, "الحساب معطّل", None
    exp = parse(row["expires_at"])
    if exp and now() > exp: return False, "انتهت صلاحية الحساب", None
    return True, "ok", row

def create_user_session(row):
    tok = secrets.token_urlsafe(32)
    with db() as c:
        c.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                  (tok, row["id"], iso(now()), row["expires_at"]))
    return tok

def get_user_session(token):
    if not token: return None
    with db() as c:
        row = c.execute("SELECT * FROM sessions WHERE token=? AND active=1", (token,)).fetchone()
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
            return redirect(url_for("admin_master"))
        return f(*a, **k)
    return w

# ================= AUTO INSTALL =================
STDLIB = {
    "os","sys","re","json","math","time","asyncio","logging","threading",
    "subprocess","pathlib","shutil","signal","zipfile","tempfile","typing",
    "datetime","random","collections","itertools","functools","traceback",
    "uuid","statistics","sqlite3","http","urllib","email","io","base64",
    "hashlib","hmac","secrets","socket","struct","string","textwrap","warnings",
    "weakref","xml","csv","html","unicodedata","codecs","platform"
}

def detect_python_deps(folder: Path):
    """Scan .py files for import statements → return list of pip packages."""
    deps = set()
    try:
        import ast
        for py in folder.rglob("*.py"):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names: deps.add(a.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    deps.add(node.module.split(".")[0])
    except Exception as e:
        log.warning(f"dep scan: {e}")

    aliases = {
        "telegram":"python-telegram-bot","telethon":"telethon","discord":"discord.py",
        "flask":"Flask","PIL":"Pillow","cv2":"opencv-python","bs4":"beautifulsoup4",
        "dotenv":"python-dotenv","yaml":"PyYAML","dateutil":"python-dateutil",
        "Crypto":"pycryptodome","aiohttp":"aiohttp","requests":"requests",
        "pytz":"pytz","numpy":"numpy","pandas":"pandas","matplotlib":"matplotlib",
        "gtts":"gTTS","qrcode":"qrcode","yt_dlp":"yt-dlp","psutil":"psutil",
        "sqlalchemy":"SQLAlchemy","pymongo":"pymongo","redis":"redis",
        "fastapi":"fastapi","uvicorn":"uvicorn","tornado":"tornado",
        "websockets":"websockets","pydantic":"pydantic","starlette":"starlette",
    }
    return sorted(aliases.get(x, x) for x in deps
                  if x not in STDLIB and x not in {"__future__","__main__"})

def detect_node_deps(folder: Path):
    pkg = folder / "package.json"
    if not pkg.exists(): return []
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
        return list(data.get("dependencies", {}).keys()) + list(data.get("devDependencies", {}).keys())
    except: return []

def pip_install(folder: Path, packages):
    if not packages: return True, "no packages"
    try:
        p = subprocess.run([sys.executable,"-m","pip","install","--no-cache-dir",*packages],
                           cwd=str(folder), capture_output=True, text=True, timeout=900)
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return False, str(e)

def npm_install(folder: Path):
    if not shutil.which("npm"): return False, "npm not installed"
    try:
        p = subprocess.run(["npm","install","--omit=dev"], cwd=str(folder),
                           capture_output=True, text=True, timeout=900)
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return False, str(e)

def detect_runtime(folder: Path):
    """اختيار تلقائي: python أو node"""
    py = list(folder.glob("*.py"))
    js = list(folder.glob("*.js")) + list(folder.glob("*.mjs"))
    if py and not js: return "python"
    if js and not py: return "node"
    if py and js:
        # إذا فيه package.json → node
        return "node" if (folder/"package.json").exists() else "python"
    return None

# ================= PROCESS MGMT =================
def start_project(user_id, pid):
    with db() as c:
        row = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid,user_id)).fetchone()
    if not row: return False, "المشروع غير موجود"
    folder = BOTS / row["folder"]
    if not folder.exists(): return False, "المجلد مفقود"
    entry = row["entry"]
    if not entry:
        return False, "لم يتم تحديد ملف التشغيل بعد"
    entry_path = folder / entry
    if not entry_path.exists(): return False, f"الملف {entry} مفقود"

    # تثبيت المكتبات
    if entry.endswith(".py"):
        req = folder / "requirements.txt"
        if req.exists():
            try:
                subprocess.run([sys.executable,"-m","pip","install","--no-cache-dir","-r",str(req)],
                               cwd=str(folder), capture_output=True, timeout=900)
            except: pass
        else:
            deps = detect_python_deps(folder)
            if deps:
                ok, out = pip_install(folder, deps[:30])
                lf = open(LOGS / f"proj_{pid}.log", "a", encoding="utf-8")
                lf.write(f"\n--- pip install: {'OK' if ok else 'FAIL'} ---\n{out[-2000:]}\n")
                lf.close()
    elif entry.endswith((".js",".mjs",".cjs")):
        if (folder/"package.json").exists():
            npm_install(folder)

    lf = open(LOGS / f"proj_{pid}.log", "a", encoding="utf-8")
    lf.write(f"\n\n=== {now()} | تشغيل {row['name']} | entry={entry} ===\n")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PORT"] = env.get("PORT", "8080")

    cmd = [sys.executable, str(entry_path.resolve())] if entry.endswith(".py") \
        else ["node", str(entry_path.resolve())]

    try:
        proc = subprocess.Popen(cmd, cwd=str(folder), stdout=lf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, text=True,
                                start_new_session=(os.name != "nt"))
    except Exception as e:
        return False, f"خطأ: {e}"
    with _lock: running_bots[pid] = proc
    with db() as c: c.execute("UPDATE projects SET status='running' WHERE id=?", (pid,))
    return True, f"تم التشغيل (PID {proc.pid})"

def stop_project(user_id, pid):
    with _lock: proc = running_bots.get(pid)
    if proc and proc.poll() is None:
        try:
            if os.name == "nt": proc.terminate()
            else: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except: pass
    with _lock: running_bots.pop(pid, None)
    with db() as c: c.execute("UPDATE projects SET status='stopped' WHERE id=?", (pid,))
    return True, "تم الإيقاف"

def read_log(pid, lines=300):
    p = LOGS / f"proj_{pid}.log"
    if not p.exists(): return "— لا توجد سجلات —"
    d = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(d[-lines:])

def project_status(pid):
    with _lock:
        proc = running_bots.get(pid)
    if not proc: return "stopped"
    return "running" if proc.poll() is None else "stopped"

# =====================================================================
#                       FLASK APP
# =====================================================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# =====================================================
#                  BASE TEMPLATE
# =====================================================
BASE_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}""" + SITE_NAME + """{% endblock %}</title>
<style>
:root{
  --bg:#000;--bg2:#04100a;--card:#0a130f;--card2:#060b08;
  --green:#00ff88;--green2:#00cc66;--green3:#009944;
  --border:#14a060;--border2:#1a3a28;--text:#fff;--muted:#8fb0a0;
  --danger:#ff4466;--gold:#ffd700;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{overflow-x:hidden}
body{
  font-family:'Segoe UI',Tahoma,sans-serif;background:#000;
  background-image:
    radial-gradient(circle at 20% 0%,rgba(0,255,136,.08) 0%,transparent 40%),
    radial-gradient(circle at 80% 100%,rgba(0,255,136,.05) 0%,transparent 40%),
    linear-gradient(180deg,#000 0%,#021008 50%,#000 100%);
  color:var(--text);min-height:100vh;display:flex;flex-direction:column;
}
a{color:var(--green);text-decoration:none;transition:.2s}
a:hover{opacity:.85}

/* شريط علوي متحرك */
body::after{
  content:"";position:fixed;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,#00ff88,transparent);
  animation:scan 6s linear infinite;z-index:99;pointer-events:none;
}
@keyframes scan{0%{transform:translateY(0)}100%{transform:translateY(100vh)}}

/* الهيدر */
.header{
  background:linear-gradient(180deg,rgba(0,255,136,.08) 0%,rgba(0,0,0,.95) 100%);
  border-bottom:2px solid var(--border);padding:22px;
  text-align:center;position:relative;
  box-shadow:0 4px 30px rgba(0,255,136,.15);
}
.header h1{
  font-size:30px;font-weight:900;letter-spacing:3px;color:var(--green);
  text-shadow:
    0 0 10px rgba(0,255,136,.8),
    0 0 20px rgba(0,255,136,.5),
    0 0 40px rgba(0,255,136,.3);
  display:inline-flex;align-items:center;gap:12px;justify-content:center;
  animation:pulse 2.5s ease-in-out infinite;
}
@keyframes pulse{
  0%,100%{text-shadow:0 0 10px rgba(0,255,136,.8),0 0 20px rgba(0,255,136,.5)}
  50%{text-shadow:0 0 15px rgba(0,255,136,1),0 0 40px rgba(0,255,136,.6),0 0 60px rgba(0,255,136,.4)}
}
.header .subtitle{
  color:var(--muted);font-size:13px;margin-top:8px;letter-spacing:1px;
}
.header .lightning{
  color:var(--green);font-size:32px;
  filter:drop-shadow(0 0 8px rgba(0,255,136,.8));
}

/* شريط التنقل */
.nav{
  background:rgba(0,0,0,.9);border-bottom:1px solid var(--border2);
  padding:12px 20px;display:flex;justify-content:space-between;
  align-items:center;position:sticky;top:0;z-index:60;
  backdrop-filter:blur(14px);flex-wrap:wrap;gap:10px;
}
.nav .logo{
  font-size:14px;font-weight:800;color:var(--green);letter-spacing:1px;
}
.nav .links{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.nav .links a{
  color:var(--muted);font-size:13px;font-weight:600;
  padding:6px 12px;border-radius:8px;transition:.2s;
}
.nav .links a:hover{color:var(--green);background:rgba(0,255,136,.08)}

.container{flex:1;max-width:1200px;width:100%;margin:0 auto;padding:24px 16px}
.card{
  background:linear-gradient(145deg,#0a130f,#040806);
  border:1px solid var(--border2);border-radius:16px;padding:24px;
  margin-bottom:20px;position:relative;overflow:hidden;
  box-shadow:0 0 40px rgba(0,255,136,.06),inset 0 1px 0 rgba(0,255,136,.08);
}
.card::before{
  content:"";position:absolute;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,var(--green),transparent);
  opacity:.6;
}
h1,h2,h3{color:var(--green);margin-bottom:14px;letter-spacing:.5px}
h1{font-size:24px}
h2{font-size:19px}
p{color:var(--muted);line-height:1.75}
.btn{
  display:inline-flex;align-items:center;gap:6px;justify-content:center;
  background:linear-gradient(135deg,#00ff88,#009944);color:#000!important;
  font-weight:800;padding:11px 22px;border:none;border-radius:10px;
  cursor:pointer;font-size:14px;transition:.25s;text-align:center;
  box-shadow:0 0 18px rgba(0,255,136,.35);letter-spacing:.5px;
}
.btn:hover{transform:translateY(-2px);box-shadow:0 10px 28px rgba(0,255,136,.55)}
.btn:active{transform:translateY(0)}
.btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
.btn.danger{background:linear-gradient(135deg,#ff4466,#aa0022);color:#fff!important;box-shadow:0 0 18px rgba(255,68,102,.3)}
.btn.gray{background:#0d1a12;color:#fff!important;border:1px solid var(--border2);box-shadow:none}
.btn.gray:hover{border-color:var(--green)}
.btn.gold{background:linear-gradient(135deg,#ffd700,#b8860b);color:#000!important}
.btn.small{padding:7px 13px;font-size:12px;border-radius:7px}
.btn.full{width:100%}

input,select,textarea{
  width:100%;padding:13px 15px;margin:6px 0 16px;background:#040a06;
  border:1px solid var(--border2);color:#fff;border-radius:10px;
  font-size:14px;font-family:inherit;transition:.2s;
}
input:focus,select:focus,textarea:focus{
  outline:none;border-color:var(--green);
  box-shadow:0 0 0 3px rgba(0,255,136,.15),0 0 20px rgba(0,255,136,.15);
}
label{color:var(--green);font-size:13px;font-weight:700;letter-spacing:.5px;display:block;margin-bottom:2px}

/* Hero */
.hero{
  text-align:center;padding:40px 20px;
  background:linear-gradient(135deg,rgba(0,255,136,.08),transparent);
  border:2px solid var(--border);border-radius:20px;margin-bottom:22px;
  position:relative;overflow:hidden;
}
.hero::before{
  content:"";position:absolute;inset:0;
  background:radial-gradient(circle at 50% 50%,rgba(0,255,136,.1),transparent 70%);
  pointer-events:none;
}
.hero h1{font-size:32px;margin-bottom:12px;letter-spacing:2px;position:relative}
.hero p{font-size:15px;max-width:600px;margin:0 auto;line-height:1.85;position:relative}
.hero .badge{
  display:inline-block;margin-top:14px;padding:6px 16px;
  background:rgba(0,255,136,.1);border:1px solid var(--green);
  border-radius:20px;color:var(--green);font-size:12px;font-weight:700;
  letter-spacing:1px;
}

/* Project card */
.project{
  background:#060d09;border:1px solid var(--border2);border-radius:14px;
  padding:18px;margin-bottom:14px;position:relative;transition:.25s;
}
.project:hover{border-color:rgba(0,255,136,.4);transform:translateY(-2px);
  box-shadow:0 8px 30px rgba(0,255,136,.1)}
.project-head{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:12px}
.project-name{font-size:16px;font-weight:800;color:#fff;display:flex;align-items:center;gap:10px}
.project-meta{font-size:12px;color:var(--muted);display:flex;gap:14px;flex-wrap:wrap}
.project-actions{display:flex;gap:8px;flex-wrap:wrap}

.status{
  display:inline-block;padding:4px 12px;border-radius:20px;
  font-size:11px;font-weight:800;letter-spacing:.5px;
}
.status.running{background:rgba(0,255,136,.15);color:var(--green);border:1px solid rgba(0,255,136,.4);animation:blink 2s infinite}
.status.stopped{background:rgba(255,68,102,.12);color:#ff6688;border:1px solid rgba(255,68,102,.35)}
.status.admin{background:rgba(255,215,0,.15);color:var(--gold);border:1px solid rgba(255,215,0,.4)}
@keyframes blink{50%{opacity:.7}}

/* Files list */
.files-list{background:#000;border:1px solid var(--border2);border-radius:10px;overflow:hidden;margin-top:10px}
.file-row{
  display:flex;justify-content:space-between;align-items:center;
  padding:10px 14px;border-bottom:1px solid #0d1a12;font-size:13px;
}
.file-row:last-child{border-bottom:none}
.file-row:hover{background:rgba(0,255,136,.03)}
.file-name{color:#e8f0ea;font-family:monospace;font-size:12px}
.file-name.entry{color:var(--green);font-weight:800}
.file-name.entry::before{content:"⭐ ";color:var(--gold)}
.file-actions{display:flex;gap:6px}

/* Logs */
.log-box{
  background:#000;border:1px solid var(--border2);border-radius:10px;
  padding:14px;font-family:ui-monospace,monospace;font-size:12px;
  color:#00ff88;max-height:500px;overflow:auto;white-space:pre-wrap;
  word-break:break-all;line-height:1.6;
}

/* Alerts */
.alert{padding:13px 16px;border-radius:10px;margin-bottom:16px;font-size:13px;font-weight:600;animation:slideIn .3s}
@keyframes slideIn{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:none}}
.alert.ok{background:rgba(0,255,136,.12);border:1px solid var(--green);color:var(--green)}
.alert.err{background:rgba(255,68,102,.12);border:1px solid #ff4466;color:#ff8093}

/* Table */
table{width:100%;border-collapse:collapse;font-size:13px}
table th{padding:12px;text-align:right;color:var(--green);font-weight:800;
  border-bottom:2px solid var(--border2);font-size:12px;letter-spacing:.5px}
table td{padding:12px;border-bottom:1px solid #0d1a12;color:#e8f0ea}
table tr:hover td{background:rgba(0,255,136,.03)}

/* Upload area */
.dropzone{
  border:2px dashed var(--green);border-radius:14px;padding:30px;
  text-align:center;background:rgba(0,255,136,.03);cursor:pointer;
  transition:.25s;
}
.dropzone:hover{background:rgba(0,255,136,.08);border-style:solid}
.dropzone input{display:none}
.dropzone-icon{font-size:42px;color:var(--green);margin-bottom:10px}
.dropzone-text{color:var(--text);font-weight:700;margin-bottom:6px}
.dropzone-hint{color:var(--muted);font-size:12px}
.file-preview{margin-top:14px;text-align:right}
.file-preview .item{
  display:flex;justify-content:space-between;padding:8px 12px;
  background:#000;border:1px solid var(--border2);border-radius:8px;
  margin-bottom:6px;font-size:12px;
}
.file-preview .item .name{color:var(--green);font-family:monospace}
.file-preview .item .size{color:var(--muted)}

/* Footer */
footer{
  background:#000;border-top:2px solid var(--border2);
  padding:30px 18px;text-align:center;color:var(--muted);
  font-size:13px;line-height:1.9;margin-top:auto;
}
footer .brand{
  color:var(--green);font-weight:900;font-size:18px;
  letter-spacing:2px;margin-bottom:10px;
  text-shadow:0 0 14px rgba(0,255,136,.6);
}
footer .features{
  display:flex;justify-content:center;flex-wrap:wrap;gap:14px;
  margin-top:18px;font-size:12px;
}
footer .features span{
  padding:6px 14px;background:rgba(0,255,136,.06);
  border:1px solid var(--border2);border-radius:20px;color:var(--green);
  font-weight:600;
}
footer .credit{margin-top:18px;font-size:11px;color:#4a5a50}

/* Grid */
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.grid-3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.grid-4{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}

/* Stat box */
.stat{
  background:linear-gradient(145deg,#08120d,#030604);
  border:1px solid var(--border2);border-radius:14px;padding:20px;
  text-align:center;transition:.25s;
}
.stat:hover{border-color:rgba(0,255,136,.4);transform:translateY(-2px)}
.stat .num{font-size:32px;font-weight:900;color:var(--green);
  text-shadow:0 0 15px rgba(0,255,136,.5);margin-bottom:6px}
.stat .lbl{font-size:12px;color:var(--muted);font-weight:700;letter-spacing:.5px}

@media(max-width:768px){
  .header h1{font-size:22px;letter-spacing:1px}
  .hero h1{font-size:24px}
  .grid-2,.grid-3,.grid-4{grid-template-columns:1fr}
  .nav{flex-direction:column;align-items:flex-start}
}
</style>
</head>
<body>

<div class="header">
  <h1><span class="lightning">⚡</span> """ + SITE_NAME + """ <span class="lightning">⚡</span></h1>
  <div class="subtitle">🚀 منصة استضافة بوتات ومواقع — Python • Node.js • Free Fire • Telegram • Discord</div>
</div>

<nav class="nav">
  <div class="logo">⚡ ABDOUU VIP</div>
  <div class="links">
    {% if session.is_admin %}
      <a href="{{ url_for('admin_panel') }}">👑 لوحة الأدمن</a>
      <a href="{{ url_for('admin_logout') }}">🚪 خروج</a>
    {% elif session.token %}
      <a href="{{ url_for('dashboard') }}">🏠 الرئيسية</a>
      <a href="{{ url_for('upload') }}">📤 رفع مشروع</a>
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
  <div>منصة استضافة بوتات ومواقع شاملة — Python • Node.js • Free Fire • Telegram • Discord • Flask</div>
  <div class="features">
    <span>🔐 دخول آمن</span>
    <span>📤 رفع حتى 15 ملف</span>
    <span>📦 فك ZIP تلقائي</span>
    <span>⭐ اختيار ملف التشغيل</span>
    <span>⚙️ تثبيت مكتبات ذكي</span>
    <span>🎮 Python + Node.js</span>
    <span>📊 سجلات حية</span>
    <span>🚀 24/7</span>
  </div>
  <div class="credit">© 2025 ABDOUU VIP HOSTING — جميع الحقوق محفوظة</div>
</footer>

{% block scripts %}{% endblock %}
</body>
</html>
"""

# ---------------- LOGIN ----------------
LOGIN_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="hero">
  <h1>🔐 تسجيل الدخول</h1>
  <p>أدخل بياناتك للوصول إلى لوحة الاستضافة وإدارة مشاريعك.</p>
  <div class="badge">🚀 منصة استضافة احترافية</div>
</div>

<div class="card" style="max-width:440px;margin:0 auto">
  <h2 style="text-align:center">🔐 بوابة المستخدمين</h2>
  <form method="post" style="margin-top:16px">
    <label>👤 اسم المستخدم</label>
    <input name="username" required autofocus autocomplete="off">
    <label>🔑 كلمة المرور</label>
    <input name="password" type="password" required autocomplete="off">
    <button class="btn full">🔓 دخول</button>
  </form>
  <p style="margin-top:16px;font-size:12px;text-align:center">
    للحصول على حساب تواصل مع المسؤول
  </p>
</div>
{% endblock %}
""")

# ---------------- DASHBOARD ----------------
DASH_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="hero">
  <h1>🏠 مرحباً {{ username }}</h1>
  <p>لوحة تحكم مشاريعك — ارفع، شغّل، تابع، وتحكم بكل شيء.</p>
  <div class="badge">⏳ الصلاحية حتى {{ expires_at[:10] }}</div>
</div>

<div class="grid-4" style="margin-bottom:20px">
  <div class="stat"><div class="num">{{ projects|length }}</div><div class="lbl">📦 المشاريع</div></div>
  <div class="stat"><div class="num">{{ running_count }}</div><div class="lbl">🟢 يعمل الآن</div></div>
  <div class="stat"><div class="num">{{ total_files }}</div><div class="lbl">📄 الملفات</div></div>
  <div class="stat"><div class="num">{{ total_size }}</div><div class="lbl">💾 الحجم</div></div>
</div>

<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:14px">
    <h2 style="margin:0">📦 مشاريعك</h2>
    <a class="btn" href="{{ url_for('upload') }}">📤 رفع مشروع جديد</a>
  </div>

  {% if projects %}
    {% for p in projects %}
      <div class="project">
        <div class="project-head">
          <div>
            <div class="project-name">
              🤖 {{ p['name'] }}
              <span class="status {{ p['status'] }}">{{ 'يعمل' if p['status']=='running' else 'متوقف' }}</span>
            </div>
            <div class="project-meta" style="margin-top:8px">
              <span>📄 <b style="color:var(--green)">{{ p['entry'] or 'لم يحدد' }}</b></span>
              <span>📁 {{ p['file_count'] }} ملف</span>
              <span>🕒 {{ p['created_at'][:16] }}</span>
            </div>
          </div>
          <div class="project-actions">
            {% if p['status']=='running' %}
              <a class="btn danger small" href="{{ url_for('stop', pid=p['id']) }}">⏹ إيقاف</a>
              <a class="btn gray small" href="{{ url_for('restart', pid=p['id']) }}">🔄 إعادة</a>
            {% else %}
              <a class="btn small" href="{{ url_for('start', pid=p['id']) }}">▶️ تشغيل</a>
            {% endif %}
            <a class="btn gray small" href="{{ url_for('files', pid=p['id']) }}">📁 الملفات</a>
            <a class="btn gray small" href="{{ url_for('logs', pid=p['id']) }}">📜 سجلات</a>
            <a class="btn danger small" href="{{ url_for('delete_project', pid=p['id']) }}" onclick="return confirm('حذف المشروع نهائياً؟')">🗑</a>
          </div>
        </div>
      </div>
    {% endfor %}
  {% else %}
    <p style="text-align:center;padding:30px">
      لا يوجد مشاريع بعد. <a href="{{ url_for('upload') }}">ارفع أول مشروع</a> 🚀
    </p>
  {% endif %}
</div>
{% endblock %}
""")

# ---------------- UPLOAD ----------------
UPLOAD_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="card" style="max-width:820px;margin:20px auto">
  <h1>📤 رفع مشروع جديد</h1>
  <p>ارفع ملفاتك مباشرة (حتى 15 ملف) — إذا رفعت ZIP رح ننفكه تلقائياً.</p>

  <form method="post" enctype="multipart/form-data" style="margin-top:20px" id="uploadForm">
    <label>📛 اسم المشروع</label>
    <input name="name" placeholder="مثال: My Telegram Bot" required autocomplete="off">

    <label>📁 الملفات (حتى 15 ملف — .py .js .zip .json .txt ...)</label>
    <div class="dropzone" onclick="document.getElementById('fileInput').click()">
      <div class="dropzone-icon">📤</div>
      <div class="dropzone-text">اضغط لاختيار الملفات</div>
      <div class="dropzone-hint">يمكن اختيار أكثر من ملف • ZIP يُفك تلقائياً</div>
      <input type="file" id="fileInput" name="files" multiple required
             accept=".py,.js,.mjs,.cjs,.zip,.json,.txt,.yaml,.yml,.toml,.env,.cfg,.ini">
    </div>

    <div class="file-preview" id="preview"></div>

    <div style="margin-top:20px;padding:14px;background:#000;border:1px solid var(--border2);border-radius:10px">
      <label style="margin-bottom:10px">⚙️ خيارات</label>
      <div style="display:flex;gap:16px;flex-wrap:wrap">
        <label style="display:flex;align-items:center;gap:8px;color:var(--muted);font-weight:400;cursor:pointer">
          <input type="checkbox" name="auto_install" value="1" checked style="width:auto;margin:0">
          <span>📦 تثبيت المكتبات تلقائياً</span>
        </label>
        <label style="display:flex;align-items:center;gap:8px;color:var(--muted);font-weight:400;cursor:pointer">
          <input type="checkbox" name="auto_start" value="1" checked style="width:auto;margin:0">
          <span>🚀 تشغيل بعد الرفع مباشرة</span>
        </label>
      </div>
    </div>

    <button class="btn full" style="margin-top:18px">📤 رفع المشروع</button>
  </form>
</div>

<script>
const fi = document.getElementById('fileInput');
const pv = document.getElementById('preview');
fi.addEventListener('change', () => {
  pv.innerHTML = '';
  const files = Array.from(fi.files);
  if (files.length > 15) {
    pv.innerHTML = '<div class="item"><span class="name" style="color:#ff6688">⚠️ أكثر من 15 ملف! سيتم رفع أول 15 فقط</span></div>';
  }
  files.slice(0,15).forEach(f => {
    const div = document.createElement('div');
    div.className = 'item';
    const kb = f.size < 1024 ? f.size+' B' : f.size < 1048576 ? (f.size/1024).toFixed(1)+' KB' : (f.size/1048576).toFixed(2)+' MB';
    div.innerHTML = `<span class="name">📄 ${f.name}</span><span class="size">${kb}</span>`;
    pv.appendChild(div);
  });
});
</script>
{% endblock %}
""")

# ---------------- FILES ----------------
FILES_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:16px">
    <h2 style="margin:0">📁 ملفات المشروع: {{ project['name'] }}</h2>
    <a class="btn gray small" href="{{ url_for('dashboard') }}">↩ رجوع</a>
  </div>

  <p style="margin-bottom:14px">
    ⭐ اضغط على <b style="color:var(--gold)">"تعيين كملف تشغيل"</b> لاختيار الملف اللي رح يشتغل.
  </p>

  <div class="files-list">
    {% for f in files %}
      <div class="file-row">
        <div>
          <span class="file-name {{ 'entry' if f['is_entry'] else '' }}">{{ f['filename'] }}</span>
          <span style="color:var(--muted);font-size:11px;margin-right:10px">({{ f['size'] }})</span>
        </div>
        <div class="file-actions">
          {% if not f['is_entry'] %}
            <a class="btn small" href="{{ url_for('set_entry', pid=project['id'], fid=f['id']) }}">⭐ تعيين كملف تشغيل</a>
          {% else %}
            <span class="status running">⭐ ملف التشغيل الحالي</span>
          {% endif %}
          <a class="btn danger small" href="{{ url_for('delete_file', pid=project['id'], fid=f['id']) }}" onclick="return confirm('حذف الملف؟')">🗑</a>
        </div>
      </div>
    {% endfor %}
  </div>
</div>
{% endblock %}
""")

# ---------------- LOGS ----------------
LOGS_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:16px">
    <h2 style="margin:0">📜 سجلات: {{ project['name'] }}</h2>
    <div style="display:flex;gap:8px">
      <a class="btn gray small" href="{{ url_for('dashboard') }}">↩ رجوع</a>
      <a class="btn small" href="{{ url_for('logs', pid=project['id']) }}">🔄 تحديث</a>
    </div>
  </div>
  <div class="log-box">{{ log }}</div>
</div>
{% endblock %}
""")

# ---------------- ADMIN MASTER (بدون كلمة سر) ----------------
ADMIN_LOGIN_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="hero" style="border-color:var(--gold);background:linear-gradient(135deg,rgba(255,215,0,.08),transparent)">
  <h1 style="color:var(--gold)">👑 بوابة الأدمن</h1>
  <p>هذه البوابة مخصصة للمسؤول الرئيسي.</p>
</div>
<div class="card" style="max-width:440px;margin:0 auto;border-color:rgba(255,215,0,.3)">
  <h2 style="text-align:center;color:var(--gold)">🔐 تسجيل دخول الأدمن</h2>
  <form method="post" style="margin-top:16px">
    <label>👤 اسم المستخدم</label>
    <input name="username" required autofocus autocomplete="off">
    <label>🔑 كلمة المرور</label>
    <input name="password" type="password" required autocomplete="off">
    <button class="btn gold full">🔓 دخول الأدمن</button>
  </form>
</div>
{% endblock %}
""")

# ---------------- ADMIN PANEL ----------------
ADMIN_PANEL_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="hero" style="border-color:var(--gold);background:linear-gradient(135deg,rgba(255,215,0,.08),transparent)">
  <h1 style="color:var(--gold)">👑 لوحة تحكم المسؤول</h1>
  <p>مرحباً <b style="color:#fff">ABDOUUU</b> — تحكم كامل بالنظام</p>
  <div class="badge" style="border-color:var(--gold);color:var(--gold);background:rgba(255,215,0,.1)">🛡️ صلاحيات كاملة</div>
</div>

<div class="grid-4" style="margin-bottom:20px">
  <div class="stat"><div class="num">{{ users|length }}</div><div class="lbl">👥 المستخدمون</div></div>
  <div class="stat"><div class="num">{{ projects|length }}</div><div class="lbl">📦 المشاريع</div></div>
  <div class="stat"><div class="num">{{ running_count }}</div><div class="lbl">🟢 يعمل الآن</div></div>
  <div class="stat"><div class="num">{{ total_files }}</div><div class="lbl">📄 الملفات</div></div>
</div>

<div class="card">
  <h2>➕ إضافة مستخدم جديد</h2>
  <form method="post" action="{{ url_for('admin_create_user') }}"
        style="display:flex;gap:10px;flex-wrap:wrap;align-items:end">
    <div style="flex:1;min-width:150px">
      <label>اسم المستخدم</label>
      <input name="username" required autocomplete="off">
    </div>
    <div style="flex:1;min-width:150px">
      <label>كلمة المرور</label>
      <input name="password" required autocomplete="off">
    </div>
    <div style="flex:1;min-width:100px">
      <label>عدد الأيام</label>
      <input name="days" type="number" value="30" min="1" max="3650">
    </div>
    <button class="btn gold" style="margin-bottom:16px">➕ إنشاء</button>
  </form>

  {% if new_user %}
    <div style="background:#000;border:2px dashed var(--green);border-radius:10px;padding:14px;margin-top:10px">
      <div style="color:var(--muted);font-size:12px;margin-bottom:8px">✅ تم إنشاء الحساب — احتفظ بهذي البيانات:</div>
      <div style="color:var(--green);font-size:14px;font-family:monospace">
        👤 <b>{{ new_user.username }}</b> &nbsp;|&nbsp; 🔑 <b>{{ new_user.password }}</b> &nbsp;|&nbsp; 📅 {{ new_user.days }} يوم
      </div>
    </div>
  {% endif %}
</div>

<div class="card">
  <h2>👥 المستخدمون ({{ users|length }})</h2>
  {% if users %}
    <div style="overflow-x:auto">
    <table>
      <thead><tr>
        <th>#</th><th>الاسم</th><th>كلمة المرور</th><th>الأيام</th>
        <th>الحالة</th><th>ينتهي</th><th>إجراء</th>
      </tr></thead>
      <tbody>
        {% for u in users %}
          <tr>
            <td>{{ u['id'] }}</td>
            <td><b style="color:#fff">{{ u['username'] }}</b></td>
            <td><code style="color:var(--green);background:#000;padding:3px 8px;border-radius:5px;font-size:11px">{{ u['password'] }}</code></td>
            <td style="text-align:center">{{ u['days'] }}</td>
            <td style="text-align:center">
              {% if u['active'] %}<span style="color:var(--green)">🟢 نشط</span>
              {% else %}<span style="color:#ff6688">🔴 معطّل</span>{% endif %}
            </td>
            <td style="text-align:center;font-size:11px">{{ u['expires_at'][:10] }}</td>
            <td>
              {% if u['active'] %}
                <a class="btn danger small" href="{{ url_for('admin_disable_user', uid=u['id']) }}">🚫</a>
              {% else %}
                <a class="btn small" href="{{ url_for('admin_enable_user', uid=u['id']) }}">✅</a>
              {% endif %}
              <a class="btn danger small" href="{{ url_for('admin_delete_user', uid=u['id']) }}" onclick="return confirm('حذف نهائي؟')">🗑</a>
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
    </div>
  {% else %}<p>لا يوجد مستخدمون</p>{% endif %}
</div>

<div class="card">
  <h2>📦 كل المشاريع ({{ projects|length }})</h2>
  {% if projects %}
    <div style="overflow-x:auto">
    <table>
      <thead><tr><th>المشروع</th><th>المستخدم</th><th>ملف التشغيل</th><th>الحالة</th></tr></thead>
      <tbody>
        {% for p in projects %}
          <tr>
            <td>🤖 <b style="color:#fff">{{ p['name'] }}</b></td>
            <td>{{ p['username'] or '—' }}</td>
            <td><code style="color:var(--green);font-size:11px">{{ p['entry'] or '—' }}</code></td>
            <td><span class="status {{ p['status'] }}">{{ 'يعمل' if p['status']=='running' else 'متوقف' }}</span></td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
    </div>
  {% else %}<p>لا يوجد مشاريع</p>{% endif %}
</div>
{% endblock %}
""")

# =====================================================================
#                       ROUTES
# =====================================================================

# ---------- Admin MASTER (بدون كلمة سر — فقط من الرابط السري) ----------
@app.route(f"/{ADMIN_SECRET_PATH}")
def admin_master():
    """دخول الأدمن المباشر بدون كلمة سر — من الرابط السري فقط."""
    session["is_admin"] = True
    log.info(f"👑 دخول أدمن من الرابط السري | IP={request.remote_addr}")
    flash("👑 مرحباً بك في لوحة الأدمن", "ok")
    return redirect(url_for("admin_panel"))

# ---------- Admin login بالاسم + كلمة سر (احتياطي) ----------
@app.route("/admin-login", methods=["GET","POST"])
def admin_login():
    if session.get("is_admin"):
        return redirect(url_for("admin_panel"))
    if request.method == "POST":
        u = request.form.get("username","").strip()
        p = request.form.get("password","")
        if u == ADMIN_USER and p == ADMIN_PASS:
            session["is_admin"] = True
            return redirect(url_for("admin_panel"))
        flash("❌ بيانات دخول خاطئة", "err")
    return render_template_string(ADMIN_LOGIN_HTML)

@app.route("/admin-logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))

@app.route("/admin")
@admin_required
def admin_panel():
    with db() as c:
        users = c.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
        projects = c.execute("""
            SELECT p.*, u.username FROM projects p
            LEFT JOIN users u ON u.id=p.user_id ORDER BY p.id DESC
        """).fetchall()
        total_files = c.execute("SELECT COUNT(*) FROM project_files").fetchone()[0]
    running_count = sum(1 for p in projects if p["status"]=="running")
    new_user = session.pop("last_new_user", None)
    return render_template_string(ADMIN_PANEL_HTML,
        users=users, projects=projects,
        running_count=running_count, total_files=total_files,
        new_user=new_user)

@app.route("/admin/create-user", methods=["POST"])
@admin_required
def admin_create_user():
    try:
        u = request.form.get("username","").strip()
        p = request.form.get("password","").strip()
        d = max(1, min(int(request.form.get("days",30)), 3650))
        if not u or not p: raise ValueError("فارغ")
    except Exception as e:
        flash(f"بيانات غير صالحة: {e}", "err")
        return redirect(url_for("admin_panel"))
    name, msg = create_user(u, p, d)
    if not name:
        flash(f"❌ {msg}", "err")
        return redirect(url_for("admin_panel"))
    session["last_new_user"] = {"username":u,"password":p,"days":d}
    flash(f"✅ تم إنشاء: {u}", "ok")
    return redirect(url_for("admin_panel"))

@app.route("/admin/disable-user/<int:uid>")
@admin_required
def admin_disable_user(uid):
    with db() as c:
        c.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
        c.execute("UPDATE sessions SET active=0 WHERE user_id=?", (uid,))
    flash("🚫 تم التعطيل", "ok")
    return redirect(url_for("admin_panel"))

@app.route("/admin/enable-user/<int:uid>")
@admin_required
def admin_enable_user(uid):
    with db() as c:
        c.execute("UPDATE users SET active=1 WHERE id=?", (uid,))
    flash("✅ تم التفعيل", "ok")
    return redirect(url_for("admin_panel"))

@app.route("/admin/delete-user/<int:uid>")
@admin_required
def admin_delete_user(uid):
    with db() as c:
        rows = c.execute("SELECT id, folder FROM projects WHERE user_id=?", (uid,)).fetchall()
        for r in rows:
            stop_project(uid, r["id"])
            shutil.rmtree(BOTS / r["folder"], ignore_errors=True)
        c.execute("DELETE FROM project_files WHERE project_id IN (SELECT id FROM projects WHERE user_id=?)", (uid,))
        c.execute("DELETE FROM projects WHERE user_id=?", (uid,))
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    flash("🗑 تم الحذف الكامل", "ok")
    return redirect(url_for("admin_panel"))

# ---------- User Login ----------
@app.route("/", methods=["GET"])
def index():
    if get_user_session(session.get("token")):
        return redirect(url_for("dashboard"))
    return render_template_string(LOGIN_HTML)

@app.route("/", methods=["POST"])
def login_post():
    ok, msg, row = validate_user(request.form.get("username","").strip(),
                                 request.form.get("password",""))
    if not ok:
        flash(msg, "err"); return redirect(url_for("index"))
    session["token"] = create_user_session(row)
    session["username"] = row["username"]
    flash(f"✅ مرحباً {row['username']}", "ok")
    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout():
    session.pop("token", None); session.pop("username", None)
    return redirect(url_for("index"))

@app.route("/dashboard")
@user_required
def dashboard():
    s = get_user_session(session["token"])
    with db() as c:
        projects = c.execute("""
            SELECT p.*, (SELECT COUNT(*) FROM project_files WHERE project_id=p.id) as file_count
            FROM projects p WHERE p.user_id=? ORDER BY p.id DESC
        """, (s["user_id"],)).fetchall()
        user = c.execute("SELECT * FROM users WHERE id=?", (s["user_id"],)).fetchone()
        total_files = c.execute("""
            SELECT COUNT(*) FROM project_files WHERE project_id IN
            (SELECT id FROM projects WHERE user_id=?)
        """, (s["user_id"],)).fetchone()[0]

    # حجم المجموع
    total_bytes = 0
    for p in projects:
        pf = BOTS / p["folder"]
        if pf.exists():
            for f in pf.rglob("*"):
                if f.is_file():
                    try: total_bytes += f.stat().st_size
                    except: pass
    running_count = sum(1 for p in projects if p["status"]=="running")

    return render_template_string(DASH_HTML,
        projects=projects, username=user["username"], expires_at=user["expires_at"],
        running_count=running_count, total_files=total_files,
        total_size=human_size(total_bytes))

# ---------- Upload ----------
@app.route("/upload", methods=["GET","POST"])
@user_required
def upload():
    s = get_user_session(session["token"])
    if request.method == "POST":
        name = safe(request.form.get("name", "project"))
        files = request.files.getlist("files")
        files = [f for f in files if f and f.filename][:MAX_FILES]
        if not files:
            flash("اختر ملفاً واحداً على الأقل", "err"); return redirect(url_for("upload"))

        folder_name = f"u{s['user_id']}_{int(time.time())}"
        folder = BOTS / folder_name
        folder.mkdir(parents=True, exist_ok=True)

        saved_files = []
        extracted = False

        for f in files:
            fname = safe(f.filename)
            if fname.lower().endswith(".zip"):
                tmp = UPLOADS / f"tmp_{int(time.time())}_{fname}"
                f.save(tmp)
                try:
                    with zipfile.ZipFile(tmp) as z:
                        root = folder.resolve()
                        for info in z.infolist():
                            dest = (folder / info.filename).resolve()
                            if not str(dest).startswith(str(root)):
                                raise ValueError("مسار غير آمن")
                        z.extractall(folder)
                    extracted = True
                except Exception as e:
                    flash(f"خطأ فك ZIP: {e}", "err")
                finally:
                    tmp.unlink(missing_ok=True)
            else:
                dest = folder / fname
                f.save(dest)
                saved_files.append(fname)

        # سجّل كل الملفات (الموجودة بعد الفك)
        with db() as c:
            cur = c.execute(
                "INSERT INTO projects(user_id,name,folder,created_at) VALUES(?,?,?,?)",
                (s["user_id"], name, folder_name, iso(now()))
            )
            pid = cur.lastrowid

            # سجل الملفات
            all_files = [f for f in folder.rglob("*") if f.is_file()]
            for f in all_files:
                rel = str(f.relative_to(folder))
                c.execute(
                    "INSERT INTO project_files(project_id,filename,created_at) VALUES(?,?,?)",
                    (pid, rel, iso(now()))
                )

        # اختيار ملف تشغيل تلقائي
        runtime = detect_runtime(folder)
        entry = None
        for cand in ("bot.py","main.py","app.py","index.py","index.js","main.js","bot.js"):
            if (folder / cand).exists():
                entry = cand; break
        if not entry:
            py_files = list(folder.rglob("*.py"))
            js_files = list(folder.rglob("*.js"))
            if py_files: entry = str(py_files[0].relative_to(folder))
            elif js_files: entry = str(js_files[0].relative_to(folder))

        if entry:
            with db() as c:
                c.execute("UPDATE projects SET entry=? WHERE id=?", (entry, pid))
                c.execute("UPDATE project_files SET is_entry=1 WHERE project_id=? AND filename=?", (pid, entry))

        # تثبيت المكتبات
        if request.form.get("auto_install"):
            if entry and entry.endswith(".py"):
                req = folder / "requirements.txt"
                if req.exists():
                    try:
                        subprocess.run([sys.executable,"-m","pip","install","--no-cache-dir","-r",str(req)],
                                       cwd=str(folder), capture_output=True, timeout=900)
                    except: pass
                else:
                    deps = detect_python_deps(folder)
                    if deps:
                        pip_install(folder, deps[:30])
            elif (folder / "package.json").exists():
                npm_install(folder)

        # تشغيل تلقائي
        if request.form.get("auto_start") and entry:
            ok, m = start_project(s["user_id"], pid)
            flash(("✅ " if ok else "❌ ") + m, "ok" if ok else "err")
        else:
            flash(f"✅ تم رفع {len(files)} ملف — اضغط تشغيل للمتابعة", "ok")

        return redirect(url_for("dashboard"))

    return render_template_string(UPLOAD_HTML)

# ---------- Files ----------
@app.route("/files/<int:pid>")
@user_required
def files(pid):
    s = get_user_session(session["token"])
    with db() as c:
        project = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, s["user_id"])).fetchone()
        if not project: return "غير موجود", 404
        file_list = c.execute("SELECT * FROM project_files WHERE project_id=? ORDER BY is_entry DESC, filename", (pid,)).fetchall()

    # أضف حجم كل ملف
    enriched = []
    folder = BOTS / project["folder"]
    for f in file_list:
        p = folder / f["filename"]
        sz = human_size(p.stat().st_size) if p.exists() else "—"
        enriched.append({"id":f["id"], "filename":f["filename"], "is_entry":f["is_entry"], "size":sz})

    return render_template_string(FILES_HTML, project=project, files=enriched)

@app.route("/files/<int:pid>/set-entry/<int:fid>")
@user_required
def set_entry(pid, fid):
    s = get_user_session(session["token"])
    with db() as c:
        project = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, s["user_id"])).fetchone()
        if not project: return "غير موجود", 404
        file_row = c.execute("SELECT * FROM project_files WHERE id=? AND project_id=?", (fid, pid)).fetchone()
        if not file_row: return "غير موجود", 404
        c.execute("UPDATE project_files SET is_entry=0 WHERE project_id=?", (pid,))
        c.execute("UPDATE project_files SET is_entry=1 WHERE id=?", (fid,))
        c.execute("UPDATE projects SET entry=? WHERE id=?", (file_row["filename"], pid))
    flash(f"⭐ تم تعيين '{file_row['filename']}' كملف تشغيل", "ok")
    return redirect(url_for("files", pid=pid))

@app.route("/files/<int:pid>/delete/<int:fid>")
@user_required
def delete_file(pid, fid):
    s = get_user_session(session["token"])
    with db() as c:
        project = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, s["user_id"])).fetchone()
        if not project: return "غير موجود", 404
        file_row = c.execute("SELECT * FROM project_files WHERE id=? AND project_id=?", (fid, pid)).fetchone()
        if not file_row: return "غير موجود", 404
        p = BOTS / project["folder"] / file_row["filename"]
        try: p.unlink()
        except: pass
        c.execute("DELETE FROM project_files WHERE id=?", (fid,))
        if file_row["is_entry"]:
            c.execute("UPDATE projects SET entry=NULL WHERE id=?", (pid,))
    flash("🗑 تم الحذف", "ok")
    return redirect(url_for("files", pid=pid))

# ---------- Project controls ----------
@app.route("/start/<int:pid>")
@user_required
def start(pid):
    s = get_user_session(session["token"])
    ok, m = start_project(s["user_id"], pid)
    flash(m, "ok" if ok else "err")
    return redirect(url_for("dashboard"))

@app.route("/stop/<int:pid>")
@user_required
def stop(pid):
    s = get_user_session(session["token"])
    ok, m = stop_project(s["user_id"], pid)
    flash(m, "ok" if ok else "err")
    return redirect(url_for("dashboard"))

@app.route("/restart/<int:pid>")
@user_required
def restart(pid):
    s = get_user_session(session["token"])
    stop_project(s["user_id"], pid); time.sleep(1)
    ok, m = start_project(s["user_id"], pid)
    flash("🔄 " + m, "ok" if ok else "err")
    return redirect(url_for("dashboard"))

@app.route("/logs/<int:pid>")
@user_required
def logs(pid):
    s = get_user_session(session["token"])
    with db() as c:
        project = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, s["user_id"])).fetchone()
    if not project: return "غير موجود", 404
    return render_template_string(LOGS_HTML, project=project, log=read_log(pid))

@app.route("/delete/<int:pid>")
@user_required
def delete_project(pid):
    s = get_user_session(session["token"])
    stop_project(s["user_id"], pid)
    with db() as c:
        row = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, s["user_id"])).fetchone()
        if row:
            c.execute("DELETE FROM project_files WHERE project_id=?", (pid,))
            c.execute("DELETE FROM projects WHERE id=?", (pid,))
    if row: shutil.rmtree(BOTS / row["folder"], ignore_errors=True)
    flash("🗑 تم حذف المشروع", "ok")
    return redirect(url_for("dashboard"))

# ---------- Health ----------
@app.route("/health")
def health(): return {"ok": True, "site": SITE_NAME}

# ================= MAIN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log.info(f"🌐 {SITE_NAME} — port {port}")
    log.info(f"👑 Admin master URL: /{ADMIN_SECRET_PATH}")
    log.info(f"👑 Admin login:      /admin-login ({ADMIN_USER})")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)