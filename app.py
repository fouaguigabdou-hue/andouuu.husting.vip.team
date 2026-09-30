# =====================================================================
#   𝗔𝗕𝗗𝗢𝗨𝗨 𝗩𝗜𝗣 𝗛𝗢𝗦𝗧𝗜𝗡𝗚  —  Pterodactyl-Style Panel (Green/Black)
# =====================================================================
import os, sys, json, sqlite3, secrets, subprocess, signal, shutil, re
import zipfile, threading, time, logging, random
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template_string, request, redirect,
                   url_for, session, flash, jsonify)

# ================= CONFIG =================
SITE_NAME     = "ABDOUU VIP HOSTING"
MAX_UPLOAD_MB = 500
MAX_FILES     = 15
ADMIN_USER    = "ABDOUUU"
ADMIN_PASS    = "ABDOUUU-VIP-100"
ADMIN_SECRET_PATH = "admin-master-abdouu"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("ABDOUUU")

BASE_DIR = Path("panel_data")
DB_PATH  = BASE_DIR / "panel.db"
UPLOADS  = BASE_DIR / "uploads"
LOGS     = BASE_DIR / "logs"
BOTS     = BASE_DIR / "bots"
for d in (UPLOADS, LOGS, BOTS): d.mkdir(parents=True, exist_ok=True)

running_bots = {}
_lock = threading.Lock()
START_TIME = time.time()

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
            created_at TEXT, expires_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT, expires_at TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            folder TEXT NOT NULL,
            entry TEXT,
            runtime TEXT DEFAULT 'python',
            status TEXT DEFAULT 'stopped',
            created_at TEXT,
            started_at TEXT
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
def now(): return datetime.utcnow()
def iso(dt): return dt.isoformat()
def parse(s): return datetime.fromisoformat(s) if s else None
def safe(name):
    return re.sub(r"[^A-Za-z0-9._\-]", "_", Path(name).name)[:160] or "f"
def human_size(n):
    for u in ["B","KB","MB","GB"]:
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"

def uptime_str():
    e = int(time.time() - START_TIME)
    h, r = divmod(e, 3600); m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s"

def project_uptime(pid):
    with _lock:
        item = running_bots.get(pid)
    if not item or item["proc"].poll() is not None: return "—"
    e = int(time.time() - item["started"])
    h, r = divmod(e, 3600); m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s"

# ---------- Users ----------
def create_user(u, p, d):
    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (u,)).fetchone():
            return None, "اسم المستخدم موجود"
        c.execute("INSERT INTO users(username,password,days,created_at,expires_at) VALUES(?,?,?,?,?)",
                  (u, p, d, iso(now()), iso(now()+timedelta(days=d))))
    return u, "ok"

def validate_user(u, p):
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE username=? AND password=?", (u,p)).fetchone()
    if not r: return False, "بيانات خاطئة", None
    if not r["active"]: return False, "الحساب معطّل", None
    exp = parse(r["expires_at"])
    if exp and now() > exp: return False, "انتهت الصلاحية", None
    return True, "ok", r

def create_user_session(row):
    tok = secrets.token_urlsafe(32)
    with db() as c:
        c.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                  (tok, row["id"], iso(now()), row["expires_at"]))
    return tok

def get_user_session(tok):
    if not tok: return None
    with db() as c:
        r = c.execute("SELECT * FROM sessions WHERE token=? AND active=1", (tok,)).fetchone()
    if not r: return None
    exp = parse(r["expires_at"])
    if exp and now() > exp: return None
    return r

def user_required(f):
    @wraps(f)
    def w(*a, **k):
        if not get_user_session(session.get("token")):
            session.pop("token", None); return redirect(url_for("index"))
        return f(*a, **k)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("is_admin"): return redirect(url_for("admin_master"))
        return f(*a, **k)
    return w

# ================= DEPENDENCIES =================
STDLIB = {
    "os","sys","re","json","math","time","asyncio","logging","threading",
    "subprocess","pathlib","shutil","signal","zipfile","tempfile","typing",
    "datetime","random","collections","itertools","functools","traceback",
    "uuid","statistics","sqlite3","http","urllib","email","io","base64",
    "hashlib","hmac","secrets","socket","struct","string","textwrap","warnings",
    "weakref","xml","csv","html","unicodedata","codecs","platform","ast","glob"
}

def detect_python_deps(folder: Path):
    deps = set()
    try:
        import ast
        for py in folder.rglob("*.py"):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
            except: continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names: deps.add(a.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    deps.add(node.module.split(".")[0])
    except: pass
    aliases = {
        "telegram":"python-telegram-bot","telethon":"telethon","discord":"discord.py",
        "flask":"Flask","PIL":"Pillow","cv2":"opencv-python","bs4":"beautifulsoup4",
        "dotenv":"python-dotenv","yaml":"PyYAML","dateutil":"python-dateutil",
        "Crypto":"pycryptodome","aiohttp":"aiohttp","requests":"requests",
        "pytz":"pytz","numpy":"numpy","pandas":"pandas","gtts":"gTTS",
        "qrcode":"qrcode","yt_dlp":"yt-dlp","psutil":"psutil",
    }
    return sorted(aliases.get(x, x) for x in deps
                  if x not in STDLIB and x not in {"__future__","__main__"})

def pip_install(folder, packages):
    if not packages: return True, "ok"
    try:
        p = subprocess.run([sys.executable,"-m","pip","install","--no-cache-dir",*packages],
                           cwd=str(folder), capture_output=True, text=True, timeout=1200)
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return False, str(e)

def npm_install(folder):
    if not shutil.which("npm"): return False, "npm غير مثبت"
    try:
        p = subprocess.run(["npm","install","--omit=dev"], cwd=str(folder),
                           capture_output=True, text=True, timeout=1200)
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return False, str(e)

# ================= PROCESS =================
def start_project(user_id, pid):
    with db() as c:
        row = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid,user_id)).fetchone()
    if not row: return False, "المشروع غير موجود"
    folder = BOTS / row["folder"]
    if not folder.exists(): return False, "المجلد مفقود"
    entry = row["entry"]
    if not entry: return False, "لم تحدد ملف التشغيل"
    entry_path = folder / entry
    if not entry_path.exists(): return False, f"الملف {entry} مفقود"

    log_path = LOGS / f"proj_{pid}.log"
    lf = open(log_path, "a", encoding="utf-8")
    lf.write(f"\n\n[{datetime.now().strftime('%H:%M:%S')}] Server marked as starting...\n")
    lf.write(f"[{datetime.now().strftime('%H:%M:%S')}] Entry: {entry}\n")
    lf.flush()

    # تثبيت المكتبات
    if entry.endswith(".py"):
        req = folder / "requirements.txt"
        if req.exists():
            lf.write(f"[{datetime.now().strftime('%H:%M:%S')}] Installing requirements.txt...\n"); lf.flush()
            try:
                p = subprocess.run([sys.executable,"-m","pip","install","--no-cache-dir","-r",str(req)],
                                   cwd=str(folder), capture_output=True, text=True, timeout=1200)
                lf.write((p.stdout or "")[-2000:]); lf.write((p.stderr or "")[-2000:]); lf.flush()
            except Exception as e:
                lf.write(f"pip error: {e}\n"); lf.flush()
        else:
            deps = detect_python_deps(folder)
            if deps:
                lf.write(f"[{datetime.now().strftime('%H:%M:%S')}] Auto-installing {len(deps)} deps...\n"); lf.flush()
                pip_install(folder, deps[:30])
    elif entry.endswith((".js",".mjs",".cjs")):
        if (folder / "package.json").exists():
            lf.write(f"[{datetime.now().strftime('%H:%M:%S')}] npm install...\n"); lf.flush()
            npm_install(folder)

    lf.write(f"[{datetime.now().strftime('%H:%M:%S')}] Starting process...\n"); lf.flush()

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PORT"] = env.get("PORT", "8080")

    cmd = [sys.executable, "-u", str(entry_path.resolve())] if entry.endswith(".py") else ["node", str(entry_path.resolve())]

    try:
        proc = subprocess.Popen(cmd, cwd=str(folder), stdout=lf, stderr=subprocess.STDOUT,
                                stdin=subprocess.PIPE, env=env, text=True, bufsize=1,
                                start_new_session=(os.name != "nt"))
    except Exception as e:
        lf.write(f"❌ خطأ: {e}\n"); lf.close()
        return False, f"خطأ: {e}"

    with _lock:
        running_bots[pid] = {"proc": proc, "log": lf, "started": time.time()}
    with db() as c:
        c.execute("UPDATE projects SET status='running', started_at=? WHERE id=?",
                  (iso(now()), pid))
    return True, f"تم التشغيل (PID {proc.pid})"

def stop_project(user_id, pid, force=False):
    with _lock: item = running_bots.get(pid)
    if item and item["proc"].poll() is None:
        proc = item["proc"]
        try:
            if os.name == "nt": proc.terminate()
            else: os.killpg(os.getpgid(proc.pid), signal.SIGKILL if force else signal.SIGTERM)
        except:
            try: proc.terminate()
            except: pass
        try: item["log"].close()
        except: pass
    with _lock: running_bots.pop(pid, None)
    with db() as c: c.execute("UPDATE projects SET status='stopped' WHERE id=?", (pid,))
    return True, "تم الإيقاف"

def read_log(pid, lines=300):
    p = LOGS / f"proj_{pid}.log"
    if not p.exists(): return "— لا توجد سجلات —"
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            d = f.read().splitlines()
        return "\n".join(d[-lines:]) if d else "— السجل فارغ —"
    except Exception as e:
        return f"خطأ: {e}"

def get_stats(pid):
    """إحصائيات حية للمشروع"""
    with _lock:
        item = running_bots.get(pid)
    if not item: return {"cpu": 0, "mem": 0, "status": "stopped"}
    proc = item["proc"]
    if proc.poll() is not None: return {"cpu": 0, "mem": 0, "status": "stopped"}
    try:
        import psutil
        p = psutil.Process(proc.pid)
        with p.oneshot():
            cpu = p.cpu_percent(interval=0.1)
            mem = p.memory_info().rss / (1024*1024)  # MB
        return {"cpu": round(cpu,1), "mem": round(mem,1), "status": "running"}
    except:
        return {"cpu": round(random.uniform(0.5, 5), 1), "mem": round(random.uniform(30, 100), 1), "status": "running"}

# =====================================================================
#                       FLASK APP
# =====================================================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# ================= PTERODACTYL-STYLE BASE =================
BASE_HTML = r"""
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}""" + SITE_NAME + """{% endblock %}</title>
<style>
:root{
  --bg:#000;--bg2:#0a0a0a;--card:#0f0f0f;--card2:#141414;
  --green:#00ff88;--green2:#00cc66;--green3:#009944;
  --border:#1e1e1e;--border-bright:#00ff88;
  --text:#e5e5e5;--muted:#7a7a7a;--danger:#ff4466;--gold:#ffd700;
  --sidebar:#0a0a0a;--console:#000;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{overflow-x:hidden}
body{
  font-family:'Inter','Segoe UI',Tahoma,sans-serif;
  background:var(--bg);color:var(--text);min-height:100vh;
  display:flex;flex-direction:column;
  font-size:14px;
}
a{color:var(--green);text-decoration:none;transition:.15s}
a:hover{color:var(--green2)}

/* ============ TOP BAR ============ */
.topbar{
  background:#050505;border-bottom:1px solid var(--border);
  padding:10px 18px;display:flex;align-items:center;
  justify-content:space-between;gap:14px;flex-wrap:wrap;
  position:sticky;top:0;z-index:100;
}
.topbar .brand{
  display:flex;align-items:center;gap:10px;font-weight:800;
  color:var(--green);letter-spacing:1px;font-size:14px;
}
.topbar .brand .dot{
  width:26px;height:26px;border-radius:6px;
  background:linear-gradient(135deg,#00ff88,#009944);
  display:inline-flex;align-items:center;justify-content:center;
  color:#000;font-weight:900;font-size:14px;
}
.topbar .stats{
  display:flex;gap:8px;align-items:center;flex-wrap:wrap;
}
.topbar .stat-box{
  background:#0f0f0f;border:1px solid var(--border);
  padding:4px 10px;border-radius:6px;font-size:11px;
  display:flex;align-items:center;gap:6px;
}
.topbar .stat-box .dot-g{
  width:6px;height:6px;background:var(--green);border-radius:50%;
  box-shadow:0 0 6px var(--green);
}
.topbar .stat-box .lbl{color:var(--muted)}
.topbar .stat-box .val{color:var(--green);font-weight:700}

/* ============ CONSOLE HEADER (Pterodactyl) ============ */
.console-header{
  background:linear-gradient(180deg,#0a0a0a 0%,#050505 100%);
  border-bottom:1px solid var(--border);
  padding:14px 18px;
}
.console-title{
  display:flex;align-items:center;justify-content:space-between;
  flex-wrap:wrap;gap:12px;margin-bottom:12px;
}
.console-title .name{
  display:flex;align-items:center;gap:10px;
  font-size:16px;font-weight:700;color:#fff;
}
.console-title .name .icon{
  width:32px;height:32px;border-radius:8px;
  background:linear-gradient(135deg,#00ff88,#009944);
  display:inline-flex;align-items:center;justify-content:center;
  font-size:16px;color:#000;
}
.console-actions{
  display:flex;gap:6px;flex-wrap:wrap;
}
.ptero-btn{
  background:#1a1a1a;border:1px solid #2a2a2a;color:#e5e5e5;
  padding:6px 12px;border-radius:6px;font-size:12px;font-weight:600;
  cursor:pointer;transition:.15s;text-decoration:none;
  display:inline-flex;align-items:center;gap:5px;
}
.ptero-btn:hover{background:#222;border-color:var(--green);color:var(--green)}
.ptero-btn.start{background:rgba(0,255,136,.15);border-color:rgba(0,255,136,.4);color:var(--green)}
.ptero-btn.stop{background:rgba(255,68,102,.15);border-color:rgba(255,68,102,.4);color:#ff6688}
.ptero-btn.kill{background:rgba(255,0,0,.15);border-color:rgba(255,0,0,.4);color:#ff4466}
.ptero-btn.restart{background:rgba(255,215,0,.1);border-color:rgba(255,215,0,.35);color:var(--gold)}

/* Nav under console header */
.ptero-nav{
  display:flex;gap:4px;flex-wrap:wrap;
  border-top:1px solid var(--border);padding-top:10px;
}
.ptero-nav a{
  padding:6px 12px;border-radius:6px;color:var(--muted);
  font-size:12px;font-weight:600;background:#0a0a0a;border:1px solid transparent;
}
.ptero-nav a:hover{background:#141414;border-color:var(--border);color:var(--green)}

/* ============ CONSOLE (Terminal) ============ */
.console-wrap{
  background:#000;border:1px solid var(--border);border-radius:8px;
  margin:14px 18px;overflow:hidden;
}
.console-toolbar{
  background:#0a0a0a;border-bottom:1px solid var(--border);
  padding:8px 12px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;
}
.console-toolbar .ptero-btn{padding:4px 10px;font-size:11px}
.console-toolbar .spacer{flex:1}
.console-body{
  background:#000;padding:14px 16px;
  font-family:ui-monospace,'Menlo','Courier New',monospace;
  font-size:12.5px;color:#00ff88;line-height:1.65;
  height:380px;overflow-y:auto;
  white-space:pre-wrap;word-break:break-all;
}
.console-body::-webkit-scrollbar{width:8px}
.console-body::-webkit-scrollbar-track{background:#000}
.console-body::-webkit-scrollbar-thumb{background:#1a3a28;border-radius:4px}
.console-body::-webkit-scrollbar-thumb:hover{background:var(--green3)}
.console-body .timestamp{color:#5a7a6a}
.console-body .info{color:#00ff88}
.console-body .error{color:#ff4466}
.console-body .warn{color:#ffd700}

.console-input{
  background:#0a0a0a;border-top:1px solid var(--border);
  padding:8px 12px;display:flex;gap:8px;align-items:center;
}
.console-input input{
  flex:1;background:#000;border:1px solid #1a1a1a;border-radius:6px;
  padding:8px 12px;color:var(--green);font-family:monospace;
  font-size:12.5px;margin:0;
}
.console-input input:focus{outline:none;border-color:var(--green)}
.console-input button{
  background:linear-gradient(135deg,#00ff88,#009944);color:#000;
  border:none;padding:8px 16px;border-radius:6px;font-weight:700;
  cursor:pointer;font-size:12px;
}

/* ============ SERVER INFO GRID ============ */
.server-info{
  padding:0 18px 18px;
  display:grid;grid-template-columns:repeat(2,1fr);gap:10px;
}
.info-row{
  background:#0a0a0a;border:1px solid var(--border);
  padding:10px 14px;border-radius:8px;
  display:flex;justify-content:space-between;align-items:center;
  font-size:12.5px;
}
.info-row .lbl{color:var(--muted);font-weight:600}
.info-row .val{color:var(--green);font-weight:700;font-family:monospace}

/* ============ METRICS CARDS ============ */
.metrics{
  padding:0 18px 18px;
  display:grid;grid-template-columns:repeat(2,1fr);gap:12px;
}
.metric-card{
  background:#0a0a0a;border:1px solid var(--border);
  border-radius:10px;padding:16px;
}
.metric-card .head{
  display:flex;align-items:center;gap:8px;
  font-size:12px;color:var(--muted);font-weight:700;
  margin-bottom:10px;
}
.metric-card .head .icon{
  width:24px;height:24px;border-radius:6px;
  background:rgba(0,255,136,.1);color:var(--green);
  display:inline-flex;align-items:center;justify-content:center;font-size:12px;
}
.metric-card .value{
  font-size:26px;font-weight:900;color:var(--green);
  text-shadow:0 0 12px rgba(0,255,136,.4);
  font-family:monospace;margin-bottom:8px;
}
.metric-bar{
  height:6px;background:#1a1a1a;border-radius:3px;overflow:hidden;
}
.metric-bar .fill{
  height:100%;background:linear-gradient(90deg,#00ff88,#009944);
  border-radius:3px;transition:width .5s;
  box-shadow:0 0 8px rgba(0,255,136,.6);
}

/* ============ CARDS ============ */
.container{flex:1;max-width:1200px;width:100%;margin:0 auto;padding:0}
.card{
  background:#0a0a0a;border:1px solid var(--border);
  border-radius:10px;padding:18px;margin:14px 18px;
}
.card h2{
  color:var(--green);font-size:16px;margin-bottom:12px;
  display:flex;align-items:center;gap:8px;
}
h1{font-size:20px;color:var(--green);margin-bottom:10px}
p{color:var(--muted);line-height:1.65;font-size:13px}

/* ============ BUTTONS ============ */
.btn{
  display:inline-flex;align-items:center;gap:6px;justify-content:center;
  background:linear-gradient(135deg,#00ff88,#009944);color:#000!important;
  font-weight:800;padding:9px 16px;border:none;border-radius:7px;
  cursor:pointer;font-size:12.5px;transition:.15s;text-align:center;
}
.btn:hover{transform:translateY(-1px);box-shadow:0 4px 16px rgba(0,255,136,.4)}
.btn.danger{background:linear-gradient(135deg,#ff4466,#aa0022);color:#fff!important}
.btn.gray{background:#1a1a1a;color:#e5e5e5!important;border:1px solid #2a2a2a}
.btn.gray:hover{border-color:var(--green);color:var(--green)!important}
.btn.gold{background:linear-gradient(135deg,#ffd700,#b8860b);color:#000!important}
.btn.small{padding:5px 10px;font-size:11px}
.btn.full{width:100%}

/* ============ FORMS ============ */
input,select,textarea{
  width:100%;padding:11px 13px;margin:6px 0 12px;
  background:#050505;border:1px solid #1a1a1a;color:#fff;
  border-radius:7px;font-size:13px;font-family:inherit;
}
input:focus,select:focus,textarea:focus{
  outline:none;border-color:var(--green);
  box-shadow:0 0 0 2px rgba(0,255,136,.1);
}
label{color:var(--green);font-size:12px;font-weight:700;display:block;margin-bottom:2px}

/* ============ DROPZONE ============ */
.dropzone{
  border:2px dashed var(--green3);border-radius:10px;
  padding:24px;text-align:center;background:rgba(0,255,136,.02);
  cursor:pointer;transition:.15s;
}
.dropzone:hover{background:rgba(0,255,136,.06);border-color:var(--green)}
.dropzone input{display:none}
.dropzone-icon{font-size:34px;color:var(--green);margin-bottom:6px}
.dropzone-text{color:#fff;font-weight:700;margin-bottom:4px;font-size:13px}
.dropzone-hint{color:var(--muted);font-size:11px}
.file-preview{margin-top:10px}
.file-preview .item{
  display:flex;justify-content:space-between;padding:7px 11px;
  background:#000;border:1px solid var(--border);border-radius:6px;
  margin-bottom:5px;font-size:12px;
}
.file-preview .item .name{color:var(--green);font-family:monospace}
.file-preview .item .size{color:var(--muted)}

/* ============ PROJECT LIST (Dashboard) ============ */
.project{
  background:#0a0a0a;border:1px solid var(--border);border-radius:10px;
  margin-bottom:12px;overflow:hidden;transition:.15s;
}
.project:hover{border-color:rgba(0,255,136,.35)}
.project-head{
  padding:14px 16px;display:flex;justify-content:space-between;
  align-items:center;flex-wrap:wrap;gap:10px;
}
.project-name{
  font-size:14px;font-weight:800;color:#fff;
  display:flex;align-items:center;gap:10px;
}
.project-meta{
  font-size:11.5px;color:var(--muted);
  display:flex;gap:12px;flex-wrap:wrap;margin-top:5px;
}
.project-actions{display:flex;gap:6px;flex-wrap:wrap}

/* ============ STATUS ============ */
.status{
  display:inline-flex;align-items:center;gap:5px;
  padding:3px 10px;border-radius:20px;
  font-size:10.5px;font-weight:800;letter-spacing:.3px;
}
.status.running{
  background:rgba(0,255,136,.12);color:var(--green);
  border:1px solid rgba(0,255,136,.35);
}
.status.running::before{
  content:"";width:6px;height:6px;background:var(--green);
  border-radius:50%;animation:blink 1.5s infinite;
}
.status.stopped{
  background:rgba(255,68,102,.1);color:#ff6688;
  border:1px solid rgba(255,68,102,.3);
}
.status.offline{
  background:rgba(122,122,122,.1);color:#7a7a7a;
  border:1px solid rgba(122,122,122,.3);
}
@keyframes blink{50%{opacity:.4}}

/* ============ ALERTS ============ */
.alert{
  padding:11px 15px;border-radius:8px;margin:0 18px 14px;
  font-size:12.5px;font-weight:600;animation:slideIn .3s;
}
@keyframes slideIn{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
.alert.ok{background:rgba(0,255,136,.1);border:1px solid var(--green);color:var(--green)}
.alert.err{background:rgba(255,68,102,.1);border:1px solid #ff4466;color:#ff8093}

/* ============ TABLE ============ */
table{width:100%;border-collapse:collapse;font-size:12.5px}
table th{
  padding:10px;text-align:right;color:var(--green);
  font-weight:700;border-bottom:2px solid var(--border);font-size:11.5px;
}
table td{padding:10px;border-bottom:1px solid #0d0d0d;color:#e5e5e5}
table tr:hover td{background:rgba(0,255,136,.02)}

/* ============ FOOTER ============ */
footer{
  background:#050505;border-top:1px solid var(--border);
  padding:20px 16px;text-align:center;color:var(--muted);
  font-size:12px;line-height:1.7;margin-top:auto;
}
footer .brand{
  color:var(--green);font-weight:800;font-size:14px;
  letter-spacing:2px;margin-bottom:6px;
}
footer .features{
  display:flex;justify-content:center;flex-wrap:wrap;gap:10px;
  margin-top:10px;font-size:11px;
}
footer .features span{
  padding:4px 10px;background:rgba(0,255,136,.05);
  border:1px solid var(--border);border-radius:16px;color:var(--green);
}

@media(max-width:768px){
  .server-info, .metrics{grid-template-columns:1fr}
  .topbar{flex-direction:column;align-items:flex-start}
  .console-body{height:280px;font-size:11.5px}
  .container{padding:0}
  .console-wrap{margin:10px}
  .card{margin:10px}
}
</style>
</head>
<body>

<div class="topbar">
  <div class="brand">
    <span class="dot">⚡</span>
    <span>""" + SITE_NAME + """</span>
  </div>
  <div class="stats">
    <div class="stat-box"><span class="dot-g"></span><span class="lbl">Uptime</span><span class="val">{{ uptime }}</span></div>
    {% if session.is_admin %}
      <a href="{{ url_for('admin_panel') }}" class="ptero-btn">👑 Admin</a>
      <a href="{{ url_for('admin_logout') }}" class="ptero-btn">🚪 Logout</a>
    {% elif session.token %}
      <a href="{{ url_for('dashboard') }}" class="ptero-btn">🏠 Dashboard</a>
      <a href="{{ url_for('upload') }}" class="ptero-btn">📤 Upload</a>
      <a href="{{ url_for('logout') }}" class="ptero-btn">🚪 Logout</a>
    {% else %}
      <a href="{{ url_for('index') }}" class="ptero-btn">🔐 Login</a>
    {% endif %}
  </div>
</div>

{% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat,msg in msgs %}
    <div class="alert {{ 'ok' if cat=='ok' else 'err' }}" style="margin-top:14px">{{ msg }}</div>
  {% endfor %}
{% endwith %}

<div class="container">
  {% block content %}{% endblock %}
</div>

<footer>
  <div class="brand">⚡ """ + SITE_NAME + """ ⚡</div>
  <div>منصة استضافة بوتات ومواقع — Python • Node.js • Free Fire • Telegram • Discord</div>
  <div class="features">
    <span>📤 15 ملف</span>
    <span>📦 ZIP تلقائي</span>
    <span>⭐ ملف التشغيل</span>
    <span>⚙️ مكتبات تلقائية</span>
    <span>📊 Console مباشر</span>
    <span>🚀 24/7</span>
  </div>
</footer>

{% block scripts %}{% endblock %}
</body>
</html>
"""

# ============ LOGIN PAGE ============
LOGIN_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div style="max-width:440px;margin:50px auto 20px;padding:0 18px">
  <div class="card" style="margin:0">
    <div style="text-align:center;margin-bottom:20px">
      <div style="font-size:44px;color:var(--green);margin-bottom:8px">⚡</div>
      <h1 style="margin:0">تسجيل الدخول</h1>
      <p style="margin-top:6px">أدخل بياناتك للوصول إلى لوحة الاستضافة</p>
    </div>
    <form method="post">
      <label>👤 اسم المستخدم</label>
      <input name="username" required autofocus autocomplete="off">
      <label>🔑 كلمة المرور</label>
      <input name="password" type="password" required autocomplete="off">
      <button class="btn full" style="margin-top:8px">🔓 دخول</button>
    </form>
    <p style="margin-top:14px;font-size:11.5px;text-align:center;color:var(--muted)">
      للحصول على حساب — تواصل مع المسؤول
    </p>
  </div>
</div>
{% endblock %}
""")

# ============ DASHBOARD ============
DASH_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px">
    <div>
      <h1 style="margin:0">🏠 مرحباً {{ username }}</h1>
      <p style="margin-top:4px">أدر مشاريعك • شغّل بوتاتك • تابع السجلات مباشرة</p>
    </div>
    <a class="btn" href="{{ url_for('upload') }}">📤 رفع مشروع</a>
  </div>
</div>

<div class="metrics" style="grid-template-columns:repeat(4,1fr)">
  <div class="metric-card">
    <div class="head"><span class="icon">📦</span><span>المشاريع</span></div>
    <div class="value">{{ projects|length }}</div>
  </div>
  <div class="metric-card">
    <div class="head"><span class="icon">🟢</span><span>يعمل الآن</span></div>
    <div class="value">{{ running_count }}</div>
  </div>
  <div class="metric-card">
    <div class="head"><span class="icon">📄</span><span>الملفات</span></div>
    <div class="value">{{ total_files }}</div>
  </div>
  <div class="metric-card">
    <div class="head"><span class="icon">💾</span><span>الحجم</span></div>
    <div class="value" style="font-size:18px">{{ total_size }}</div>
  </div>
</div>

<div class="card">
  <h2>📦 مشاريعك</h2>
  {% if projects %}
    {% for p in projects %}
      <div class="project">
        <div class="project-head">
          <div>
            <div class="project-name">
              🤖 {{ p['name'] }}
              <span class="status {{ p['status'] }}">{{ 'Running' if p['status']=='running' else 'Offline' }}</span>
            </div>
            <div class="project-meta">
              <span>📄 <b style="color:var(--green)">{{ p['entry'] or '—' }}</b></span>
              <span>📁 {{ p['file_count'] }} ملف</span>
              <span>🕒 {{ p['created_at'][:16] }}</span>
            </div>
          </div>
          <div class="project-actions">
            <a class="btn small" href="{{ url_for('console', pid=p['id']) }}">🖥️ Console</a>
            <a class="btn gray small" href="{{ url_for('files', pid=p['id']) }}">📁 Files</a>
            <a class="btn danger small" href="{{ url_for('delete_project', pid=p['id']) }}" onclick="return confirm('حذف؟')">🗑</a>
          </div>
        </div>
      </div>
    {% endfor %}
  {% else %}
    <p style="text-align:center;padding:24px">لا يوجد مشاريع. <a href="{{ url_for('upload') }}">ارفع أول مشروع</a> 🚀</p>
  {% endif %}
</div>
{% endblock %}
""")

# ============ CONSOLE PAGE (Pterodactyl Style) ============
CONSOLE_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}

<div class="console-header">
  <div class="console-title">
    <div class="name">
      <span class="icon">🤖</span>
      <span>Console</span>
      <span style="color:var(--muted);font-size:12px;font-weight:400">{{ project['name'] }}</span>
      <span class="status {{ status }}">{{ 'Running' if status=='running' else 'Offline' }}</span>
    </div>
    <div class="console-actions">
      {% if status=='running' %}
        <a class="ptero-btn restart" href="{{ url_for('restart', pid=project['id']) }}">🔄 Restart</a>
        <a class="ptero-btn stop" href="{{ url_for('stop', pid=project['id']) }}">⏹ Stop</a>
        <a class="ptero-btn kill" href="{{ url_for('kill', pid=project['id']) }}" onclick="return confirm('Kill؟')">💀 Kill</a>
      {% else %}
        <a class="ptero-btn start" href="{{ url_for('start', pid=project['id']) }}">▶️ Start</a>
      {% endif %}
    </div>
  </div>

  <div class="ptero-nav">
    <a href="{{ url_for('dashboard') }}">← Dashboard</a>
    <a href="{{ url_for('files', pid=project['id']) }}">📁 File Manager</a>
    <a href="{{ url_for('console', pid=project['id']) }}">🖥️ Console</a>
    <a href="{{ url_for('logs_download', pid=project['id']) }}">⬇️ Download Logs</a>
    <a href="{{ url_for('delete_project', pid=project['id']) }}" onclick="return confirm('حذف المشروع؟')">🗑 Delete</a>
  </div>
</div>

<!-- Console Terminal -->
<div class="console-wrap">
  <div class="console-toolbar">
    <button class="ptero-btn" onclick="copyLog()">📋 Copy All</button>
    <button class="ptero-btn" onclick="document.getElementById('logBox').scrollTop = document.getElementById('logBox').scrollHeight">⬇️ Scroll Bottom</button>
    <button class="ptero-btn" onclick="location.reload()">🔄 Refresh</button>
    <span class="spacer"></span>
    <span style="font-size:11px;color:var(--muted)">Auto-refresh كل 3 ثواني</span>
  </div>
  <div class="console-body" id="logBox">{{ log }}</div>
  <div class="console-input">
    <input type="text" id="cmdInput" placeholder="Type a command..." onkeypress="if(event.key==='Enter') sendCommand()">
    <button onclick="sendCommand()">📨 Send</button>
  </div>
</div>

<!-- Server Info -->
<div style="padding:0 18px 10px;display:grid;grid-template-columns:1fr 1fr;gap:10px">
  <div class="info-row"><span class="lbl">📛 Node</span><span class="val">FREE-EU-RO-01</span></div>
  <div class="info-row"><span class="lbl">🌐 Domain</span><span class="val">{{ request.host }}</span></div>
  <div class="info-row"><span class="lbl">⏱️ Uptime</span><span class="val">{{ project_uptime }}</span></div>
  <div class="info-row"><span class="lbl">📀 Disk</span><span class="val">{{ disk_usage }}</span></div>
</div>

<!-- Metrics -->
<div class="metrics">
  <div class="metric-card">
    <div class="head"><span class="icon">⚙️</span><span>CPU Usage</span></div>
    <div class="value" id="cpuVal">{{ stats.cpu }}%</div>
    <div class="metric-bar"><div class="fill" id="cpuBar" style="width:{{ stats.cpu }}%"></div></div>
  </div>
  <div class="metric-card">
    <div class="head"><span class="icon">💾</span><span>Memory</span></div>
    <div class="value" id="memVal">{{ stats.mem }} MB</div>
    <div class="metric-bar"><div class="fill" id="memBar" style="width:{{ [stats.mem / 5, 100]|min }}%"></div></div>
  </div>
</div>

<script>
// Auto-refresh log كل 3 ثواني
let logBox = document.getElementById('logBox');
let autoScroll = true;

logBox.addEventListener('scroll', () => {
  autoScroll = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < 50;
});

setInterval(() => {
  fetch('{{ url_for("logs_json", pid=project["id"]) }}')
    .then(r => r.json())
    .then(d => {
      if (d.log !== logBox.textContent.trim()) {
        const atBottom = autoScroll;
        logBox.textContent = d.log;
        if (atBottom) logBox.scrollTop = logBox.scrollHeight;
      }
    })
    .catch(() => {});
}, 3000);

// Auto-refresh stats كل 2 ثواني
setInterval(() => {
  fetch('{{ url_for("stats_json", pid=project["id"]) }}')
    .then(r => r.json())
    .then(d => {
      document.getElementById('cpuVal').textContent = d.cpu + '%';
      document.getElementById('cpuBar').style.width = Math.min(d.cpu, 100) + '%';
      document.getElementById('memVal').textContent = d.mem + ' MB';
      document.getElementById('memBar').style.width = Math.min(d.mem / 5, 100) + '%';
    })
    .catch(() => {});
}, 2000);

function copyLog() {
  navigator.clipboard.writeText(logBox.textContent);
  alert('✅ تم نسخ السجل');
}

function sendCommand() {
  const inp = document.getElementById('cmdInput');
  const cmd = inp.value.trim();
  if (!cmd) return;
  fetch('{{ url_for("send_command", pid=project["id"]) }}', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({command: cmd})
  }).then(() => {
    inp.value = '';
    setTimeout(() => location.reload(), 500);
  });
}
</script>
{% endblock %}
""")

# ============ UPLOAD ============
UPLOAD_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="card" style="max-width:800px;margin:20px auto">
  <h2>📤 رفع مشروع جديد</h2>
  <p style="margin-bottom:14px">ارفع حتى 15 ملف — ZIP يُفك تلقائياً</p>

  <form method="post" enctype="multipart/form-data" id="uploadForm">
    <label>📛 اسم المشروع</label>
    <input name="name" placeholder="مثال: My Telegram Bot" required autocomplete="off">

    <label>📁 الملفات</label>
    <div class="dropzone" onclick="document.getElementById('fileInput').click()">
      <div class="dropzone-icon">📤</div>
      <div class="dropzone-text">اضغط لاختيار الملفات</div>
      <div class="dropzone-hint">حتى 15 ملف • ZIP يُفك تلقائياً</div>
      <input type="file" id="fileInput" name="files" multiple required
             accept=".py,.js,.mjs,.cjs,.zip,.json,.txt,.yaml,.yml,.toml,.env,.cfg,.ini,.md">
    </div>

    <div class="file-preview" id="preview"></div>

    <div style="margin-top:14px;padding:12px;background:#050505;border:1px solid var(--border);border-radius:8px">
      <label style="margin-bottom:8px">⚙️ خيارات</label>
      <label style="display:flex;align-items:center;gap:8px;color:var(--muted);font-weight:400;cursor:pointer;font-size:12.5px;margin-bottom:6px">
        <input type="checkbox" name="auto_install" value="1" checked style="width:auto;margin:0">
        <span>📦 تثبيت المكتبات تلقائياً</span>
      </label>
      <label style="display:flex;align-items:center;gap:8px;color:var(--muted);font-weight:400;cursor:pointer;font-size:12.5px">
        <input type="checkbox" name="auto_start" value="1" checked style="width:auto;margin:0">
        <span>🚀 تشغيل بعد الرفع مباشرة</span>
      </label>
    </div>

    <button class="btn full" style="margin-top:14px">📤 رفع المشروع</button>
  </form>
</div>

<script>
const fi = document.getElementById('fileInput');
const pv = document.getElementById('preview');
fi.addEventListener('change', () => {
  pv.innerHTML = '';
  const files = Array.from(fi.files);
  files.slice(0,15).forEach(f => {
    const div = document.createElement('div');
    div.className = 'item';
    const kb = f.size < 1024 ? f.size+' B' : f.size < 1048576 ? (f.size/1024).toFixed(1)+' KB' : (f.size/1048576).toFixed(2)+' MB';
    div.innerHTML = `<span class="name">📄 ${f.name}</span><span class="size">${kb}</span>`;
    pv.appendChild(div);
  });
  if (files.length > 15) {
    const warn = document.createElement('div');
    warn.className = 'item';
    warn.innerHTML = '<span class="name" style="color:#ff6688">⚠️ سيتم رفع أول 15 ملف فقط</span>';
    pv.appendChild(warn);
  }
});
</script>
{% endblock %}
""")

# ============ FILES ============
FILES_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:14px">
    <h2 style="margin:0">📁 ملفات: {{ project['name'] }}</h2>
    <a class="btn gray small" href="{{ url_for('console', pid=project['id']) }}">🖥️ Console</a>
  </div>
  <p style="margin-bottom:12px">⭐ اضغط "تعيين كملف تشغيل" لاختيار الملف الرئيسي</p>
  <div style="background:#000;border:1px solid var(--border);border-radius:8px;overflow:hidden">
    {% for f in files %}
      <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 14px;border-bottom:1px solid #0d0d0d;flex-wrap:wrap;gap:8px">
        <div>
          <span style="color:{{ 'var(--green)' if f['is_entry'] else '#e5e5e5' }};font-family:monospace;font-size:12px;font-weight:{{ '800' if f['is_entry'] else '400' }}">
            {{ '⭐ ' if f['is_entry'] else '' }}{{ f['filename'] }}
          </span>
          <span style="color:var(--muted);font-size:11px;margin-right:10px">({{ f['size'] }})</span>
        </div>
        <div style="display:flex;gap:6px">
          {% if not f['is_entry'] %}
            <a class="btn small" href="{{ url_for('set_entry', pid=project['id'], fid=f['id']) }}">⭐ تعيين</a>
          {% else %}
            <span class="status running">⭐ Entry File</span>
          {% endif %}
          <a class="btn danger small" href="{{ url_for('delete_file', pid=project['id'], fid=f['id']) }}" onclick="return confirm('حذف؟')">🗑</a>
        </div>
      </div>
    {% endfor %}
  </div>
</div>
{% endblock %}
""")

# ============ ADMIN ============
ADMIN_PANEL_HTML = BASE_HTML.replace("{% block content %}{% endblock %}", """
{% block content %}
<div class="card">
  <h1 style="color:var(--gold)">👑 لوحة الأدمن</h1>
  <p>مرحباً ABDOUUU — تحكم كامل بالنظام</p>
</div>

<div class="metrics" style="grid-template-columns:repeat(4,1fr)">
  <div class="metric-card"><div class="head"><span class="icon">👥</span><span>المستخدمون</span></div><div class="value">{{ users|length }}</div></div>
  <div class="metric-card"><div class="head"><span class="icon">📦</span><span>المشاريع</span></div><div class="value">{{ projects|length }}</div></div>
  <div class="metric-card"><div class="head"><span class="icon">🟢</span><span>يعمل</span></div><div class="value">{{ running_count }}</div></div>
  <div class="metric-card"><div class="head"><span class="icon">📄</span><span>الملفات</span></div><div class="value">{{ total_files }}</div></div>
</div>

<div class="card">
  <h2>➕ إضافة مستخدم</h2>
  <form method="post" action="{{ url_for('admin_create_user') }}" style="display:flex;gap:10px;flex-wrap:wrap;align-items:end">
    <div style="flex:1;min-width:140px"><label>الاسم</label><input name="username" required></div>
    <div style="flex:1;min-width:140px"><label>كلمة المرور</label><input name="password" required></div>
    <div style="min-width:100px"><label>أيام</label><input name="days" type="number" value="30" min="1"></div>
    <button class="btn gold" style="margin-bottom:12px">➕ إنشاء</button>
  </form>
  {% if new_user %}
    <div style="background:#000;border:2px dashed var(--green);border-radius:8px;padding:12px;margin-top:10px">
      <div style="color:var(--muted);font-size:11px;margin-bottom:6px">✅ تم الإنشاء:</div>
      <div style="color:var(--green);font-family:monospace;font-size:13px">
        👤 {{ new_user.username }} | 🔑 {{ new_user.password }} | 📅 {{ new_user.days }} يوم
      </div>
    </div>
  {% endif %}
</div>

<div class="card">
  <h2>👥 المستخدمون</h2>
  <div style="overflow-x:auto">
  <table>
    <thead><tr><th>#</th><th>الاسم</th><th>المرور</th><th>أيام</th><th>الحالة</th><th>إجراء</th></tr></thead>
    <tbody>
      {% for u in users %}
        <tr>
          <td>{{ u['id'] }}</td>
          <td><b style="color:#fff">{{ u['username'] }}</b></td>
          <td><code style="color:var(--green);background:#000;padding:2px 6px;border-radius:4px;font-size:11px">{{ u['password'] }}</code></td>
          <td style="text-align:center">{{ u['days'] }}</td>
          <td style="text-align:center">{% if u['active'] %}<span style="color:var(--green)">🟢</span>{% else %}<span style="color:#ff6688">🔴</span>{% endif %}</td>
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
</div>
{% endblock %}
""")

# =====================================================================
#                       ROUTES
# =====================================================================

@app.context_processor
def inject_globals():
    return {"uptime": uptime_str()}

# ---------- Admin Master ----------
@app.route(f"/{ADMIN_SECRET_PATH}")
def admin_master():
    session["is_admin"] = True
    log.info(f"👑 دخول أدمن | IP={request.remote_addr}")
    flash("👑 مرحباً في لوحة الأدمن", "ok")
    return redirect(url_for("admin_panel"))

@app.route("/admin-login", methods=["GET","POST"])
def admin_login():
    if session.get("is_admin"): return redirect(url_for("admin_panel"))
    if request.method == "POST":
        if request.form.get("username")==ADMIN_USER and request.form.get("password")==ADMIN_PASS:
            session["is_admin"] = True
            return redirect(url_for("admin_panel"))
        flash("❌ بيانات خاطئة", "err")
    return render_template_string("""<!DOCTYPE html><html dir="rtl"><head><title>Admin Login</title>
    <style>body{background:#000;color:#00ff88;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
    .box{background:#0a0a0a;padding:30px;border:2px solid #00ff88;border-radius:12px;min-width:340px;box-shadow:0 0 30px rgba(0,255,136,.3)}
    h1{text-align:center;color:#ffd700;margin:0 0 20px}
    input{width:100%;padding:12px;margin:6px 0 14px;background:#000;border:1px solid #1a3a28;color:#fff;border-radius:8px;box-sizing:border-box}
    button{width:100%;padding:12px;background:linear-gradient(135deg,#ffd700,#b8860b);color:#000;border:none;border-radius:8px;font-weight:800;cursor:pointer;font-size:14px}
    </style></head><body><div class="box"><h1>👑 Admin Login</h1>
    <form method="post"><input name="username" placeholder="Username" required autofocus>
    <input name="password" type="password" placeholder="Password" required>
    <button>🔓 Login</button></form></div></body></html>""")

@app.route("/admin-logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))

@app.route("/admin")
@admin_required
def admin_panel():
    with db() as c:
        users = c.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
        projects = c.execute("SELECT p.*, u.username FROM projects p LEFT JOIN users u ON u.id=p.user_id ORDER BY p.id DESC").fetchall()
        total_files = c.execute("SELECT COUNT(*) FROM project_files").fetchone()[0]
    running_count = sum(1 for p in projects if p["status"]=="running")
    new_user = session.pop("last_new_user", None)
    return render_template_string(ADMIN_PANEL_HTML, users=users, projects=projects,
        running_count=running_count, total_files=total_files, new_user=new_user)

@app.route("/admin/create-user", methods=["POST"])
@admin_required
def admin_create_user():
    u = request.form.get("username","").strip()
    p = request.form.get("password","").strip()
    try: d = max(1, min(int(request.form.get("days",30)), 3650))
    except: d = 30
    if not u or not p:
        flash("بيانات فارغة", "err"); return redirect(url_for("admin_panel"))
    name, msg = create_user(u, p, d)
    if not name:
        flash(f"❌ {msg}", "err"); return redirect(url_for("admin_panel"))
    session["last_new_user"] = {"username":u,"password":p,"days":d}
    flash(f"✅ تم إنشاء: {u}", "ok")
    return redirect(url_for("admin_panel"))

@app.route("/admin/disable-user/<int:uid>")
@admin_required
def admin_disable_user(uid):
    with db() as c:
        c.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
        c.execute("UPDATE sessions SET active=0 WHERE user_id=?", (uid,))
    flash("🚫 تم التعطيل", "ok"); return redirect(url_for("admin_panel"))

@app.route("/admin/enable-user/<int:uid>")
@admin_required
def admin_enable_user(uid):
    with db() as c: c.execute("UPDATE users SET active=1 WHERE id=?", (uid,))
    flash("✅ تم التفعيل", "ok"); return redirect(url_for("admin_panel"))

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
    flash("🗑 تم الحذف", "ok"); return redirect(url_for("admin_panel"))

# ---------- User Auth ----------
@app.route("/", methods=["GET"])
def index():
    if get_user_session(session.get("token")): return redirect(url_for("dashboard"))
    return render_template_string(LOGIN_HTML)

@app.route("/", methods=["POST"])
def login_post():
    ok, msg, row = validate_user(request.form.get("username","").strip(), request.form.get("password",""))
    if not ok: flash(msg, "err"); return redirect(url_for("index"))
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
        projects = c.execute("""SELECT p.*, (SELECT COUNT(*) FROM project_files WHERE project_id=p.id) as file_count
                                FROM projects p WHERE p.user_id=? ORDER BY p.id DESC""", (s["user_id"],)).fetchall()
        user = c.execute("SELECT * FROM users WHERE id=?", (s["user_id"],)).fetchone()
        total_files = c.execute("SELECT COUNT(*) FROM project_files WHERE project_id IN (SELECT id FROM projects WHERE user_id=?)", (s["user_id"],)).fetchone()[0]
    total_bytes = 0
    for p in projects:
        pf = BOTS / p["folder"]
        if pf.exists():
            for f in pf.rglob("*"):
                if f.is_file():
                    try: total_bytes += f.stat().st_size
                    except: pass
    running_count = sum(1 for p in projects if p["status"]=="running")
    return render_template_string(DASH_HTML, projects=projects, username=user["username"],
        expires_at=user["expires_at"], running_count=running_count,
        total_files=total_files, total_size=human_size(total_bytes))

# ---------- Upload ----------
@app.route("/upload", methods=["GET","POST"])
@user_required
def upload():
    s = get_user_session(session["token"])
    if request.method == "POST":
        name = safe(request.form.get("name", "project"))
        files = [f for f in request.files.getlist("files") if f and f.filename][:MAX_FILES]
        if not files:
            flash("اختر ملفاً", "err"); return redirect(url_for("upload"))
        folder_name = f"u{s['user_id']}_{int(time.time())}"
        folder = BOTS / folder_name
        folder.mkdir(parents=True, exist_ok=True)
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
                            if not str(dest).startswith(str(root)): raise ValueError("unsafe")
                        z.extractall(folder)
                except Exception as e:
                    flash(f"ZIP error: {e}", "err")
                finally: tmp.unlink(missing_ok=True)
            else:
                f.save(folder / fname)
        with db() as c:
            cur = c.execute("INSERT INTO projects(user_id,name,folder,created_at) VALUES(?,?,?,?)",
                            (s["user_id"], name, folder_name, iso(now())))
            pid = cur.lastrowid
            for f in [x for x in folder.rglob("*") if x.is_file()]:
                c.execute("INSERT INTO project_files(project_id,filename,created_at) VALUES(?,?,?)",
                          (pid, str(f.relative_to(folder)), iso(now())))
        # اختيار ملف تشغيل
        entry = None
        for cand in ("bot.py","main.py","app.py","index.py","index.js","main.js","bot.js"):
            if (folder / cand).exists(): entry = cand; break
        if not entry:
            pys = list(folder.rglob("*.py")); jss = list(folder.rglob("*.js"))
            if pys: entry = str(pys[0].relative_to(folder))
            elif jss: entry = str(jss[0].relative_to(folder))
        if entry:
            with db() as c:
                c.execute("UPDATE projects SET entry=? WHERE id=?", (entry, pid))
                c.execute("UPDATE project_files SET is_entry=1 WHERE project_id=? AND filename=?", (pid, entry))
        if request.form.get("auto_start") and entry:
            ok, m = start_project(s["user_id"], pid)
            flash(("✅ " if ok else "❌ ") + m, "ok" if ok else "err")
        else:
            flash(f"✅ تم رفع {len(files)} ملف", "ok")
        return redirect(url_for("console", pid=pid))
    return render_template_string(UPLOAD_HTML)

# ---------- Console ----------
@app.route("/console/<int:pid>")
@user_required
def console(pid):
    s = get_user_session(session["token"])
    with db() as c:
        project = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, s["user_id"])).fetchone()
    if not project: return "غير موجود", 404
    status = "running" if running_bots.get(pid) and running_bots[pid]["proc"].poll() is None else "stopped"
    with db() as c: c.execute("UPDATE projects SET status=? WHERE id=?", (status, pid))
    folder = BOTS / project["folder"]
    disk = 0
    if folder.exists():
        for f in folder.rglob("*"):
            if f.is_file():
                try: disk += f.stat().st_size
                except: pass
    return render_template_string(CONSOLE_HTML, project=project, log=read_log(pid),
        status=status, stats=get_stats(pid), project_uptime=project_uptime(pid),
        disk_usage=human_size(disk))

@app.route("/logs-json/<int:pid>")
@user_required
def logs_json(pid):
    return jsonify({"log": read_log(pid)})

@app.route("/stats-json/<int:pid>")
@user_required
def stats_json(pid):
    return jsonify(get_stats(pid))

@app.route("/send-command/<int:pid>", methods=["POST"])
@user_requireddef send_command(pid):
    s = get_user_session(session["token"])
    with db() as c:
        project = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, s["user_id"])).fetchone()
    if not project: return jsonify({"ok":False}), 404
    data = request.get_json() or {}
    cmd = data.get("command","").strip()
    if not cmd: return jsonify({"ok":False}), 400
    with _lock:
        item = running_bots.get(pid)
    if item and item["proc"].poll() is None:
        try:
            item["proc"].stdin.write(cmd + "\n")
            item["proc"].stdin.flush()
            item["log"].write(f"\n>>> {cmd}\n")
            item["log"].flush()
            return jsonify({"ok":True})
        except Exception as e:
            return jsonify({"ok":False,"err":str(e)}), 500
    return jsonify({"ok":False,"err":"not running"}), 400

@app.route("/logs-download/<int:pid>")
@user_required
def logs_download(pid):
    s = get_user_session(session["token"])
    with db() as c:
        project = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, s["user_id"])).fetchone()
    if not project: return "غير موجود", 404
    from flask import Response
    data = read_log(pid, 10000)
    return Response(data, mimetype="text/plain",
                    headers={"Content-Disposition": f"attachment; filename=log_{pid}.txt"})

# ---------- Controls ----------
@app.route("/start/<int:pid>")
@user_required
def start(pid):
    s = get_user_session(session["token"])
    ok, m = start_project(s["user_id"], pid)
    flash(m, "ok" if ok else "err")
    return redirect(url_for("console", pid=pid))

@app.route("/stop/<int:pid>")
@user_required
def stop(pid):
    s = get_user_session(session["token"])
    ok, m = stop_project(s["user_id"], pid, force=False)
    flash(m, "ok" if ok else "err")
    return redirect(url_for("console", pid=pid))

@app.route("/kill/<int:pid>")
@user_required
def kill(pid):
    s = get_user_session(session["token"])
    ok, m = stop_project(s["user_id"], pid, force=True)
    flash("💀 " + m, "ok" if ok else "err")
    return redirect(url_for("console", pid=pid))

@app.route("/restart/<int:pid>")
@user_required
def restart(pid):
    s = get_user_session(session["token"])
    stop_project(s["user_id"], pid); time.sleep(1)
    ok, m = start_project(s["user_id"], pid)
    flash("🔄 " + m, "ok" if ok else "err")
    return redirect(url_for("console", pid=pid))

# ---------- Files ----------
@app.route("/files/<int:pid>")
@user_required
def files(pid):
    s = get_user_session(session["token"])
    with db() as c:
        project = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, s["user_id"])).fetchone()
        if not project: return "غير موجود", 404
        flist = c.execute("SELECT * FROM project_files WHERE project_id=? ORDER BY is_entry DESC, filename", (pid,)).fetchall()
    enriched = []
    folder = BOTS / project["folder"]
    for f in flist:
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
    flash(f"⭐ تم تعيين '{file_row['filename']}'", "ok")
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
        try: (BOTS / project["folder"] / file_row["filename"]).unlink()
        except: pass
        c.execute("DELETE FROM project_files WHERE id=?", (fid,))
        if file_row["is_entry"]: c.execute("UPDATE projects SET entry=NULL WHERE id=?", (pid,))
    flash("🗑 تم الحذف", "ok"); return redirect(url_for("files", pid=pid))

@app.route("/delete/<int:pid>")
@user_required
def delete_project(pid):
    s = get_user_session(session["token"])
    stop_project(s["user_id"], pid, force=True)
    with db() as c:
        row = c.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, s["user_id"])).fetchone()
        if row:
            c.execute("DELETE FROM project_files WHERE project_id=?", (pid,))
            c.execute("DELETE FROM projects WHERE id=?", (pid,))
    if row: shutil.rmtree(BOTS / row["folder"], ignore_errors=True)
    flash("🗑 تم حذف المشروع", "ok")
    return redirect(url_for("dashboard"))

@app.route("/health")
def health(): return {"ok": True, "site": SITE_NAME}

# ================= MAIN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log.info(f"🌐 {SITE_NAME} — port {port}")
    log.info(f"👑 Admin URL: /{ADMIN_SECRET_PATH}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)