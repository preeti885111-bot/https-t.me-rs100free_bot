import os
import re
import json
import time
import uuid
import random
import threading
import logging
import shutil
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import telebot
from telebot import types
from flask import Flask

# ============================================================
# YonoVoucher2Bot / Profit Masters - FULL ADMIN BOT
# Preserves the original bot flow and adds the requested
# admin controls, analytics, URL tracking, user management,
# backups, reports, previews and broadcast controls.
#
# Required Render environment variables:
#   BOT_TOKEN = your Telegram bot token
#   ADMIN_ID  = your owner Telegram numeric ID
#
# Start command:
#   python bot.py
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s"
)
log = logging.getLogger("YonoVoucher2Bot")
try:
    log.addHandler(logging.FileHandler("bot.log", encoding="utf-8"))
except Exception:
    pass

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")

try:
    OWNER_ID = int(os.getenv("ADMIN_ID", "1908832842"))
except ValueError:
    OWNER_ID = 1908832842

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

DB_FILE = os.path.join(DATA_DIR, "bot_data.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

db_lock = threading.RLock()

# ------------------------- DEFAULTS --------------------------

DEFAULT_DB = {
    "users": {},                         # uid -> profile
    "admins": [OWNER_ID],
    "settings": {
        "maintenance": False,

        "welcome_enabled": True,
        "welcome_photo": "https://placehold.co/600x400/png",
        "welcome_caption": (
            "🎉 <b>Welcome to Profit Masters!</b>\n\n"
            "🎁 Your signup bonus is ready.\n"
            "👇 Join the required channels and verify."
        ),

        "force_join_enabled": True,
        "channels": ["@ch1", "@ch2"],
        "join_button_text": "✅ Join Channel",
        "force_join_message": (
            "⚠️ <b>Join all required channels first.</b>\n\n"
            "After joining, press the verification button again."
        ),

        "free_code_enabled": True,
        "free_code_btn_text": "🎁 Get My Free Code",
        "code_prefix": "IW7-PROMO",
        "success_message": (
            "🎁 <b>Verification Successful!</b>\n\n"
            "🔑 Your Code: <code>{code}</code>"
        ),
        "verification_delay_enabled": True,
        "verification_delay_seconds": 5,
        "verification_text": "⏳ Processing your request... Please wait {seconds} seconds.",
        "verification_success_text": "✅ Verification successful!",
        "verification_failed_text": "❌ Verification failed. Please join all required channels.",

        "welcome_buttons": [
            {"id": "b1", "text": "🚀 Claim ₹500", "url": "https://t.me/telegram", "clicks": 0},
            {"id": "b2", "text": "🎁 Unlock Code", "url": "https://t.me/telegram", "clicks": 0},
            {"id": "b3", "text": "🎯 Claim Bonus", "url": "https://t.me/telegram", "clicks": 0},
            {"id": "b4", "text": "💎 VIP Gift", "url": "https://t.me/telegram", "clicks": 0},
        ],

        "broadcast_button_text": "👉 Register Now",
        "auto_pin": True,
        "auto_unpin": False,
        "auto_delete": False,
        "auto_delete_seconds": 0,
        "register_message": "📢 New registration offer is available.",

        "welcome_emoji": True,
        "bot_enabled": True,
        "code_url": "",
        "broadcast_mode": "copy",
        "auto_register_message": False,
        "error_log": [],
    },
    "links": {},                         # link id -> URL tracking object
    "stats": {
        "total_clicks": 0,
        "daily_clicks": {},              # YYYY-MM-DD -> count
        "last_click": None,
    },
    "broadcasts": [],
}

# ------------------------- STORAGE ---------------------------

def deep_copy_default():
    return json.loads(json.dumps(DEFAULT_DB))

def load_db():
    if not os.path.exists(DB_FILE):
        data = deep_copy_default()
        # Migrate the older users_db.json used by the original bot when present.
        old_file = "users_db.json"
        if os.path.exists(old_file):
            try:
                with open(old_file, "r", encoding="utf-8") as f:
                    old = json.load(f)
                old_users = old.get("users", []) if isinstance(old, dict) else []
                for uid in old_users:
                    data["users"][str(uid)] = {"id": int(uid), "username": "", "first_name": "", "blocked": False, "joined_at": now().isoformat()}
                old_links = old.get("broadcast_links", {}) if isinstance(old, dict) else {}
                data["links"] = old_links or {}
                log.info("Migrated %s users from users_db.json", len(old_users))
            except Exception:
                log.exception("Old database migration failed")
        save_db(data)
        return data
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Non-destructive migration for missing keys.
        default = deep_copy_default()

        def merge(dst, src):
            for k, v in src.items():
                if k not in dst:
                    dst[k] = v
                elif isinstance(v, dict) and isinstance(dst[k], dict):
                    merge(dst[k], v)

        merge(data, default)

        if OWNER_ID not in data["admins"]:
            data["admins"].insert(0, OWNER_ID)
        return data
    except Exception:
        log.exception("Could not read database; using defaults.")
        return deep_copy_default()

def save_db(data=None):
    global DB
    if data is None:
        data = DB
    with db_lock:
        tmp = DB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, DB_FILE)

DB = load_db()

def settings():
    return DB["settings"]

# ------------------------- HELPERS ---------------------------

def now():
    return datetime.now(timezone.utc)

def today_key():
    return now().strftime("%Y-%m-%d")

def is_owner(uid):
    return int(uid) == OWNER_ID

def is_admin(uid):
    return int(uid) in [int(x) for x in DB.get("admins", [])]

def admin_only(message):
    return is_admin(message.from_user.id)

def ensure_user(user):
    uid = str(user.id)
    if uid not in DB["users"]:
        DB["users"][uid] = {
            "id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "joined_at": now().isoformat(),
            "blocked": False,
            "clicks": 0,
            "last_seen": now().isoformat(),
        }
        save_db()
    else:
        u = DB["users"][uid]
        u["username"] = user.username or u.get("username", "")
        u["first_name"] = user.first_name or u.get("first_name", "")
        u["last_name"] = user.last_name or u.get("last_name", "")
        u["last_seen"] = now().isoformat()
        save_db()

def valid_url(value):
    return bool(re.match(r"^(https?://|tg://|t\.me/)", (value or "").strip(), re.I))

def extract_url(text):
    if not text:
        return None
    found = re.findall(r"(https?://[^\s<]+|t\.me/[^\s<]+)", text)
    return found[0].rstrip(".,)") if found else None

def safe_html(value):
    return (
        str(value).replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

def add_admin_if_missing(uid):
    if int(uid) not in [int(x) for x in DB["admins"]]:
        DB["admins"].append(int(uid))
        save_db()

def user_count():
    return len(DB["users"])

def click_count():
    return int(DB["stats"].get("total_clicks", 0))

def create_tracked_link(url, label="Register"):
    lid = uuid.uuid4().hex[:10]
    DB["links"][lid] = {
        "url": url,
        "label": label,
        "clicks": 0,
        "created_at": now().isoformat(),
        "last_click": None,
        "daily": {}
    }
    save_db()
    return lid

def record_click(lid, uid=None):
    item = DB["links"].get(lid)
    if not item:
        return False
    item["clicks"] = int(item.get("clicks", 0)) + 1
    item["last_click"] = now().isoformat()
    day = today_key()
    item.setdefault("daily", {})
    item["daily"][day] = int(item["daily"].get(day, 0)) + 1

    DB["stats"]["total_clicks"] = click_count() + 1
    DB["stats"].setdefault("daily_clicks", {})
    DB["stats"]["daily_clicks"][day] = (
        int(DB["stats"]["daily_clicks"].get(day, 0)) + 1
    )
    DB["stats"]["last_click"] = now().isoformat()

    if uid and str(uid) in DB["users"]:
        DB["users"][str(uid)]["clicks"] = (
            int(DB["users"][str(uid)].get("clicks", 0)) + 1
        )
    save_db()
    return True

def reset_link_clicks(lid):
    if lid in DB["links"]:
        DB["links"][lid]["clicks"] = 0
        DB["links"][lid]["last_click"] = None
        DB["links"][lid]["daily"] = {}
        save_db()
        return True
    return False

def reset_all_clicks():
    for item in DB["links"].values():
        item["clicks"] = 0
        item["last_click"] = None
        item["daily"] = {}
    for b in settings()["welcome_buttons"]:
        b["clicks"] = 0
    DB["stats"]["total_clicks"] = 0
    DB["stats"]["daily_clicks"] = {}
    DB["stats"]["last_click"] = None
    save_db()

def fmt_time(value):
    if not value:
        return "Never"
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone().strftime("%d-%m-%Y %I:%M:%S %p")
    except Exception:
        return str(value)

# ---------------------- KEYBOARDS ----------------------------

def admin_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    rows = [
        ["📊 Analytics", "📈 Reports"],
        ["👋 Welcome Control", "📢 Force Join"],
        ["🎁 Free Code", "🔘 Buttons"],
        ["🔗 URL Manager", "📤 Broadcast"],
        ["👥 User Manager", "🛠 Bot Settings"],
        ["📌 Message Control", "👮 Admin Manager"],
        ["⚙️ System Settings", "❌ Cancel"],
    ]
    for row in rows:
        kb.row(*[types.KeyboardButton(x) for x in row])
    return kb

def back_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.row("⬅️ Back", "❌ Cancel")
    return kb

def welcome_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for b in settings().get("welcome_buttons", []):
        # Direct URL buttons: pressing the button opens the saved URL.
        url = (b.get("url") or "").strip()
        if url and not url.lower().startswith(("http://", "https://", "tg://")):
            url = "https://" + url
        if url:
            buttons.append(types.InlineKeyboardButton(b["text"], url=url))
        else:
            buttons.append(types.InlineKeyboardButton(
                b["text"], callback_data=f"wbclick_{b['id']}"
            ))
    for i in range(0, len(buttons), 2):
        kb.row(*buttons[i:i+2])
    if settings().get("free_code_enabled"):
        kb.row(types.InlineKeyboardButton(
            settings()["free_code_btn_text"], callback_data="get_free_code"
        ))
    return kb

def force_join_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=1)
    for ch in settings().get("channels", []):
        ref, link, name = channel_parts(ch)
        if link:
            kb.add(types.InlineKeyboardButton(f"{settings()['join_button_text']} {name}", url=link))
    kb.add(types.InlineKeyboardButton("🔄 Verify Now", callback_data="verify_join"))
    return kb

# ----------------------- FORCE JOIN --------------------------

def check_user_joined_all(uid):
    if not settings()["force_join_enabled"]:
        return True
    channels = settings()["channels"]
    if not channels:
        return True
    for channel in channels:
        ref, _, _ = channel_parts(channel)
        try:
            member = bot.get_chat_member(ref, uid)
            if member.status in ("left", "kicked", "restricted"):
                return False
        except Exception as e:
            log.warning("Force join check failed for %s: %s", ref, e)
            record_error("force_join", e)
            return False
    return True

# ----------------------- WELCOME -----------------------------

def send_welcome(chat_id, uid):
    if not settings().get("bot_enabled", True):
        return
    if not settings()["welcome_enabled"]:
        return
    if settings()["maintenance"] and not is_admin(uid):
        bot.send_message(
            chat_id,
            "🚧 <b>Bot is currently under maintenance.</b>\nPlease try again later."
        )
        return

    markup = welcome_keyboard()
    caption = settings()["welcome_caption"]

    photo = settings().get("welcome_photo", "")
    try:
        if photo:
            bot.send_photo(chat_id, photo, caption=caption, reply_markup=markup)
        else:
            bot.send_message(chat_id, caption, reply_markup=markup)
    except Exception:
        bot.send_message(chat_id, caption, reply_markup=markup)

# ------------------------- START -----------------------------

@bot.message_handler(commands=["start"])
def start_cmd(message):
    ensure_user(message.from_user)
    uid = message.from_user.id
    if not settings().get("bot_enabled", True) and not is_admin(uid):
        bot.send_message(message.chat.id, "🚫 Bot is temporarily disabled. Please try later.")
        return
    if DB["users"][str(uid)].get("blocked"):
        bot.send_message(message.chat.id, "🚫 You are blocked by the administrator.")
        return
    send_welcome(message.chat.id, uid)

@bot.message_handler(commands=["admin"])
def admin_cmd(message):
    if not admin_only(message):
        return
    bot.send_message(
        message.chat.id,
        "🛠 <b>Admin Control Panel</b>\nChoose an option below.",
        reply_markup=admin_keyboard()
    )

# --------------------- USER FREE CODE ------------------------

def deliver_code(chat_id):
    code = f"{settings()['code_prefix']}-{random.randint(100000, 999999)}"
    msg = settings().get("verification_success_text") or settings().get("success_message", "🎁 Code: <code>{code}</code>")
    msg = msg.replace("{code}", safe_html(code))
    markup = None
    code_url = str(settings().get("code_url", "")).strip()
    if valid_url(code_url):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔗 Open URL", url=code_url))
    try:
        bot.send_message(chat_id, msg, reply_markup=markup)
    except Exception as e:
        record_error("deliver_code", e)
        bot.send_message(chat_id, f"🎁 Code: <code>{safe_html(code)}</code>", reply_markup=markup)

def verification_worker(chat_id, uid):
    delay = max(0, int(settings().get("verification_delay_seconds", 5)))
    time.sleep(delay)

    try:
        if not check_user_joined_all(uid):
            bot.send_message(
                chat_id,
                settings()["verification_failed_text"],
                reply_markup=force_join_keyboard()
            )
            return
        deliver_code(chat_id)
    except Exception:
        log.exception("Verification worker failed")

@bot.callback_query_handler(func=lambda c: c.data == "get_free_code")
def get_free_code(call):
    ensure_user(call.from_user)
    bot.answer_callback_query(call.id)

    # Always show loading, then the configured error.
    # No membership check and no code generation.
    delay = max(0, int(settings().get("verification_delay_seconds", 5)))
    loading = settings().get(
        "verification_text",
        "⏳ Processing your request... Please wait {seconds} seconds."
    ).replace("{seconds}", str(delay))
    error_text = settings().get(
        "verification_failed_text",
        "❌ Verification failed."
    )

    if delay > 0:
        bot.send_message(call.message.chat.id, loading)

        def _send_error():
            time.sleep(delay)
            bot.send_message(call.message.chat.id, error_text)

        threading.Thread(target=_send_error, daemon=True).start()
    else:
        bot.send_message(call.message.chat.id, error_text)

@bot.callback_query_handler(func=lambda c: c.data == "verify_join")
def verify_join(call):
    uid = call.from_user.id
    if check_user_joined_all(uid):
        bot.answer_callback_query(call.id, "✅ Verification successful!", show_alert=True)
        if settings()["free_code_enabled"]:
            deliver_code(call.message.chat.id)
    else:
        bot.answer_callback_query(
            call.id,
            settings()["verification_failed_text"],
            show_alert=True
        )

# ---------------------- TRACKED LINKS ------------------------

@bot.callback_query_handler(func=lambda c: c.data.startswith("track_"))
def tracked_click(call):
    lid = call.data.split("_", 1)[1]
    if record_click(lid, call.from_user.id):
        url = DB["links"][lid]["url"]
        bot.answer_callback_query(call.id, url=url)
    else:
        bot.answer_callback_query(
            call.id,
            "⚠️ This link is no longer active.",
            show_alert=True
        )

@bot.callback_query_handler(func=lambda c: c.data.startswith("wbclick_"))
def welcome_button_click(call):
    bid = call.data.split("_", 1)[1]
    for b in settings()["welcome_buttons"]:
        if b["id"] == bid:
            b["clicks"] = int(b.get("clicks", 0)) + 1
            DB["stats"]["total_clicks"] = click_count() + 1
            DB["stats"]["daily_clicks"][today_key()] = (
                int(DB["stats"]["daily_clicks"].get(today_key(), 0)) + 1
            )
            DB["stats"]["last_click"] = now().isoformat()
            save_db()
            bot.answer_callback_query(call.id, url=b["url"])
            return
    bot.answer_callback_query(call.id, "Button not found.", show_alert=True)

# --------------------- ADMIN STATE ---------------------------

STATE = {}

def set_state(uid, name, **kwargs):
    STATE[str(uid)] = {"name": name, **kwargs}

def get_state(uid):
    return STATE.get(str(uid), {})

def clear_state(uid):
    STATE.pop(str(uid), None)

# ------------------- ADMIN MENUS -----------------------------

def welcome_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🖼 Change Photo", callback_data="wc_photo"),
        types.InlineKeyboardButton("📝 Change Message", callback_data="wc_msg"),
    )
    kb.add(
        types.InlineKeyboardButton("🔘 Button Names", callback_data="wc_names"),
        types.InlineKeyboardButton("🔗 Button URLs", callback_data="wc_urls"),
    )
    kb.add(
        types.InlineKeyboardButton("➕ Add Button", callback_data="btn_add"),
        types.InlineKeyboardButton("➖ Remove Button", callback_data="btn_remove"),
    )
    kb.add(
        types.InlineKeyboardButton("↕️ Reorder Buttons", callback_data="btn_reorder"),
        types.InlineKeyboardButton("👀 Preview", callback_data="wc_preview"),
    )
    kb.add(types.InlineKeyboardButton(
        "🟢 Welcome ON" if settings()["welcome_enabled"] else "🔴 Welcome OFF",
        callback_data="wc_toggle"
    ))
    return kb

def force_join_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Add Channel", callback_data="fj_add"),
        types.InlineKeyboardButton("➖ Remove Channel", callback_data="fj_remove"),
    )
    kb.add(
        types.InlineKeyboardButton("✏️ Edit Channel", callback_data="fj_edit"),
        types.InlineKeyboardButton("📝 Join Button Text", callback_data="fj_text"),
    )
    kb.add(
        types.InlineKeyboardButton(
            "🟢 Force Join ON" if settings()["force_join_enabled"] else "🔴 Force Join OFF",
            callback_data="fj_toggle"
        ),
        types.InlineKeyboardButton("👀 Preview", callback_data="fj_preview"),
    )
    return kb

def free_code_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📝 Button Text", callback_data="fc_btn"),
        types.InlineKeyboardButton("📝 Code Text", callback_data="fc_code"),
    )
    kb.add(
        types.InlineKeyboardButton("🔗 Code URL", callback_data="fc_url"),
        types.InlineKeyboardButton(
            "🟢 ON" if settings()["free_code_enabled"] else "🔴 OFF",
            callback_data="fc_toggle"
        )
    )
    kb.add(
        types.InlineKeyboardButton("⏱ Delay", callback_data="fc_delay"),
        types.InlineKeyboardButton("💬 Verification Text", callback_data="fc_verify"),
    )
    kb.add(
        types.InlineKeyboardButton("✅ Success Text", callback_data="fc_success"),
        types.InlineKeyboardButton("❌ Failed Text", callback_data="fc_failed"),
    )
    return kb

def buttons_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Add", callback_data="btn_add"),
        types.InlineKeyboardButton("➖ Remove", callback_data="btn_remove"),
    )
    kb.add(
        types.InlineKeyboardButton("✏️ Edit Name", callback_data="btn_names"),
        types.InlineKeyboardButton("🔗 Edit URL", callback_data="btn_urls"),
    )
    kb.add(
        types.InlineKeyboardButton("↕️ Reorder", callback_data="btn_reorder"),
        types.InlineKeyboardButton("📊 Clicks", callback_data="btn_clicks"),
    )
    kb.add(types.InlineKeyboardButton("😀 Change Button Emoji", callback_data="btn_emoji"))
    return kb

def url_menu():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🔗 List URLs / Status", callback_data="url_list"))
    kb.add(types.InlineKeyboardButton("✏️ Change URL", callback_data="url_change"))
    kb.add(types.InlineKeyboardButton("🔄 Reset URL", callback_data="url_reset"))
    kb.add(types.InlineKeyboardButton("0️⃣ Reset Click Counter", callback_data="url_reset_clicks"))
    return kb

def user_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔎 Search User", callback_data="u_search"),
        types.InlineKeyboardButton("🚫 Ban User", callback_data="u_ban"),
    )
    kb.add(
        types.InlineKeyboardButton("✅ Unban User", callback_data="u_unban"),
        types.InlineKeyboardButton("📤 Export", callback_data="u_export"),
    )
    kb.add(
        types.InlineKeyboardButton("💾 Backup Users", callback_data="u_backup"),
        types.InlineKeyboardButton("♻️ Restore Users", callback_data="u_restore"),
    )
    return kb

def bot_settings_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(
            "🛑 Maintenance ON" if not settings()["maintenance"] else "🟢 Maintenance OFF",
            callback_data="set_maintenance"
        ),
        types.InlineKeyboardButton("🔄 Restart", callback_data="set_restart"),
    )
    kb.add(
        types.InlineKeyboardButton("💾 Backup Settings", callback_data="set_backup"),
        types.InlineKeyboardButton("♻️ Restore Settings", callback_data="set_restore"),
    )
    kb.add(
        types.InlineKeyboardButton("📋 Logs", callback_data="set_logs"),
        types.InlineKeyboardButton("⚠️ Error Report", callback_data="set_errors"),
    )
    return kb

def message_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(
            "📌 Auto Pin ON" if settings()["auto_pin"] else "📌 Auto Pin OFF",
            callback_data="msg_pin"
        ),
        types.InlineKeyboardButton(
            "📍 Auto Unpin ON" if settings()["auto_unpin"] else "📍 Auto Unpin OFF",
            callback_data="msg_unpin"
        ),
    )
    kb.add(
        types.InlineKeyboardButton(
            "🗑 Auto Delete ON" if settings()["auto_delete"] else "🗑 Auto Delete OFF",
            callback_data="msg_delete"
        ),
        types.InlineKeyboardButton("📝 Register Message", callback_data="msg_register")
    )
    kb.add(types.InlineKeyboardButton("👀 Welcome Preview", callback_data="wc_preview"))
    kb.add(types.InlineKeyboardButton("🧾 Register Message ON" if settings().get("auto_register_message") else "🧾 Register Message OFF", callback_data="msg_register_toggle"))
    return kb

def admin_manager_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Add Admin", callback_data="am_add"),
        types.InlineKeyboardButton("➖ Remove Admin", callback_data="am_remove"),
    )
    kb.add(
        types.InlineKeyboardButton("👥 View Admins", callback_data="am_list"),
        types.InlineKeyboardButton("👑 Owner Protection", callback_data="am_owner"),
    )
    return kb

# --------------------- ADMIN TEXT PANEL ----------------------

@bot.message_handler(
    func=lambda m: admin_only(m),
    content_types=["text", "photo", "video", "document", "audio", "voice", "animation", "sticker"]
)
def admin_messages(message):
    uid = message.from_user.id
    text = (message.text or "").strip()
    st = get_state(uid)

    # Top-level menus
    if text == "❌ Cancel":
        clear_state(uid)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=admin_keyboard())
        return

    if text == "⬅️ Back":
        clear_state(uid)
        bot.send_message(message.chat.id, "⬅️ Back to Admin Panel.", reply_markup=admin_keyboard())
        return

    if text == "👋 Welcome Control":
        bot.send_message(message.chat.id, "👋 <b>Welcome Control</b>", reply_markup=welcome_menu())
        return
    if text == "📢 Force Join":
        bot.send_message(message.chat.id, "📢 <b>Force Join Control</b>", reply_markup=force_join_menu())
        return
    if text == "🎁 Free Code":
        bot.send_message(message.chat.id, "🎁 <b>Free Code Control</b>", reply_markup=free_code_menu())
        return
    if text == "🔘 Buttons":
        bot.send_message(message.chat.id, "🔘 <b>Button Manager</b>", reply_markup=buttons_menu())
        return
    if text == "🔗 URL Manager":
        bot.send_message(message.chat.id, "🔗 <b>URL Manager</b>", reply_markup=url_menu())
        return
    if text == "👥 User Manager":
        bot.send_message(message.chat.id, "👥 <b>User Manager</b>", reply_markup=user_menu())
        return
    if text == "🛠 Bot Settings":
        bot.send_message(message.chat.id, "🛠 <b>Bot Settings</b>", reply_markup=bot_settings_menu())
        return
    if text == "📌 Message Control":
        bot.send_message(message.chat.id, "📌 <b>Message Control</b>", reply_markup=message_menu())
        return
    if text == "👮 Admin Manager":
        if not is_owner(uid):
            bot.send_message(message.chat.id, "👑 Only the owner can manage admins.")
            return
        bot.send_message(message.chat.id, "👮 <b>Admin Manager</b>", reply_markup=admin_manager_menu())
        return
    if text == "⚙️ System Settings":
        bot.send_message(
            message.chat.id,
            system_summary(),
            reply_markup=back_keyboard()
        )
        return
    if text == "📊 Analytics":
        bot.send_message(message.chat.id, analytics_text(message.from_user.id))
        return
    if text == "📈 Reports":
        bot.send_message(message.chat.id, reports_text())
        return
    if text == "📤 Broadcast":
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("📝 Text/Media", callback_data="bc_copy"), types.InlineKeyboardButton("↪️ Forward", callback_data="bc_forward"))
        kb.add(types.InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel"))
        bot.send_message(message.chat.id, "📤 <b>Broadcast Mode</b>\nChoose Copy or Forward mode.", reply_markup=kb)
        return

    # State handlers
    name = st.get("name")

    if name == "welcome_photo":
        if message.content_type == "photo":
            file_id = message.photo[-1].file_id
            settings()["welcome_photo"] = file_id
        else:
            settings()["welcome_photo"] = text
        save_db(); clear_state(uid)
        bot.send_message(message.chat.id, "✅ Welcome photo updated.", reply_markup=admin_keyboard())
        return

    if name == "welcome_msg":
        settings()["welcome_caption"] = text
        save_db(); clear_state(uid)
        bot.send_message(message.chat.id, "✅ Welcome message updated.", reply_markup=admin_keyboard())
        return

    if name == "free_btn":
        settings()["free_code_btn_text"] = text
        save_db(); clear_state(uid)
        bot.send_message(message.chat.id, "✅ Free Code button text updated.", reply_markup=admin_keyboard())
        return

    if name == "code_prefix":
        settings()["code_prefix"] = text
        save_db(); clear_state(uid)
        bot.send_message(message.chat.id, "✅ Code prefix updated.", reply_markup=admin_keyboard())
        return

    if name == "code_url":
        settings()["code_url"] = text
        save_db(); clear_state(uid)
        bot.send_message(message.chat.id, "✅ Code URL saved.", reply_markup=admin_keyboard())
        return

    if name == "delay":
        try:
            sec = int(text)
            if sec < 0 or sec > 300:
                raise ValueError
            settings()["verification_delay_seconds"] = sec
            settings()["verification_delay_enabled"] = sec > 0
            save_db(); clear_state(uid)
            bot.send_message(message.chat.id, f"✅ Verification delay set to {sec} seconds.")
        except ValueError:
            bot.send_message(message.chat.id, "❌ Enter a number from 0 to 300.")
        return

    if name in ("verify_text", "success_text", "failed_text"):
        key = {
            "verify_text": "verification_text",
            "success_text": "verification_success_text",
            "failed_text": "verification_failed_text"
        }[name]
        settings()[key] = text
        save_db(); clear_state(uid)
        bot.send_message(message.chat.id, "✅ Text updated.", reply_markup=admin_keyboard())
        return

    if name == "channel_add":
        raw = text.strip()
        if not raw:
            bot.send_message(message.chat.id, "❌ Format: @channel | https://t.me/channel (link optional)")
            return
        parts = [x.strip() for x in raw.split("|", 1)]
        ref = parts[0]
        link = parts[1] if len(parts) == 2 else (f"https://t.me/{ref.lstrip('@')}" if ref.startswith("@") else "")
        entry = {"username": ref, "link": link, "name": ref}
        settings()["channels"].append(entry)
        save_db(); clear_state(uid)
        bot.send_message(message.chat.id, f"✅ Channel added: <code>{safe_html(ref)}</code>")
        return

    if name == "channel_edit":
        parts = [x.strip() for x in text.split("|", 2)]
        if len(parts) < 2:
            bot.send_message(message.chat.id, "Format: @channel | new_username_or_id | new_link")
            return
        old_ref = parts[0]; new_ref = parts[1]; new_link = parts[2] if len(parts) > 2 else (f"https://t.me/{new_ref.lstrip('@')}" if new_ref.startswith("@") else "")
        for i, ch in enumerate(settings()["channels"]):
            ref, _, _ = channel_parts(ch)
            if ref == old_ref:
                settings()["channels"][i] = {"username": new_ref, "link": new_link, "name": new_ref}
                save_db(); clear_state(uid); bot.send_message(message.chat.id, "✅ Channel username/link updated."); return
        bot.send_message(message.chat.id, "❌ Old channel not found.")
        return

    if name == "join_btn_text":
        settings()["join_button_text"] = text
        save_db(); clear_state(uid)
        bot.send_message(message.chat.id, "✅ Join button text updated.")
        return

    if name == "broadcast_btn":
        settings()["broadcast_button_text"] = text
        save_db(); clear_state(uid)
        bot.send_message(message.chat.id, "✅ Broadcast button text updated.")
        return

    if name == "register_msg":
        settings()["register_message"] = text
        save_db(); clear_state(uid)
        bot.send_message(message.chat.id, "✅ Register message updated.")
        return

    if name == "auto_delete_sec":
        try:
            seconds = int(text)
            if seconds < 0:
                raise ValueError
            settings()["auto_delete_seconds"] = seconds
            save_db(); clear_state(uid)
            bot.send_message(message.chat.id, f"✅ Auto-delete delay: {seconds}s")
        except ValueError:
            bot.send_message(message.chat.id, "❌ Enter a valid number.")
        return

    if name == "admin_add":
        try:
            target = int(text)
            if not is_owner(uid):
                bot.send_message(message.chat.id, "❌ Owner only.")
                return
            add_admin_if_missing(target)
            clear_state(uid)
            bot.send_message(message.chat.id, f"✅ Admin added: <code>{target}</code>")
        except ValueError:
            bot.send_message(message.chat.id, "❌ Send a numeric Telegram user ID.")
        return

    if name == "admin_remove":
        try:
            target = int(text)
            if target == OWNER_ID:
                bot.send_message(message.chat.id, "🛡️ Owner protection: owner cannot be removed.")
                return
            if target in DB["admins"]:
                DB["admins"].remove(target)
                save_db()
                clear_state(uid)
                bot.send_message(message.chat.id, "✅ Admin removed.")
            else:
                bot.send_message(message.chat.id, "❌ Admin not found.")
        except ValueError:
            bot.send_message(message.chat.id, "❌ Send a numeric Telegram user ID.")
        return

    if name in ("ban", "unban"):
        try:
            target = int(text)
            if str(target) not in DB["users"]:
                bot.send_message(message.chat.id, "❌ User not found in database.")
                return
            DB["users"][str(target)]["blocked"] = (name == "ban")
            save_db(); clear_state(uid)
            bot.send_message(message.chat.id, "✅ User status updated.")
        except ValueError:
            bot.send_message(message.chat.id, "❌ Send numeric user ID.")
        return

    if name == "user_search":
        target = text.lstrip("@")
        matches = []
        for u in DB["users"].values():
            if target in str(u.get("id")) or target.lower() in str(u.get("username", "")).lower():
                matches.append(u)
        if not matches:
            bot.send_message(message.chat.id, "❌ No user found.")
        else:
            lines = []
            for u in matches[:20]:
                lines.append(
                    f"👤 <code>{u['id']}</code> @{safe_html(u.get('username',''))}\n"
                    f"Blocked: {u.get('blocked', False)}"
                )
            bot.send_message(message.chat.id, "\n\n".join(lines))
        clear_state(uid)
        return

    if name == "url_change":
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            bot.send_message(message.chat.id, "Format: <link_id> <new_url>")
            return
        lid, new_url = parts
        if lid not in DB["links"]:
            bot.send_message(message.chat.id, "❌ Link ID not found.")
            return
        if not valid_url(new_url):
            bot.send_message(message.chat.id, "❌ Invalid URL.")
            return
        DB["links"][lid]["url"] = new_url
        DB["links"][lid]["clicks"] = 0
        DB["links"][lid]["last_click"] = None
        save_db(); clear_state(uid)
        bot.send_message(message.chat.id, "✅ URL changed. Click counter reset to 0.")
        return

    if name == "url_reset":
        try:
            lid = text.strip()
            if lid not in DB["links"]:
                raise ValueError
            DB["links"][lid]["url"] = ""
            reset_link_clicks(lid)
            clear_state(uid)
            bot.send_message(message.chat.id, "✅ URL reset and click counter cleared.")
        except ValueError:
            bot.send_message(message.chat.id, "❌ Link ID not found.")
        return

    if name == "url_reset_clicks":
        lid = text.strip()
        if reset_link_clicks(lid):
            clear_state(uid)
            bot.send_message(message.chat.id, "✅ Click counter reset to 0.")
        else:
            bot.send_message(message.chat.id, "❌ Link ID not found.")
        return

    if name == "button_add_name":
        set_state(uid, "button_add_url", button_name=text)
        bot.send_message(message.chat.id, "Now send the button URL.")
        return

    if name == "button_add_url":
        if not valid_url(text):
            bot.send_message(message.chat.id, "❌ Invalid URL.")
            return
        bid = "b" + uuid.uuid4().hex[:8]
        settings()["welcome_buttons"].append({
            "id": bid, "text": st.get("button_name", "Button"),
            "url": text, "clicks": 0
        })
        save_db(); clear_state(uid)
        bot.send_message(message.chat.id, "✅ Button added.")
        return

    if name == "button_emoji":
        idx = st.get("idx")
        if idx is not None and 0 <= idx < len(settings()["welcome_buttons"]):
            emoji = text.strip().split()[0] if text.strip() else ""
            if not emoji:
                bot.send_message(message.chat.id, "❌ Send an emoji."); return
            old = settings()["welcome_buttons"][idx]["text"]
            cleaned = re.sub(r"^[\U0001F300-\U0001FAFF\u2600-\u27BF]+\s*", "", old)
            settings()["welcome_buttons"][idx]["text"] = f"{emoji} {cleaned}"
            save_db(); clear_state(uid); bot.send_message(message.chat.id, "✅ Button emoji updated.")
        return

    if name == "button_name":
        idx = st.get("idx")
        if idx is not None and 0 <= idx < len(settings()["welcome_buttons"]):
            settings()["welcome_buttons"][idx]["text"] = text
            save_db(); clear_state(uid)
            bot.send_message(message.chat.id, "✅ Button name updated.")
        return

    if name == "button_url":
        idx = st.get("idx")
        if idx is not None and 0 <= idx < len(settings()["welcome_buttons"]):
            if not valid_url(text):
                bot.send_message(message.chat.id, "❌ Invalid URL.")
                return
            settings()["welcome_buttons"][idx]["url"] = text
            save_db(); clear_state(uid)
            bot.send_message(message.chat.id, "✅ Button URL updated.")
        return

    if name == "button_reorder":
        try:
            order = [int(x) - 1 for x in text.split()]
            buttons = settings()["welcome_buttons"]
            if sorted(order) != list(range(len(buttons))):
                raise ValueError
            settings()["welcome_buttons"] = [buttons[i] for i in order]
            save_db(); clear_state(uid)
            bot.send_message(message.chat.id, "✅ Button order updated.")
        except Exception:
            bot.send_message(
                message.chat.id,
                "❌ Send all positions exactly once. Example for 4 buttons: <code>4 2 1 3</code>"
            )
        return

    if name == "broadcast":
        clear_state(uid)
        perform_broadcast(message)
        return

    if name == "restore_db":
        if message.content_type == "document":
            try:
                file_info = bot.get_file(message.document.file_id)
                raw = bot.download_file(file_info.file_path)
                restored = json.loads(raw.decode("utf-8"))
                if not isinstance(restored, dict) or "users" not in restored:
                    raise ValueError
                DB["users"] = restored["users"]
                save_db()
                clear_state(uid)
                bot.send_message(message.chat.id, "♻️ Users database restored.")
            except Exception:
                bot.send_message(message.chat.id, "❌ Invalid users backup.")
        else:
            bot.send_message(message.chat.id, "📎 Send the JSON backup file.")
        return

    if name == "restore_settings":
        if message.content_type == "document":
            try:
                file_info = bot.get_file(message.document.file_id)
                raw = bot.download_file(file_info.file_path)
                restored = json.loads(raw.decode("utf-8"))
                if "settings" not in restored:
                    raise ValueError
                DB["settings"] = restored["settings"]
                save_db()
                clear_state(uid)
                bot.send_message(message.chat.id, "♻️ Settings restored.")
            except Exception:
                bot.send_message(message.chat.id, "❌ Invalid settings backup.")
        else:
            bot.send_message(message.chat.id, "📎 Send the JSON settings backup.")
        return

# ---------------------- INLINE ADMIN -------------------------

@bot.callback_query_handler(func=lambda c: c.from_user and is_admin(c.from_user.id))
def admin_callbacks(call):
    uid = call.from_user.id
    data = call.data
    chat_id = call.message.chat.id

    def answer(msg="Done"):
        try:
            bot.answer_callback_query(call.id, msg)
        except Exception:
            pass

    # Broadcast mode
    if data == "bc_copy":
        set_state(uid, "broadcast", mode="copy"); answer(); bot.send_message(chat_id, "📤 Send the message/media to copy-broadcast. /cancel to cancel."); return
    if data == "bc_forward":
        set_state(uid, "broadcast", mode="forward"); answer(); bot.send_message(chat_id, "↪️ Forward the message here to broadcast as forward. /cancel to cancel."); return
    if data == "bc_cancel":
        clear_state(uid); answer("Cancelled"); return

    # Welcome
    if data == "wc_toggle":
        settings()["welcome_enabled"] = not settings()["welcome_enabled"]
        save_db(); answer()
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=welcome_menu())
        return
    if data == "wc_photo":
        set_state(uid, "welcome_photo")
        answer()
        bot.send_message(chat_id, "🖼 Send a photo or photo URL.")
        return
    if data == "wc_msg":
        set_state(uid, "welcome_msg")
        answer()
        bot.send_message(chat_id, "📝 Send the new welcome message.")
        return
    if data == "wc_names":
        choose_button(chat_id, uid, "name")
        answer()
        return
    if data == "wc_urls":
        choose_button(chat_id, uid, "url")
        answer()
        return
    if data == "wc_preview":
        answer()
        send_welcome(chat_id, uid)
        return

    # Force join
    if data == "fj_toggle":
        settings()["force_join_enabled"] = not settings()["force_join_enabled"]
        save_db(); answer()
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=force_join_menu())
        return
    if data == "fj_add":
        set_state(uid, "channel_add")
        answer()
        bot.send_message(chat_id, "➕ Send channel username/ID. Example: <code>@mychannel</code>")
        return
    if data == "fj_edit":
        set_state(uid, "channel_edit")
        answer()
        bot.send_message(chat_id, "Format: <code>@oldchannel @newchannel</code>")
        return
    if data == "fj_text":
        set_state(uid, "join_btn_text")
        answer()
        bot.send_message(chat_id, "📝 Send new Join button text.")
        return
    if data == "fj_remove":
        answer()
        if not settings()["channels"]:
            bot.send_message(chat_id, "No channels.")
            return
        kb = types.InlineKeyboardMarkup()
        for i, ch in enumerate(settings()["channels"]):
            ref, _, name = channel_parts(ch)
            kb.add(types.InlineKeyboardButton(f"❌ {name}", callback_data=f"fj_del_{i}"))
        bot.send_message(chat_id, "Choose channel to remove:", reply_markup=kb)
        return
    if data == "fj_preview":
        answer()
        bot.send_message(chat_id, settings()["force_join_message"], reply_markup=force_join_keyboard())
        return
    if data.startswith("fj_del_"):
        idx = int(data.split("_")[-1])
        if 0 <= idx < len(settings()["channels"]):
            removed = settings()["channels"].pop(idx)
            save_db()
            answer("Removed")
            bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id,
                                  text=f"✅ Removed <code>{safe_html(removed)}</code>")
        return

    # Free code
    if data == "fc_toggle":
        settings()["free_code_enabled"] = not settings()["free_code_enabled"]
        save_db(); answer()
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=free_code_menu())
        return
    if data == "fc_btn":
        set_state(uid, "free_btn"); answer()
        bot.send_message(chat_id, "📝 Send new Free Code button text.")
        return
    if data == "fc_code":
        set_state(uid, "code_prefix"); answer()
        bot.send_message(chat_id, "📝 Send code prefix. Example: <code>YONO</code>")
        return
    if data == "fc_url":
        set_state(uid, "code_url"); answer()
        bot.send_message(chat_id, "🔗 Send the URL you want to save for the Free Code section.")
        return
    if data == "fc_delay":
        set_state(uid, "delay"); answer()
        bot.send_message(chat_id, "⏱ Send delay in seconds. Example: <code>5</code>, <code>3</code>, <code>10</code>. Use 0 to disable.")
        return
    if data == "fc_verify":
        set_state(uid, "verify_text"); answer()
        bot.send_message(chat_id, "💬 Send verification/loading message. Use {seconds} for the delay.")
        return
    if data == "fc_success":
        set_state(uid, "success_text"); answer()
        bot.send_message(chat_id, "✅ Send success message. Use {code} where the code should appear.")
        return
    if data == "fc_failed":
        set_state(uid, "failed_text"); answer()
        bot.send_message(chat_id, "❌ Send failed verification message.")
        return

    # Buttons
    if data == "btn_add":
        set_state(uid, "button_add_name"); answer()
        bot.send_message(chat_id, "➕ Send new button name.")
        return
    if data == "btn_remove":
        answer()
        choose_button(chat_id, uid, "remove")
        return
    if data in ("btn_names",):
        answer()
        choose_button(chat_id, uid, "name")
        return
    if data in ("btn_urls",):
        answer()
        choose_button(chat_id, uid, "url")
        return
    if data == "btn_reorder":
        set_state(uid, "button_reorder"); answer()
        bot.send_message(
            chat_id,
            "↕️ Send the new order using positions separated by spaces.\n"
            "Example for 4 buttons: <code>4 2 1 3</code>"
        )
        return
    if data == "btn_emoji":
        answer()
        choose_button(chat_id, uid, "emoji")
        return

    if data == "btn_clicks":
        if not is_owner(uid):
            answer("Owner only")
            bot.send_message(chat_id, "⛔ <b>Owner only.</b> Welcome button click counts are visible only to Owner Admin.")
            return
        answer()
        lines = ["🔘 <b>Button Clicks</b>"]
        for i, b in enumerate(settings()["welcome_buttons"], 1):
            lines.append(f"{i}. {safe_html(b['text'])}: <b>{b.get('clicks',0)}</b>")
        bot.send_message(chat_id, "\n".join(lines))
        return
    if data.startswith("btn_remove_"):
        idx = int(data.split("_")[-1])
        if 0 <= idx < len(settings()["welcome_buttons"]):
            removed = settings()["welcome_buttons"].pop(idx)
            save_db(); answer("Removed")
            bot.send_message(chat_id, f"✅ Removed: {safe_html(removed['text'])}")
        return
    if data.startswith("btn_name_"):
        idx = int(data.split("_")[-1])
        if 0 <= idx < len(settings()["welcome_buttons"]):
            set_state(uid, "button_name", idx=idx)
            answer()
            bot.send_message(chat_id, "✏️ Send new button name.")
        return
    if data.startswith("btn_emoji_"):
        idx = int(data.split("_")[-1])
        if 0 <= idx < len(settings()["welcome_buttons"]):
            set_state(uid, "button_emoji", idx=idx)
            answer(); bot.send_message(chat_id, "😀 Send one emoji (example: 🎁)")
        return
    if data.startswith("btn_url_"):
        idx = int(data.split("_")[-1])
        if 0 <= idx < len(settings()["welcome_buttons"]):
            set_state(uid, "button_url", idx=idx)
            answer()
            bot.send_message(chat_id, "🔗 Send new button URL.")
        return

    # URL manager
    if data == "url_list":
        answer()
        bot.send_message(chat_id, url_status_text())
        return
    if data == "url_change":
        set_state(uid, "url_change"); answer()
        bot.send_message(chat_id, "Format: <code>LINK_ID NEW_URL</code>")
        return
    if data == "url_reset":
        set_state(uid, "url_reset"); answer()
        bot.send_message(chat_id, "Send LINK_ID to reset.")
        return
    if data == "url_reset_clicks":
        set_state(uid, "url_reset_clicks"); answer()
        bot.send_message(chat_id, "Send LINK_ID to reset its click counter.")
        return

    # User manager
    if data == "u_search":
        set_state(uid, "user_search"); answer()
        bot.send_message(chat_id, "Send Telegram ID or username.")
        return
    if data == "u_ban":
        set_state(uid, "ban"); answer()
        bot.send_message(chat_id, "Send user ID to ban.")
        return
    if data == "u_unban":
        set_state(uid, "unban"); answer()
        bot.send_message(chat_id, "Send user ID to unban.")
        return
    if data == "u_export":
        answer(); export_users(chat_id)
        return
    if data == "u_backup":
        answer(); send_users_backup(chat_id)
        return
    if data == "u_restore":
        set_state(uid, "restore_db"); answer()
        bot.send_message(chat_id, "📎 Send users JSON backup file.")
        return

    # Bot settings
    if data == "set_maintenance":
        settings()["maintenance"] = not settings()["maintenance"]
        save_db(); answer()
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=bot_settings_menu())
        return
    if data == "set_restart":
        answer("Restarting")
        bot.send_message(chat_id, "♻️ Restarting bot process. Render will bring the service back online.")
        restart_process(); return
    if data == "set_backup":
        answer(); send_settings_backup(chat_id)
        return
    if data == "set_restore":
        set_state(uid, "restore_settings"); answer()
        bot.send_message(chat_id, "📎 Send settings JSON backup file.")
        return
    if data == "set_logs":
        answer()
        try:
            with open("bot.log", "rb") as f: bot.send_document(chat_id, f, caption="📋 Bot runtime log")
        except Exception as e: bot.send_message(chat_id, f"📋 No log file yet: {safe_html(e)}")
        return
    if data == "set_errors":
        answer()
        errs = settings().get("error_log", [])[-20:]
        if not errs: bot.send_message(chat_id, "✅ No recorded errors.")
        else:
            lines=["⚠️ <b>Recent Errors</b>"]
            for e in errs: lines.append(f"• {safe_html(e.get('time',''))} — <b>{safe_html(e.get('where',''))}</b> — {safe_html(e.get('error',''))}")
            bot.send_message(chat_id, "\n".join(lines))
        return

    # Message control
    if data == "msg_pin":
        settings()["auto_pin"] = not settings()["auto_pin"]; save_db(); answer()
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=message_menu()); return
    if data == "msg_unpin":
        settings()["auto_unpin"] = not settings()["auto_unpin"]; save_db(); answer()
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=message_menu()); return
    if data == "msg_delete":
        settings()["auto_delete"] = not settings()["auto_delete"]; save_db(); answer()
        if settings()["auto_delete"]:
            set_state(uid, "auto_delete_sec")
            bot.send_message(chat_id, "Send auto-delete delay in seconds. 0 = immediately after broadcast.")
        return
    if data == "msg_register":
        set_state(uid, "register_msg"); answer()
        bot.send_message(chat_id, "Send new register message.")
        return
    if data == "msg_register_toggle":
        settings()["auto_register_message"] = not settings().get("auto_register_message", False); save_db(); answer()
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=message_menu()); return

    # Admin manager
    if data == "am_add":
        if not is_owner(uid):
            answer("Owner only"); return
        set_state(uid, "admin_add"); answer()
        bot.send_message(chat_id, "Send numeric Telegram ID to add as admin.")
        return
    if data == "am_remove":
        if not is_owner(uid):
            answer("Owner only"); return
        set_state(uid, "admin_remove"); answer()
        bot.send_message(chat_id, "Send numeric Telegram ID to remove.")
        return
    if data == "am_list":
        answer()
        bot.send_message(
            chat_id,
            "👮 <b>Admins</b>\n" + "\n".join(
                f"• <code>{x}</code>{' 👑 OWNER' if int(x)==OWNER_ID else ''}"
                for x in DB["admins"]
            )
        )
        return
    if data == "am_owner":
        answer()
        bot.send_message(chat_id, f"👑 Owner ID: <code>{OWNER_ID}</code>\n🛡️ Owner Protection: ON")
        return

def choose_button(chat_id, uid, action):
    buttons = settings()["welcome_buttons"]
    if not buttons:
        bot.send_message(chat_id, "No buttons available.")
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    prefix = {
        "remove": "btn_remove_",
        "name": "btn_name_",
        "url": "btn_url_",
        "emoji": "btn_emoji_"
    }[action]
    icon = {"remove":"❌", "name":"✏️", "url":"🔗", "emoji":"😀"}[action]
    for i, b in enumerate(buttons):
        kb.add(types.InlineKeyboardButton(
            f"{icon} {b['text']}",
            callback_data=f"{prefix}{i}"
        ))
    bot.send_message(chat_id, "Choose button:", reply_markup=kb)

# ----------------------- ANALYTICS ---------------------------

def analytics_text(owner_id=None):
    lines = [
        "📊 <b>Analytics</b>",
        "",
        f"👥 Total Users: <b>{user_count()}</b>",
        f"🔗 Total Clicks: <b>{click_count()}</b>",
        f"⚡ Today's Clicks: <b>{DB['stats']['daily_clicks'].get(today_key(), 0)}</b>",
        f"🕒 Last Click: <b>{fmt_time(DB['stats'].get('last_click'))}</b>",
        "",
        "🔘 <b>Per Button Clicks</b>"
    ]
    for i, b in enumerate(settings()["welcome_buttons"], 1):
        lines.append(f"{i}. {safe_html(b['text'])} → <b>{b.get('clicks',0)}</b>")

    lines.append("")
    lines.append("🔗 <b>Tracked Links</b>")
    if not DB["links"]:
        lines.append("No tracked links.")
    else:
        for lid, item in list(DB["links"].items())[-20:]:
            lines.append(
                f"<code>{lid}</code> | {safe_html(item.get('label','Link'))} | "
                f"<b>{item.get('clicks',0)}</b> clicks"
            )
    return "\n".join(lines)

def reports_text():
    daily = DB["stats"].get("daily_clicks", {})
    today = daily.get(today_key(), 0)

    week_total = 0
    month_total = 0
    now_dt = now()
    for d, count in daily.items():
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            age = now_dt - dt
            if age <= timedelta(days=7):
                week_total += int(count)
            if dt.year == now_dt.year and dt.month == now_dt.month:
                month_total += int(count)
        except Exception:
            pass

    return (
        "📈 <b>Reports</b>\n\n"
        f"📅 Today: <b>{today}</b>\n"
        f"🗓 Weekly: <b>{week_total}</b>\n"
        f"📆 Monthly: <b>{month_total}</b>\n"
        f"📊 Total: <b>{click_count()}</b>\n"
        f"👥 Total Users: <b>{user_count()}</b>"
    )

def url_status_text():
    if not DB["links"]:
        return "🔗 No tracked URLs yet."
    lines = ["🔗 <b>URL Status</b>"]
    for lid, item in DB["links"].items():
        lines.append(
            f"<code>{lid}</code>\n"
            f"URL: {safe_html(item.get('url',''))}\n"
            f"Clicks: <b>{item.get('clicks',0)}</b>\n"
            f"Last Click: {fmt_time(item.get('last_click'))}\n"
            f"Status: {'🟢 Active' if item.get('url') else '🔴 Empty'}"
        )
    return "\n\n".join(lines)

def system_summary():
    s = settings()
    return (
        "⚙️ <b>System Settings</b>\n\n"
        f"Maintenance: {'ON' if s['maintenance'] else 'OFF'}\n"
        f"Welcome: {'ON' if s['welcome_enabled'] else 'OFF'}\n"
        f"Force Join: {'ON' if s['force_join_enabled'] else 'OFF'}\n"
        f"Free Code: {'ON' if s['free_code_enabled'] else 'OFF'}\n"
        f"Delay: {s['verification_delay_seconds']} sec\n"
        f"Buttons: {len(s['welcome_buttons'])}\n"
        f"Channels: {len(s['channels'])}\n"
        f"Users: {user_count()}\n"
        f"Admins: {len(DB['admins'])}"
    )

# --------------------- USER BACKUPS --------------------------

def export_users(chat_id):
    path = os.path.join(BACKUP_DIR, "users_export.txt")
    with open(path, "w", encoding="utf-8") as f:
        for uid in DB["users"]:
            f.write(str(uid) + "\n")
    with open(path, "rb") as f:
        bot.send_document(chat_id, f, caption=f"📤 Users exported: {user_count()}")

def send_users_backup(chat_id):
    path = os.path.join(BACKUP_DIR, f"users_backup_{int(time.time())}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"users": DB["users"]}, f, indent=2, ensure_ascii=False)
    with open(path, "rb") as f:
        bot.send_document(chat_id, f, caption=f"💾 Users backup: {user_count()}")

def send_settings_backup(chat_id):
    path = os.path.join(BACKUP_DIR, f"settings_backup_{int(time.time())}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"settings": settings()}, f, indent=2, ensure_ascii=False)
    with open(path, "rb") as f:
        bot.send_document(chat_id, f, caption="💾 Settings backup")

# ----------------------- BROADCAST ---------------------------

BROADCAST_CANCEL = {}

def _broadcast_send_with_retry(send_func, max_retries=5):
    """Send one broadcast item with Telegram 429 retry handling."""
    for attempt in range(max_retries):
        try:
            return send_func()
        except Exception as e:
            retry_after = None
            try:
                retry_after = int(getattr(e, "result_json", {}).get("parameters", {}).get("retry_after", 0))
            except Exception:
                retry_after = None
            if retry_after is None or retry_after <= 0:
                m = re.search(r"retry after (\d+)", str(e), re.I)
                retry_after = int(m.group(1)) if m else 0
            if retry_after > 0 and attempt < max_retries - 1:
                time.sleep(min(retry_after + 1, 30))
                continue
            raise
    return None


def perform_broadcast(message):
    mode = get_state(message.from_user.id).get("mode", "copy")
    target_ids = list(DB["users"].keys())
    total = len(target_ids); success = 0; failed = 0
    extracted = extract_url(message.text if message.content_type == "text" else getattr(message, "caption", ""))
    tracked_markup = None
    if extracted and valid_url(extracted):
        lid = create_tracked_link(extracted, settings()["broadcast_button_text"])
        tracked_markup = types.InlineKeyboardMarkup()
        tracked_markup.add(types.InlineKeyboardButton(settings()["broadcast_button_text"], callback_data=f"track_{lid}"))
    BROADCAST_CANCEL[message.from_user.id] = False
    progress = bot.send_message(message.chat.id, f"📤 Broadcast started... 0/{total}")

    for n, uid in enumerate(target_ids, 1):
        if BROADCAST_CANCEL.get(message.from_user.id):
            break
        try:
            chat = int(uid)
            sent = None

            if mode == "forward":
                # Keep the message as a real Telegram forward.
                sent = _broadcast_send_with_retry(
                    lambda: bot.forward_message(chat, message.chat.id, message.message_id)
                )
                # Telegram's forward_message API does not accept reply_markup.
                # Therefore the tracked Register button is sent immediately after the forward.
                if sent and tracked_markup:
                    _broadcast_send_with_retry(
                        lambda: bot.send_message(chat, settings()["broadcast_button_text"], reply_markup=tracked_markup)
                    )
            elif message.content_type == "text":
                sent = _broadcast_send_with_retry(
                    lambda: bot.send_message(chat, message.text, reply_markup=tracked_markup)
                )
            elif message.content_type == "photo":
                sent = _broadcast_send_with_retry(
                    lambda: bot.send_photo(chat, message.photo[-1].file_id, caption=message.caption or "", reply_markup=tracked_markup)
                )
            elif message.content_type == "video":
                sent = _broadcast_send_with_retry(
                    lambda: bot.send_video(chat, message.video.file_id, caption=message.caption or "", reply_markup=tracked_markup)
                )
            elif message.content_type == "document":
                sent = _broadcast_send_with_retry(
                    lambda: bot.send_document(chat, message.document.file_id, caption=message.caption or "", reply_markup=tracked_markup)
                )
            elif message.content_type == "voice":
                sent = _broadcast_send_with_retry(
                    lambda: bot.send_voice(chat, message.voice.file_id, caption=message.caption or "", reply_markup=tracked_markup)
                )
            elif message.content_type == "animation":
                sent = _broadcast_send_with_retry(
                    lambda: bot.send_animation(chat, message.animation.file_id, caption=message.caption or "", reply_markup=tracked_markup)
                )
            elif message.content_type == "sticker":
                sent = _broadcast_send_with_retry(lambda: bot.send_sticker(chat, message.sticker.file_id))
            elif message.content_type == "audio":
                sent = _broadcast_send_with_retry(
                    lambda: bot.send_audio(chat, message.audio.file_id, caption=message.caption or "", reply_markup=tracked_markup)
                )
            else:
                raise ValueError("Unsupported content type")

            success += 1

            if sent and settings().get("auto_register_message") and extracted and not (mode == "forward" and tracked_markup):
                try:
                    _broadcast_send_with_retry(
                        lambda: bot.send_message(chat, settings()["register_message"], reply_markup=tracked_markup)
                    )
                except Exception:
                    pass

            # New broadcast/forward: remove the previous pin first, then pin the new message.
            if sent and settings().get("auto_pin"):
                try:
                    bot.unpin_chat_message(chat)
                except Exception:
                    pass
                try:
                    bot.pin_chat_message(chat, sent.message_id, disable_notification=True)
                except Exception:
                    pass

            if sent and settings().get("auto_delete"):
                threading.Timer(
                    max(0, int(settings()["auto_delete_seconds"])),
                    delete_message_safe,
                    args=(chat, sent.message_id)
                ).start()

        except Exception as e:
            failed += 1
            log.warning("Broadcast failed to %s: %s", uid, e)
            record_error("broadcast", e)

        # Stay below Telegram's normal broadcast throughput and let 429 retries handle bursts.
        time.sleep(0.04)
        if n % 25 == 0 or n == total:
            try:
                bot.edit_message_text(
                    f"📤 Broadcast progress: {n}/{total}\n✅ {success}  ❌ {failed}",
                    message.chat.id,
                    progress.message_id
                )
            except Exception:
                pass

    BROADCAST_CANCEL.pop(message.from_user.id, None)
    DB["broadcasts"].append({
        "time": now().isoformat(), "total": total, "success": success,
        "failed": failed, "type": message.content_type, "mode": mode, "url": extracted
    })
    save_db()
    bot.send_message(
        message.chat.id,
        "📤 <b>Broadcast Report</b>\n\n"
        f"👥 Total: <b>{total}</b>\n"
        f"✅ Sent: <b>{success}</b>\n"
        f"❌ Failed: <b>{failed}</b>\n"
        f"🔗 URL: <code>{safe_html(extracted or 'None')}</code>\n"
        f"📌 Mode: <b>{safe_html(mode)}</b>"
    )

def delete_message_safe(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass

# ---------------------- COMMANDS -----------------------------

@bot.message_handler(commands=["cancel"])
def cancel_cmd(message):
    if is_admin(message.from_user.id):
        clear_state(message.from_user.id)
        bot.send_message(message.chat.id, "❌ Cancelled.", reply_markup=admin_keyboard())

# ---------------------- HEALTH CHECK -------------------------

@app.route("/")
def home():
    return "YonoVoucher2Bot is running."

@app.route("/health")
def health():
    return {"status": "ok", "users": user_count()}

def flask_run():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

# ------------------------- MAIN ------------------------------

if __name__ == "__main__":
    threading.Thread(target=flask_run, daemon=True).start()
    log.info("Bot polling started.")
    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=30,
        skip_pending=True
    )
