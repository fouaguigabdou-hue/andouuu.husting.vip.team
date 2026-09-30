# =====================================================================
#   𝗔𝗕𝗗𝗢𝗨𝗨 𝗩𝗜𝗣 𝗛𝗢𝗦𝗧𝗜𝗡𝗚 — Telegram Bot (Owner Only)
# =====================================================================
import os, asyncio, logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ApplicationBuilder, CommandHandler,
                          CallbackQueryHandler, ContextTypes)

# نستورد من app.py قاعدة البيانات والدوال
from app import db, create_code, now, parse, SITE_NAME, CODE_PREFIX

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8309622602:AAFS84wr8SFbQuYcken9TDW_N0qLnhsCQ7k")
OWNER_ID  = int(os.environ.get("OWNER_ID", "8046711782") or "8046711782")

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger("BOT")

def is_owner(uid): return int(uid) == int(OWNER_ID)

def menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎟️ إنشاء كود", callback_data="create")],
        [InlineKeyboardButton("📋 قائمة الأكواد", callback_data="list")],
        [InlineKeyboardButton("📊 إحصائيات", callback_data="stats")],
        [InlineKeyboardButton("🆔 آيديي", callback_data="myid")],
    ])

async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid = u.effective_user.id
    log.info(f"/start from {uid} | expected owner={OWNER_ID}")
    if not is_owner(uid):
        await u.message.reply_text(
            f"⛔ هذا البوت للمسؤول فقط.\n\n"
            f"🆔 آيديك: `{uid}`\n"
            f"👑 المسؤول المتوقع: `{OWNER_ID}`\n\n"
            f"إذا أنت المسؤول: ضع `{uid}` في متغير OWNER_ID على Render.",
            parse_mode="Markdown"
        )
        return
    await u.message.reply_text(
        f"⚡ *{SITE_NAME}* ⚡\n\n"
        f"لوحة تحكم المسؤول.\n\n"
        f"📌 الأوامر:\n"
        f"• /newcode `users days` — إنشاء كود\n"
        f"• /listcodes — قائمة الأكواد\n"
        f"• /disable `الكود` — تعطيل كود\n"
        f"• /stats — إحصائيات\n"
        f"• /myid — عرض آيديك",
        parse_mode="Markdown",
        reply_markup=menu_kb()
    )

async def cmd_myid(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid = u.effective_user.id
    await u.message.reply_text(
        f"🆔 آيديك: `{uid}`\n"
        f"👑 آيدي المسؤول: `{OWNER_ID}`\n"
        f"{'✅ مطابق' if is_owner(uid) else '❌ غير مطابق'}",
        parse_mode="Markdown"
    )

async def cmd_newcode(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    a = c.args or []
    if len(a) != 2:
        await u.message.reply_text(
            "📌 *الاستخدام:*\n`/newcode <عدد_المستخدمين> <عدد_الأيام>`\n\n"
            "مثال: `/newcode 5 30`",
            parse_mode="Markdown"
        )
        return
    try:
        users, days = int(a[0]), int(a[1])
        if users < 1 or users > 10000 or days < 1 or days > 3650:
            raise ValueError
    except ValueError:
        await u.message.reply_text("❌ أرقام غير صالحة. (users: 1-10000, days: 1-3650)")
        return
    code = create_code(users, days)
    await u.message.reply_text(
        f"✅ *تم إنشاء الكود*\n\n"
        f"🎟️ الكود:\n`{code}`\n\n"
        f"👥 عدد المستخدمين: *{users}*\n"
        f"📅 المدة: *{days} يوم*\n\n"
        f"📌 أرسله للمستخدم للدخول للموقع.",
        parse_mode="Markdown"
    )

async def cmd_listcodes(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    with db() as cn:
        rows = cn.execute("SELECT * FROM codes ORDER BY id DESC LIMIT 30").fetchall()
    if not rows:
        await u.message.reply_text("📭 لا يوجد أكواد بعد.")
        return
    lines = ["📋 *آخر 30 كود*\n"]
    for r in rows:
        exp = parse(r["expires_at"])
        st = "🟢" if r["active"] and (not exp or now() < exp) else "🔴"
        lines.append(
            f"{st} `{r['code']}`\n"
            f"   👥 {r['used_count']}/{r['max_users']} | 📅 {r['days']}d"
        )
    await u.message.reply_text("\n\n".join(lines), parse_mode="Markdown")

async def cmd_disable(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    a = c.args or []
    if not a:
        await u.message.reply_text(
            f"📌 الاستخدام:\n`/disable {CODE_PREFIX}-XXXX`",
            parse_mode="Markdown"
        )
        return
    code = a[0].strip()
    with db() as cn:
        row = cn.execute("SELECT id FROM codes WHERE code=?", (code,)).fetchone()
        if not row:
            await u.message.reply_text("❌ الكود غير موجود.")
            return
        cn.execute("UPDATE codes SET active=0 WHERE id=?", (row["id"],))
    await u.message.reply_text(f"🚫 تم تعطيل الكود:\n`{code}`", parse_mode="Markdown")

async def cmd_stats(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_owner(u.effective_user.id): return
    with db() as cn:
        tc = cn.execute("SELECT COUNT(*) FROM codes").fetchone()[0]
        ac = cn.execute("SELECT COUNT(*) FROM codes WHERE active=1").fetchone()[0]
        ts = cn.execute("SELECT COUNT(*) FROM sessions WHERE active=1").fetchone()[0]
        tb = cn.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
    await u.message.reply_text(
        f"📊 *إحصائيات {SITE_NAME}*\n\n"
        f"🎟️ إجمالي الأكواد: *{tc}*\n"
        f"🟢 أكواد نشطة: *{ac}*\n"
        f"👥 جلسات فعّالة: *{ts}*\n"
        f"🤖 بوتات مستضافة: *{tb}*",
        parse_mode="Markdown"
    )

async def menu_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    await q.answer()
    if not is_owner(u.effective_user.id):
        await q.edit_message_text("⛔ للمسؤول فقط.")
        return
    d = q.data
    if d == "create":
        await q.edit_message_text(
            "🎟️ *إنشاء كود*\n\n`/newcode <users> <days>`\n\nمثال: `/newcode 5 30`",
            parse_mode="Markdown", reply_markup=menu_kb()
        )
    elif d == "list":
        with db() as cn:
            rows = cn.execute("SELECT * FROM codes ORDER BY id DESC LIMIT 30").fetchall()
        if not rows:
            await q.edit_message_text("📭 لا يوجد أكواد.", reply_markup=menu_kb())
            return
        lines = ["📋 *آخر 30 كود*\n"]
        for r in rows:
            lines.append(f"{'🟢' if r['active'] else '🔴'} `{r['code']}` — {r['used_count']}/{r['max_users']}")
        await q.edit_message_text("\n".join(lines), parse_mode="Markdown",
                                  reply_markup=menu_kb())
    elif d == "stats":
        with db() as cn:
            tc = cn.execute("SELECT COUNT(*) FROM codes").fetchone()[0]
            ac = cn.execute("SELECT COUNT(*) FROM codes WHERE active=1").fetchone()[0]
            ts = cn.execute("SELECT COUNT(*) FROM sessions WHERE active=1").fetchone()[0]
            tb = cn.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
        await q.edit_message_text(
            f"📊 *إحصائيات*\n\n🎟️ {tc}\n🟢 {ac}\n👥 {ts}\n🤖 {tb}",
            parse_mode="Markdown", reply_markup=menu_kb()
        )
    elif d == "myid":
        uid = u.effective_user.id
        await q.edit_message_text(
            f"🆔 آيديك: `{uid}`\n👑 المسؤول: `{OWNER_ID}`\n"
            f"{'✅ مطابق' if is_owner(uid) else '❌ غير مطابق'}",
            parse_mode="Markdown", reply_markup=menu_kb()
        )

async def run_async():
    if not BOT_TOKEN:
        log.error("❌ BOT_TOKEN غير محدد — لن يشتغل البوت.")
        return
    if not OWNER_ID:
        log.error("❌ OWNER_ID غير محدد — لن يرد البوت على أحد.")
        return

    log.info(f"🤖 تشغيل البوت | OWNER_ID={OWNER_ID}")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("myid",      cmd_myid))
    app.add_handler(CommandHandler("newcode",   cmd_newcode))
    app.add_handler(CommandHandler("listcodes", cmd_listcodes))
    app.add_handler(CommandHandler("disable",   cmd_disable))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CallbackQueryHandler(menu_cb))

    log.info("✅ البوت جاهز — يستقبل الرسائل الآن...")

    # نستخدم start_polling يدوياً لتجنب مشاكل signal مع gunicorn
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True,
                                    allowed_updates=Update.ALL_TYPES)
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

def main():
    try:
        asyncio.run(run_async())
    except Exception as e:
        log.exception(f"💥 خطأ في البوت: {e}")

if __name__ == "__main__":
    main()