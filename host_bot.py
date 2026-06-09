# -*- coding: utf-8 -*-
import asyncio
import ast
import os
import sys
import json
import subprocess
import signal
import uuid
import re
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, 
    ContextTypes, CallbackQueryHandler, ConversationHandler, Application
)

# ==========================================
# ⚙️ SECURE CONFIGURATION (Railway Env Vars)
# ==========================================
# গিটহাবে টোকেন লুকানোর জন্য os.environ ব্যবহার করা হয়েছে
MASTER_BOT_TOKEN = os.environ.get("MASTER_TOKEN", "আপনার_টোকেন_এখানে_দিন")  
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6368178779"))  

HOST_DIR = Path("hosted_bots")
HOST_DIR.mkdir(exist_ok=True)
DB_FILE = HOST_DIR / "bots_db.json"

ASK_TOKEN, ASK_FILE = range(2)

STD_LIBS = {"os","sys","json","re","time","math","random","datetime","collections",
            "asyncio","typing","pathlib","logging","hashlib","base64","csv",
            "string","socket","ssl","sqlite3","urllib","subprocess","signal","uuid"}

PACKAGE_MAPPING = {"telegram": "python-telegram-bot", "cv2": "opencv-python", 
                   "bs4": "beautifulsoup4", "PIL": "Pillow", "yaml": "PyYAML"}

def load_db() -> dict:
    if DB_FILE.exists():
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "bots" not in data: 
                    return {"moderators": [], "users": {str(ADMIN_ID): 999}, "bots": data}
                return data
        except: pass
    return {"moderators": [], "users": {str(ADMIN_ID): 999}, "bots": {}}

def save_db(data: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

db = load_db()
active_processes = {}  

def is_mod(uid): return uid == ADMIN_ID or uid in db.get("moderators", [])
def is_approved(uid): return is_mod(uid) or str(uid) in db.get("users", {})
def get_limit(uid): return 999 if is_mod(uid) else db.get("users", {}).get(str(uid), 0)

# ==========================================
# 🛡️ WATCHDOG (Spam Message Removed & Loop Fixed)
# ==========================================
async def watchdog(bot: Bot):
    while True:
        await asyncio.sleep(30) 
        for b_id, b_info in list(db["bots"].items()):
            if b_info.get("status") == "running":
                proc = active_processes.get(b_id)
                if proc is None or proc.poll() is not None:
                    # 🌟 FIX: স্প্যামিং মেসেজ মুছে দেওয়া হয়েছে এবং স্ট্যাটাস Error করা হয়েছে
                    db["bots"][b_id]["status"] = "error"
                    save_db(db)
                    print(f"Watchdog: Bot {b_info['bot_username']} crashed. Status set to error.")

async def post_init(app: Application):
    for b_id, b_info in db["bots"].items():
        if b_info.get('status') == 'running':
            await start_bot_process(b_id)
    asyncio.create_task(watchdog(app.bot))
    print("🚀 Cloud Master Server Running...")

# ==========================================
# ⚙️ BOT PROCESS CONTROLLER
# ==========================================
def extract_imports(code: str) -> set:
    try:
        imports = set()
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.Import):
                for alias in node.names: imports.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split('.')[0])
        return imports - STD_LIBS
    except: return set()

async def auto_install_requirements(update: Update, code: str, status_msg):
    required_libs = extract_imports(code)
    pkgs = [PACKAGE_MAPPING.get(lib, lib) for lib in required_libs]
    if pkgs:
        await status_msg.edit_text(f"🔍 Installing requirements: {', '.join(pkgs)}...")
        try:
            proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "pip", "install", *pkgs, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await proc.communicate()
        except: pass

async def start_bot_process(bot_id: str):
    b_info = db["bots"].get(bot_id)
    if not b_info: return False, "Bot not found."
    
    file_path = b_info['file_path']
    err_log_path = HOST_DIR / f"{bot_id}_error.log"
    env = os.environ.copy()
    env["HOSTED_BOT_TOKEN"] = b_info['token'] 
    
    try:
        err_file = open(err_log_path, "w", encoding="utf-8")
        proc = subprocess.Popen([sys.executable, file_path], env=env, stdout=subprocess.DEVNULL, stderr=err_file)
        active_processes[bot_id] = proc
        
        await asyncio.sleep(3)
        if proc.poll() is not None:
            err_file.close()
            with open(err_log_path, "r", encoding="utf-8") as f: err = f.read().strip()
            if bot_id in active_processes: del active_processes[bot_id]
            db["bots"][bot_id]['status'] = "error"
            save_db(db)
            return False, err[-500:]
            
        db["bots"][bot_id]['status'] = "running"
        save_db(db)
        return True, ""
    except Exception as e: return False, str(e)

def stop_bot_process(bot_id: str):
    proc = active_processes.get(bot_id)
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except:
            try: os.kill(proc.pid, signal.SIGKILL)
            except: pass
        if bot_id in active_processes: del active_processes[bot_id]
        
    if bot_id in db["bots"]:
        db["bots"][bot_id]['status'] = "stopped"
        save_db(db)
    return True

# ==========================================
# 👑 ADMIN COMMANDS & HOSTING
# ==========================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_approved(uid):
        text = f"👋 <b>মাস্টার হোস্টিং প্যানেলে স্বাগতম!</b>\n\nনতুন বট হোস্ট করতে: /host\nআপনার বট দেখতে: /mybots\nলিমিট: {len([b for b in db['bots'].values() if b.get('owner_id')==uid])} / {get_limit(uid)}"
        if is_mod(uid): text += "\n\n<b>👮 Mod Commands:</b>\n/approve ID LIMIT\n/revoke ID\n/allbots"
        if uid == ADMIN_ID: text += "\n/addmod ID\n/delmod ID"
    else:
        text = f"🛑 <b>অ্যাক্সেস ডিনাইড!</b>\nআপনি এই সার্ভারে বট হোস্ট করার অনুমোদিত নন।\n\nআপনার <b>User ID:</b> <code>{uid}</code>"
    await update.message.reply_text(text, parse_mode="HTML")

async def auth_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cmd = update.message.text.split()[0].lower()
    
    if cmd in ["/addmod", "/delmod"] and uid != ADMIN_ID: return
    if cmd in ["/approve", "/revoke"] and not is_mod(uid): return
    
    args = context.args
    if not args: return await update.message.reply_text("⚠️ সঠিক নিয়ম: কমান্ড + ইউজার আইডি")
    target = args[0]

    if cmd == "/addmod":
        if int(target) not in db.get("moderators", []): db["moderators"].append(int(target))
        await update.message.reply_text(f"✅ {target} এখন একজন মডারেটর!")
    elif cmd == "/delmod":
        if int(target) in db.get("moderators", []): db["moderators"].remove(int(target))
        await update.message.reply_text(f"✅ {target} মডারেটর থেকে রিমুভড!")
    elif cmd == "/approve":
        limit = int(args[1]) if len(args) > 1 else 5
        db["users"][target] = limit
        await update.message.reply_text(f"✅ ইউজার {target} কে {limit} টি বট হোস্ট করার অনুমতি দেওয়া হয়েছে!")
    elif cmd == "/revoke":
        if target in db["users"]: del db["users"][target]
        await update.message.reply_text(f"🚫 ইউজার {target} এর অ্যাক্সেস বাতিল করা হয়েছে!")
    save_db(db)

async def host_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_approved(uid): return ConversationHandler.END
    
    user_bots = [b for b in db["bots"].values() if b.get('owner_id') == uid]
    if len(user_bots) >= get_limit(uid):
        await update.message.reply_text("⚠️ আপনার হোস্টিং লিমিট শেষ!")
        return ConversationHandler.END

    msg = await update.message.reply_text("⚡ <b>নতুন বট হোস্ট করুন:</b>\nদয়া করে বটের টোকেন দিন। (বাতিল করতে /cancel)", parse_mode="HTML")
    context.user_data['ui_msg'] = msg
    return ASK_TOKEN

async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = update.message.text.strip()
    try: await update.message.delete()
    except: pass
    ui_msg = context.user_data.get('ui_msg')

    if token == "/cancel":
        await ui_msg.edit_text("❌ হোস্টিং বাতিল।")
        return ConversationHandler.END
        
    await ui_msg.edit_text("⏳ <b>Verifying your clone...</b>", parse_mode="HTML")
    
    try:
        test_bot = Bot(token)
        bot_user = await test_bot.get_me()
        existing_id = next((b_id for b_id, b in db["bots"].items() if b['token'] == token and b['owner_id'] == update.effective_user.id), None)
        
        v_id = uuid.uuid4().hex[:6]
        context.user_data.update({'temp_token': token, 'bot_user': bot_user.username, 'v_id': v_id, 'update_id': existing_id})

        dummy_code = (f"import os, sys\nfrom telegram.ext import ApplicationBuilder, CommandHandler\n"
                      f"async def start(u, c): await u.message.reply_text('<b>আই অ্যাম ভেরিফাইড ইউর কোলন</b>', parse_mode='HTML')\n"
                      f"if __name__ == '__main__': app = ApplicationBuilder().token('{token}').build(); app.add_handler(CommandHandler('start', start)); app.run_polling()")
        
        v_file_path = HOST_DIR / f"temp_v_{v_id}.py"
        with open(v_file_path, "w", encoding="utf-8") as f: f.write(dummy_code)
            
        v_proc = subprocess.Popen([sys.executable, str(v_file_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        context.user_data.update({'v_proc': v_proc, 'v_file_path': v_file_path})

        up_text = "<b>(Smart Update Mode 🔄)</b>" if existing_id else ""
        text = (f"✅ <b>Token Verified! {up_text}</b>\n🤖 @{bot_user.username}\n\n"
                f"👉 আমি অস্থায়ীভাবে বটটি চালু করেছি। আপনার নতুন বটে গিয়ে <b>/start</b> কমান্ড দিয়ে চেক করুন।\n\n"
                f"📥 <b>চেক করা হয়ে গেলে, আপনার আসল <code>.py</code> কোড ফাইলটি আপলোড করুন।</b>")
        await ui_msg.edit_text(text, parse_mode="HTML")
        return ASK_FILE
    except Exception:
        await ui_msg.edit_text("❌ টোকেন ভুল! সঠিক টোকেন দিন অথবা /cancel।")
        return ASK_TOKEN

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    ui_msg = context.user_data.get('ui_msg')
    try: await update.message.delete()
    except: pass

    if not doc or not doc.file_name.endswith(".py"):
        await ui_msg.edit_text("⚠️ দয়া করে একটি সঠিক .py ফাইল দিন।")
        return ASK_FILE

    await ui_msg.edit_text("⚙️ <b>Deploying...</b>", parse_mode="HTML")
    
    v_proc = context.user_data.get('v_proc')
    if v_proc:
        try: v_proc.terminate()
        except: pass
    try: os.remove(context.user_data.get('v_file_path', ''))
    except: pass
    
    await asyncio.sleep(2) 

    file = await doc.get_file()
    code_bytes = await file.download_as_bytearray()
    code_str = re.sub(r'["'][0-9]{8,10}:[a-zA-Z0-9_-]{35,}["']', f'"{context.user_data["temp_token"]}"', code_bytes.decode('utf-8'))
    
    await auto_install_requirements(update, code_str, ui_msg)
    
    existing_id = context.user_data.get('update_id')
    bot_id = existing_id if existing_id else f"bot_{uuid.uuid4().hex[:8]}"
    file_path = HOST_DIR / f"{bot_id}_{doc.file_name}"
    
    if existing_id: stop_bot_process(bot_id)

    with open(file_path, "w", encoding="utf-8") as f: f.write(code_str)

    db["bots"][bot_id] = {
        "id": bot_id, "name": doc.file_name, "owner_id": update.effective_user.id,
        "bot_username": context.user_data['bot_user'], "token": context.user_data['temp_token'],
        "file_path": str(file_path), "status": "stopped"
    }
    save_db(db)
    
    success, err = await start_bot_process(bot_id)
    if success:
        up_msg = "♻️ <b>Bot Updated Successfully!</b>" if existing_id else "🚀 <b>Successfully Hosted!</b>"
        await ui_msg.edit_text(f"{up_msg}\nবট @{context.user_data['bot_user']} রান করছে!\nম্যানেজ করতে: /mybots", parse_mode="HTML")
    else:
        await ui_msg.edit_text(f"❌ <b>Bot Crashed!</b>\n\nError:\n<code>{err}</code>", parse_mode="HTML")
        
    return ConversationHandler.END

async def cancel_host(update: Update, context: ContextTypes.DEFAULT_TYPE):
    v_proc = context.user_data.get('v_proc')
    if v_proc:
        try: v_proc.terminate()
        except: pass
    try: os.remove(context.user_data.get('v_file_path', ''))
    except: pass
    await update.message.reply_text("❌ হোস্টিং বাতিল করা হয়েছে।")
    return ConversationHandler.END

async def mybots_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cmd = update.message.text.split()[0].lower()
    
    if cmd == "/allbots" and is_mod(uid):
        target_bots = db["bots"].items()
        title = "🌐 <b>All Server Bots</b>"
    else:
        target_bots = [(k, v) for k, v in db["bots"].items() if v.get('owner_id') == uid]
        title = "📋 <b>Your Bots</b>"

    if not target_bots: return await update.message.reply_text("কোনো বট পাওয়া যায়নি।")
    await update.message.reply_text(f"{title}: {len(target_bots)} টি", parse_mode="HTML")

    for idx, (b_id, b_info) in enumerate(target_bots, 1):
        status, cpu, ram = b_info['status'], 0.0, 0.0
        
        if status == "running" and b_id in active_processes:
            proc = active_processes[b_id]
            if proc.poll() is None: 
                if psutil:
                    try:
                        p = psutil.Process(proc.pid)
                        ram, cpu = p.memory_info().rss / (1024*1024), p.cpu_percent(interval=0.1)
                    except: pass
            else:
                status, db["bots"][b_id]['status'] = "error"
                save_db(db)
                
        icon = "🟢 Running" if status == "running" else "⚠️ Error" if status == "error" else "🔴 Stopped"
        
        text = f"🤖 <b>{idx}. @{b_info['bot_username']}</b>\n📊 <b>Status:</b> {icon}\n💻 <b>CPU:</b> {cpu}% | 🧠 <b>RAM:</b> {ram:.2f} MB"
        
        kb = [[InlineKeyboardButton("⏹️ Stop", callback_data=f"stop_{b_id}") if status == "running" else InlineKeyboardButton("▶️ Start", callback_data=f"start_{b_id}"),
               InlineKeyboardButton("🗑️ Delete", callback_data=f"del_{b_id}")],
              [InlineKeyboardButton("📝 View Logs", callback_data=f"log_{b_id}")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, bot_id = q.data.split("_", 1)
    b_name = db["bots"].get(bot_id, {}).get('bot_username', 'Bot')
    
    try:
        if action == "stop":
            stop_bot_process(bot_id)
            await q.edit_message_text(f"✅ @{b_name} Stopped.")
        elif action == "start":
            success, err = await start_bot_process(bot_id)
            if success: await q.edit_message_text(f"✅ @{b_name} Started.")
            else: await q.edit_message_text(f"❌ <b>Bot crashed!</b>\n\n<code>{err}</code>", parse_mode="HTML")
        elif action == "log":
            log_path = HOST_DIR / f"{bot_id}_error.log"
            err = "No logs found."
            if os.path.exists(log_path):
                with open(log_path, "r") as f: err = f.read()[-1000:] or "Logs are empty."
            await q.message.reply_text(f"📝 <b>Logs for @{b_name}:</b>\n<code>{err}</code>", parse_mode="HTML")
        elif action == "del":
            stop_bot_process(bot_id)
            for file in HOST_DIR.glob(f"{bot_id}_*"): os.remove(file)
            if bot_id in db["bots"]:
                del db["bots"][bot_id]
                save_db(db)
            await q.edit_message_text(f"🗑️ @{b_name} Deleted completely.")
    except BadRequest: pass

def main():
    app = ApplicationBuilder().token(MASTER_BOT_TOKEN).post_init(post_init).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("host", host_start)],
        states={
            ASK_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token)],
            ASK_FILE: [MessageHandler(filters.Document.ALL, receive_file)]
        },
        fallbacks=[CommandHandler("cancel", cancel_host)]
    )
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler(["addmod", "delmod", "approve", "revoke"], auth_cmds))
    app.add_handler(CommandHandler(["mybots", "allbots"], mybots_cmd))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(button_handler))

    app.run_polling()

if __name__ == "__main__":
    main()
