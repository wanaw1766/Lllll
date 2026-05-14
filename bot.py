import os
import sys
import threading
import time
import asyncio
from re import search
from threading import active_count
from time import sleep as swait

# ------------------------------------------------------------
# TRICK: Force Python to load your local telegram.py as the 'telegram' module
# before any other imports. This ensures that 'from telegram import Api' works.
# ------------------------------------------------------------
import importlib.util

# Load your local telegram.py
local_telegram_path = os.path.join(os.path.dirname(__file__), 'telegram.py')
spec = importlib.util.spec_from_file_location("telegram", local_telegram_path)
local_telegram = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_telegram)

# Replace the 'telegram' module in sys.modules with your local version
sys.modules['telegram'] = local_telegram

# Now import the REAL python-telegram-bot library using a different name
# We need to temporarily remove the current directory to avoid loading your local file again
original_path = sys.path.copy()
sys.path = [p for p in sys.path if p != '' and p != os.getcwd() and not p.endswith('/.')]
import telegram as tg_lib
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
sys.path = original_path
# ------------------------------------------------------------

# Now import your other local modules (they will use the local 'telegram' module because we replaced it)
from utilitys import config_loader, LOGO, logger, THREADS
from auto_proxy import Proxy
from telegram import Api   # This now comes from your local telegram.py

# ------------------------------------------------------------
# Global state
stop_flag = False
target_views = 0
sent_views = 0
real_views = 0
lock = threading.Lock()

# Original view updater
def view_updater(api):
    global real_views, stop_flag
    while not stop_flag:
        try:
            Api.views(api)
            real_views = Api.real_views
        except Exception as e:
            logger(e)
        swait(2)

# Original CLI display
def cli():
    from utilitys import display
    _display = display()
    while not stop_flag:
        try:
            print("\n" * 2)
            _display()
            print(f"Target: {sent_views}/{target_views}")
        except Exception as e:
            logger(e)
        swait(2)

def send_view_with_count(api, proxy, proxy_type):
    global sent_views, stop_flag, target_views, lock
    with lock:
        if sent_views >= target_views or stop_flag:
            return
    api.send_view(proxy, proxy_type)
    with lock:
        sent_views += 1

def start(api, auto_proxies, chat_id, context):
    global sent_views, stop_flag, target_views, lock
    auto_proxies.init()
    proxy_list = list(auto_proxies.proxies)
    if not proxy_list:
        print("No proxies available.")
        return

    threads = []
    for proxy_type, proxy in proxy_list:
        while active_count() > THREADS:
            if stop_flag:
                break
            swait(0.05)
        if stop_flag:
            break
        thread = threading.Thread(target=send_view_with_count, args=(api, proxy, proxy_type), daemon=True)
        threads.append(thread)
        thread.start()

    for t in threads:
        t.join()

    print(f"Finished: sent {sent_views}/{target_views}")
    if chat_id and context:
        asyncio.run_coroutine_threadsafe(
            context.bot.send_message(chat_id=chat_id, text=f"✅ Finished. Sent {sent_views}/{target_views} views."),
            asyncio.get_event_loop()
        )

# ------------------------------------------------------------
# Bot handlers
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎯 Start Viewing", callback_data="start_view")],
        [InlineKeyboardButton("🛑 Stop", callback_data="stop")],
    ]
    await update.message.reply_text(
        "📢 *Telegram Auto Views Bot*\nClick 'Start Viewing' to begin.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "start_view":
        await query.edit_message_text("Send me the Telegram post URL (e.g., `https://t.me/username/123`)")
        context.user_data['waiting_for_url'] = True
    elif data == "stop":
        global stop_flag
        stop_flag = True
        await query.edit_message_text("🛑 Stopping...")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global target_views, sent_views, stop_flag
    if context.user_data.get('waiting_for_url'):
        url = update.message.text
        match = search(r'(https?:\/\/t\.me\/)?([^/]+)/(\d+)', url)
        if not match:
            await update.message.reply_text("❌ Invalid URL. Send again or /cancel.")
            return
        _, channel, post = match.groups()
        context.user_data['channel'] = channel
        context.user_data['post'] = post
        context.user_data['waiting_for_url'] = False
        context.user_data['waiting_for_count'] = True
        await update.message.reply_text("✅ URL accepted. Now send the **number of views** (e.g., `100`):")
        return

    if context.user_data.get('waiting_for_count'):
        try:
            target = int(update.message.text.strip())
            if target <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Please send a valid positive integer (e.g., `500`).")
            return

        target_views = target
        sent_views = 0
        stop_flag = False
        context.user_data['waiting_for_count'] = False

        channel = context.user_data['channel']
        post = context.user_data['post']

        http, socks4, socks5 = config_loader()
        auto_proxies = Proxy(http_sources=http, socks4_sources=socks4, socks5_sources=socks5)
        api = Api(channel=channel, post=post)

        await update.message.reply_text(
            f"🚀 Starting view sender for `{channel}/{post}`.\n"
            f"Target: **{target_views}** views.\n"
            f"Using **{THREADS}** concurrent threads.\nUse /stop to cancel."
        )

        chat_id = update.effective_chat.id

        threading.Thread(target=view_updater, args=(api,), daemon=True).start()
        threading.Thread(target=cli, daemon=True).start()
        threading.Thread(target=start, args=(api, auto_proxies, chat_id, context), daemon=True).start()

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global stop_flag
    stop_flag = True
    await update.message.reply_text("🛑 Stopping view tasks...")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")

def main():
    print(LOGO)
    print("🤖 Bot is running. Press Ctrl+C to stop.")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("No TELEGRAM_BOT_TOKEN set.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
