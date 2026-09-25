import os, sqlite3, logging
from decimal import Decimal, InvalidOperation
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)

TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL = os.getenv("CHANNEL", "@konfingMoji")
SUPPORT = os.getenv("SUPPORT_USERNAME", "@Mojttaba6")
CARD_NUMBER = os.getenv("CARD_NUMBER", "6037997523216357")
CARD_NAME = os.getenv("CARD_NAME", "حسینی")

# Prices are stored in toman.
DEFAULT_PRICES = {"gaming": 12000, "wireguard": 5000, "web": 3000}
DB = "config_shop.db"
AWAITING_CONFIG = 1

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("config-shop")

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        service TEXT NOT NULL,
        gb INTEGER NOT NULL,
        base_price INTEGER NOT NULL,
        discount INTEGER DEFAULT 0,
        total INTEGER NOT NULL,
        status TEXT NOT NULL,
        note TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS discounts (
        code TEXT PRIMARY KEY,
        percent INTEGER NOT NULL,
        active INTEGER DEFAULT 1
    );
    """)
    for k, v in DEFAULT_PRICES.items():
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (f"price_{k}", str(v)))
    con.commit()
    con.close()

def price(service):
    con = db()
    row = con.execute("SELECT value FROM settings WHERE key=?", (f"price_{service}",)).fetchone()
    con.close()
    return int(row["value"]) if row else DEFAULT_PRICES[service]

def set_price(service, value):
    con = db()
    con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (f"price_{service}", str(value)))
    con.commit(); con.close()

def money(n): return f"{n:,} تومان"

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 گیمینگ پرسرعت", callback_data="service:gaming")],
        [InlineKeyboardButton("🛡️ گیمینگ WireGuard", callback_data="service:wireguard")],
        [InlineKeyboardButton("🌐 کانفیگ وب", callback_data="service:web")],
        [InlineKeyboardButton("💬 پشتیبانی", callback_data="support")]
    ])

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 قیمت‌ها", callback_data="admin:prices"),
         InlineKeyboardButton("🎟️ کد تخفیف", callback_data="admin:discount")],
        [InlineKeyboardButton("📈 گزارش", callback_data="admin:stats"),
         InlineKeyboardButton("📢 پیام همگانی", callback_data="admin:broadcast")]
    ])

async def is_member(bot, user_id):
    try:
        m = await bot.get_chat_member(CHANNEL, user_id)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not await is_member(context.bot, u.id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL.lstrip('@')}")],
            [InlineKeyboardButton("✅ بررسی عضویت", callback_data="check_member")]
        ])
        await update.message.reply_text("برای استفاده از ربات ابتدا عضو کانال شوید.", reply_markup=kb)
        return
    await update.message.reply_text("به Config Shop خوش آمدید 👋\nیکی از خدمات را انتخاب کنید:", reply_markup=main_menu())

async def check_member(update, context):
    q = update.callback_query
    await q.answer()
    if await is_member(context.bot, q.from_user.id):
        await q.edit_message_text("عضویت تأیید شد ✅", reply_markup=main_menu())
    else:
        await q.answer("هنوز عضویت شما تأیید نشده است.", show_alert=True)

async def service(update, context):
    q = update.callback_query; await q.answer()
    s = q.data.split(":")[1]
    context.user_data["service"] = s
    if s == "wireguard":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"{g} گیگ", callback_data=f"gb:{g}:wireguard") for g in (5,10)],
                                   [InlineKeyboardButton(f"{g} گیگ", callback_data=f"gb:{g}:wireguard") for g in (20,50)]])
        await q.edit_message_text("پلن WireGuard را انتخاب کنید:", reply_markup=kb)
    elif s == "gaming":
        await q.edit_message_text(f"حجم Gaming پرسرعت را به عدد وارد کنید.\nقیمت هر گیگ: {money(price('gaming'))}\n🎁 خرید ۱۰ گیگ یا بیشتر: ۵ گیگ هدیه")
        context.user_data["await_gb"] = True
    else:
        await q.edit_message_text(f"چند گیگ کانفیگ وب می‌خواهید؟\nقیمت هر گیگ: {money(price('web'))}")
        context.user_data["await_gb"] = True

async def gb_choice(update, context):
    q = update.callback_query; await q.answer()
    _, g, s = q.data.split(":")
    await create_order(update, context, int(g), s)

async def text_handler(update, context):
    if context.user_data.get("await_gb"):
        try:
            g = int(update.message.text.strip())
            if g <= 0 or g > 10000: raise ValueError
        except ValueError:
            await update.message.reply_text("لطفاً تعداد گیگ را به صورت یک عدد مثبت وارد کنید.")
            return
        context.user_data["await_gb"] = False
        await create_order(update, context, g, context.user_data["service"])
        return

    if context.user_data.get("await_discount"):
        code = update.message.text.strip().upper()
        con = db()
        row = con.execute("SELECT * FROM discounts WHERE code=? AND active=1", (code,)).fetchone()
        con.close()
        if not row:
            await update.message.reply_text("کد تخفیف معتبر نیست یا غیرفعال است.")
            context.user_data["await_discount"] = False
            return
        oid = context.user_data["order_id"]
        con = db()
        order = con.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        disc = order["base_price"] * row["percent"] // 100
        total = order["base_price"] - disc
        con.execute("UPDATE orders SET discount=?, total=? WHERE id=?", (disc,total,oid))
        con.commit(); con.close()
        context.user_data["await_discount"] = False
        await show_payment(update, context, oid)
        return

    if context.user_data.get("admin_note"):
        oid = context.user_data.pop("admin_note")
        con=db(); con.execute("UPDATE orders SET note=? WHERE id=?", (update.message.text, oid)); con.commit(); con.close()
        await update.message.reply_text("یادداشت ذخیره شد.")
        return

async def create_order(update, context, gb, service):
    p = price(service); total = p * gb
    u = update.effective_user
    con=db()
    cur=con.execute("""INSERT INTO orders(user_id,username,service,gb,base_price,total,status)
                       VALUES(?,?,?,?,?,?,?)""",
                    (u.id,u.username or "",service,gb,total,total,"awaiting_payment"))
    oid=cur.lastrowid; con.commit(); con.close()
    context.user_data["order_id"]=oid
    await show_payment(update, context, oid)

async def show_payment(update, context, oid):
    con=db(); o=con.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone(); con.close()
    gift = "\n🎁 ۵ گیگ Gaming هدیه" if o["service"]=="gaming" and o["gb"]>=10 else ""
    text = (f"🧾 سفارش #{oid}\n"
            f"سرویس: {o['service']}\nحجم: {o['gb']} گیگ\n"
            f"مبلغ: {money(o['total'])}{gift}\n\n"
            f"💳 شماره کارت: {CARD_NUMBER}\nبه نام: {CARD_NAME}\n\n"
            "پس از پرداخت، فیش را همینجا ارسال کنید.")
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 کپی شماره کارت", copy_text={"text": CARD_NUMBER})],
        [InlineKeyboardButton("🎟️ کد تخفیف دارم", callback_data=f"discount:{oid}")]
    ])
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text(text, reply_markup=kb)

async def discount_button(update, context):
    q=update.callback_query; await q.answer()
    context.user_data["order_id"]=int(q.data.split(":")[1])
    context.user_data["await_discount"]=True
    await q.message.reply_text("کد تخفیف را وارد کنید:")

async def receipt(update, context):
    u=update.effective_user
    con=db(); o=con.execute("""SELECT * FROM orders WHERE user_id=? AND status='awaiting_payment'
                              ORDER BY id DESC LIMIT 1""",(u.id,)).fetchone(); con.close()
    if not o:
        await update.message.reply_text("سفارش پرداخت‌نشده‌ای پیدا نشد.")
        return
    con=db(); con.execute("UPDATE orders SET status='awaiting_review' WHERE id=?", (o["id"],)); con.commit(); con.close()
    caption=f"🧾 فیش سفارش #{o['id']}\n👤 کاربر: @{u.username or 'بدون یوزرنیم'}\n🆔 {u.id}\n💰 {money(o['total'])}"
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ قبول", callback_data=f"approve:{o['id']}"),
                              InlineKeyboardButton("❌ رد", callback_data=f"reject:{o['id']}")]])
    if update.message.photo:
        await context.bot.send_photo(ADMIN_ID, update.message.photo[-1].file_id, caption=caption, reply_markup=kb)
    elif update.message.document:
        await context.bot.send_document(ADMIN_ID, update.message.document.file_id, caption=caption, reply_markup=kb)
    await update.message.reply_text("فیش شما برای بررسی ارسال شد. ⏳")

async def approve(update, context):
    q=update.callback_query; await q.answer()
    oid=int(q.data.split(":")[1])
    con=db(); o=con.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    con.execute("UPDATE orders SET status='awaiting_config' WHERE id=?", (oid,)); con.commit(); con.close()
    context.user_data["config_order"]=oid
    await q.edit_message_reply_markup(reply_markup=None)
    await q.message.reply_text(f"سفارش #{oid} تأیید شد.\nحالا کانفیگ آماده را در همین چت برای من ارسال کنید تا به مشتری تحویل بدهم.")
    try:
        await context.bot.send_message(o["user_id"], "پرداخت شما تأیید شد ✅\nکانفیگ شما در حال آماده‌سازی است.")
    except Exception: pass

async def reject(update, context):
    q=update.callback_query; await q.answer()
    oid=int(q.data.split(":")[1])
    con=db(); o=con.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    con.execute("UPDATE orders SET status='rejected' WHERE id=?", (oid,)); con.commit(); con.close()
    await q.edit_message_reply_markup(reply_markup=None)
    await q.message.reply_text(f"سفارش #{oid} رد شد.")
    try: await context.bot.send_message(o["user_id"], f"پرداخت سفارش #{oid} تأیید نشد ❌\nبرای پیگیری با {SUPPORT} تماس بگیرید.")
    except Exception: pass

async def admin_config(update, context):
    if update.effective_user.id != ADMIN_ID: return
    oid=context.user_data.get("config_order")
    if not oid: return
    con=db(); o=con.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    con.execute("UPDATE orders SET status='completed' WHERE id=?", (oid,)); con.commit(); con.close()
    context.user_data.pop("config_order",None)
    if update.message.text:
        await context.bot.send_message(o["user_id"], f"🎉 سفارش #{oid} آماده است:\n\n{update.message.text}")
    elif update.message.document:
        await context.bot.send_document(o["user_id"], update.message.document.file_id, caption=f"🎉 کانفیگ سفارش #{oid}")
    elif update.message.photo:
        await context.bot.send_photo(o["user_id"], update.message.photo[-1].file_id, caption=f"🎉 سفارش #{oid}")
    await update.message.reply_text("کانفیگ برای مشتری ارسال شد ✅")

async def admin(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("دسترسی ندارید.")
        return
    await update.message.reply_text("پنل مدیریت Config Shop", reply_markup=admin_menu())

async def admin_callbacks(update, context):
    q=update.callback_query; await q.answer()
    if q.from_user.id != ADMIN_ID: return
    action=q.data.split(":")[1]
    if action=="prices":
        await q.message.reply_text(
            f"قیمت‌ها:\nGaming: {money(price('gaming'))}\nWireGuard: {money(price('wireguard'))}\nWeb: {money(price('web'))}\n\n"
            "برای تغییر: /setprice gaming 15000"
        )
    elif action=="discount":
        await q.message.reply_text("ساخت کد: /discount CODE PERCENT\nمثال: /discount OFF10 10")
    elif action=="stats":
        con=db()
        rows=con.execute("SELECT status, COUNT(*) c FROM orders GROUP BY status").fetchall()
        total=con.execute("SELECT COALESCE(SUM(total),0) t FROM orders WHERE status='completed'").fetchone()["t"]
        con.close()
        await q.message.reply_text("📈 گزارش\n" + "\n".join(f"{r['status']}: {r['c']}" for r in rows) + f"\nفروش تکمیل‌شده: {money(total)}")
    elif action=="broadcast":
        context.user_data["broadcast"]=True
        await q.message.reply_text("متن پیام همگانی را ارسال کنید.")

async def setprice(update, context):
    if update.effective_user.id != ADMIN_ID or len(context.args)!=2: return
    s,v=context.args
    if s not in DEFAULT_PRICES:
        await update.message.reply_text("سرویس باید gaming یا wireguard یا web باشد.")
        return
    try: v=int(v)
    except: await update.message.reply_text("قیمت نامعتبر است."); return
    set_price(s,v); await update.message.reply_text("قیمت تغییر کرد ✅")

async def discount_cmd(update, context):
    if update.effective_user.id != ADMIN_ID or len(context.args)!=2: return
    code,p=context.args[0].upper(), context.args[1]
    try: p=int(p)
    except: await update.message.reply_text("درصد نامعتبر است."); return
    if not 1 <= p <= 100: await update.message.reply_text("درصد باید بین 1 تا 100 باشد."); return
    con=db(); con.execute("INSERT OR REPLACE INTO discounts(code,percent,active) VALUES(?,?,1)",(code,p)); con.commit(); con.close()
    await update.message.reply_text(f"کد {code} با تخفیف {p}% ساخته شد ✅")

async def all_messages(update, context):
    if update.effective_user.id == ADMIN_ID and context.user_data.get("config_order"):
        await admin_config(update, context)
        return
    if context.user_data.get("broadcast") and update.effective_user.id == ADMIN_ID:
        context.user_data.pop("broadcast",None)
        con=db(); users=con.execute("SELECT DISTINCT user_id FROM orders").fetchall(); con.close()
        sent=0
        for r in users:
            try: await context.bot.send_message(r["user_id"], update.message.text); sent+=1
            except: pass
        await update.message.reply_text(f"پیام برای {sent} کاربر ارسال شد.")
        return
    if update.message.photo or update.message.document:
        await receipt(update, context)
    else:
        await text_handler(update, context)

def run():
    if not TOKEN or not ADMIN_ID:
        raise RuntimeError("BOT_TOKEN و ADMIN_ID را در Replit Secrets تنظیم کنید.")
    init_db()
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("setprice", setprice))
    app.add_handler(CommandHandler("discount", discount_cmd))
    app.add_handler(CallbackQueryHandler(check_member, pattern="^check_member$"))
    app.add_handler(CallbackQueryHandler(service, pattern="^service:"))
    app.add_handler(CallbackQueryHandler(gb_choice, pattern="^gb:"))
    app.add_handler(CallbackQueryHandler(discount_button, pattern="^discount:"))
    app.add_handler(CallbackQueryHandler(approve, pattern="^approve:"))
    app.add_handler(CallbackQueryHandler(reject, pattern="^reject:"))
    app.add_handler(CallbackQueryHandler(admin_callbacks, pattern="^admin:"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, all_messages))
    app.run_polling()

if __name__ == "__main__":
    run()
