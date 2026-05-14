import os
import sys
import threading
import time
from re import search

# ---------- HACK to import the REAL telegram library (avoid conflict with your local telegram.py) ----------
original_path = sys.path.copy()
sys.path = [p for p in sys.path if p != '' and p != os.getcwd() and not p.endswith('/.')]
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
sys.path = original_path
# -------------------------------------------------------------------------------------------------------

# Now import your local modules (they will find your local telegram.py)
from utilitys import config_loader, LOGO, display, logger
from auto_proxy import Proxy
from telegram import Api   # this is YOUR local telegram.py

# -------------------------------------------------------------------
# Original CLI global variables (unchanged)
THREADS = 400
stop_flag = False          # added for bot stop command
target_views = 0           # added for target limit
sent_views = 0             # added to track sent views

# Original CLI functions (copied from your code)
def view_updater(telegram_api):
    while not stop_flag:
        try:
            Api.views(telegram_api)
        except Exception as e:
            logger(e)
        time.sleep(2)

def cli():
    _display = display()
    while not stop_flag:
        try:
            # Clear screen (works on Railway? print newlines instead to keep logs)
            print("\n" * 2)
            _display()
        except Exception as e:
            logger(e)
        time.sleep(2)

def start(api, auto_proxies):
    global sent_views, stop_flag, target_views
    auto_proxies.init()
    proxy_list = list(auto_proxies.proxies)
    if not proxy_list:
        print("No proxies available.")
        return
    proxy_index = 0
    total_proxies = len(proxy_list)
    # Original logic: loop forever, but we add target check
    while not stop_flag and sent_views < target_views:
        proxy_type, proxy = proxy_list[proxy_index % total_proxies]
        try:
            api.send_view(proxy, proxy_type)
            sent_views += 1
            proxy_index += 1
            # Small delay to match original thread-based speed
            time.sleep(0.05)
        except Exception:
            proxy_index += 1
            continue

# -------------------------------------------------------------------
# Telegram bot handlers
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

        # Load config and create objects exactly as original CLI
        http, socks4, socks5 = config_loader()
        auto_proxies = Proxy(http_sources=http, socks4_sources=socks4, socks5_sources=socks5)
        api = Api(channel=channel, post=post)

        await update.message.reply_text(
            f"🚀 Starting view sender for `{channel}/{post}`.\n"
            f"Target: **{target_views}** views.\n"
            f"Use /stop to cancel.\nProgress will appear in the console (Railway logs)."
        )

        # Start original threads (view_updater and cli)
        threading.Thread(target=view_updater, args=(api,), daemon=True).start()
        threading.Thread(target=cli, daemon=True).start()
        # Start the main view sender (original start() logic)
        threading.Thread(target=start, args=(api, auto_proxies), daemon=True).start()

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global stop_flag
    stop_flag = True
    await update.message.reply_text("🛑 Stopping view tasks...")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")

# -------------------------------------------------------------------
def main():
    print(LOGO)
    print("🤖 Bot is running. Press Ctrl+C to stop.")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("No TELEGRAM_BOT_TOKEN set in environment variables.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
