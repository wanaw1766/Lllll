import os
import sys
import threading
import time
import asyncio
from re import search

# ---------- HACK: temporarily remove current dir to import real telegram library ----------
original_path = sys.path.copy()
sys.path = [p for p in sys.path if p != '' and p != os.getcwd() and not p.endswith('/.')]
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
sys.path = original_path
# ----------------------------------------------------------------------------------------

# Your local modules (they will find your local telegram.py because path is restored)
from utilitys import config_loader, LOGO, logger
from auto_proxy import Proxy
from telegram import Api   # your local telegram.py

# ---------- Global flags ----------
stop_flag = False
target_views = 0
sent_views = 0
real_views = 0
loop = None          # asyncio event loop for sending messages from threads

def safe_send_message(context, chat_id, text):
    """Send a Telegram message from a background thread safely."""
    global loop
    if loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(
            context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown'),
            loop
        )

# ---------- Original view updater (real views) ----------
def view_updater(api, chat_id, context):
    global real_views, stop_flag
    while not stop_flag:
        try:
            Api.views(api)
            real_views = Api.real_views
            # Send progress update every 5 seconds
            safe_send_message(context, chat_id, f"📈 *Progress*\nSent: {sent_views}/{target_views}\nLive views: {real_views}")
        except Exception as e:
            logger(e)
        time.sleep(5)

# ---------- Original CLI display (prints to console logs) ----------
def cli():
    from utilitys import display
    _display = display()
    while not stop_flag:
        try:
            print("\n" * 2)
            _display()
        except Exception as e:
            logger(e)
        time.sleep(2)

# ---------- View sender (exactly like your CLI, but stops at target) ----------
def send_views(api, auto_proxies, chat_id, context):
    global sent_views, stop_flag, target_views
    auto_proxies.init()
    proxy_list = list(auto_proxies.proxies)
    if not proxy_list:
        safe_send_message(context, chat_id, "❌ No proxies available. Stopping.")
        return

    proxy_index = 0
    total = len(proxy_list)
    safe_send_message(context, chat_id, f"🚀 Started sending views. Target: {target_views}")

    while not stop_flag and sent_views < target_views:
        proxy_type, proxy = proxy_list[proxy_index % total]
        try:
            api.send_view(proxy, proxy_type)
            sent_views += 1
            proxy_index += 1
            # Same delay as your original CLI (0.05 seconds)
            time.sleep(0.05)
        except Exception:
            proxy_index += 1
            continue

    safe_send_message(context, chat_id, f"✅ Finished. Sent {sent_views}/{target_views} views.")
    global stop_flag
    stop_flag = True

# ---------- Telegram bot handlers ----------
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
    global target_views, sent_views, stop_flag, loop
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

        # Load config and create objects (same as original CLI)
        http, socks4, socks5 = config_loader()
        auto_proxies = Proxy(http_sources=http, socks4_sources=socks4, socks5_sources=socks5)
        api = Api(channel=channel, post=post)

        await update.message.reply_text(
            f"🚀 Starting view sender for `{channel}/{post}`.\n"
            f"Target: **{target_views}** views.\n"
            f"Progress will be sent every 5 seconds.\nUse /stop to cancel."
        )

        chat_id = update.effective_chat.id
        loop = asyncio.get_running_loop()

        # Start background threads
        threading.Thread(target=send_views, args=(api, auto_proxies, chat_id, context), daemon=True).start()
        threading.Thread(target=view_updater, args=(api, chat_id, context), daemon=True).start()
        threading.Thread(target=cli, daemon=True).start()

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global stop_flag
    stop_flag = True
    await update.message.reply_text("🛑 Stopping view tasks...")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")

# ---------- Main ----------
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
