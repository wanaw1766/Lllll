import os
import sys
import threading
import time
from re import search
from threading import active_count
from time import sleep as swait

# ---------- HACK: import the real telegram library (avoid conflict with your local telegram.py) ----------
original_path = sys.path.copy()
sys.path = [p for p in sys.path if p != '' and p != os.getcwd() and not p.endswith('/.')]
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
sys.path = original_path
# -------------------------------------------------------------------------------------------------------

# Your local modules (they will find your local telegram.py)
from utilitys import config_loader, LOGO, logger, THREADS
from auto_proxy import Proxy
from telegram import Api   # your original telegram.py

# ---------- Global flags ----------
stop_flag = False
target_views = 0
sent_views = 0
real_views = 0
lock = threading.Lock()   # to safely update sent_views across threads

# ---------- Original CLI view updater (real views) ----------
def view_updater(api):
    global real_views, stop_flag
    while not stop_flag:
        try:
            Api.views(api)
            real_views = Api.real_views
        except Exception as e:
            logger(e)
        swait(2)

# ---------- Original CLI display (prints to console) ----------
def cli():
    from utilitys import display
    _display = display()
    while not stop_flag:
        try:
            print("\n" * 2)
            _display()
            print(f"Target: {sent_views}/{target_views} | Stop flag: {stop_flag}")
        except Exception as e:
            logger(e)
        swait(2)

# ---------- Send view with counting (used by each thread) ----------
def send_view_with_count(api, proxy, proxy_type):
    global sent_views, stop_flag, target_views, lock
    with lock:
        if sent_views >= target_views or stop_flag:
            return
    # Call original send_view
    api.send_view(proxy, proxy_type)
    with lock:
        sent_views += 1

# ---------- Original START function (thread per proxy) with target limit ----------
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
        # Create a thread that calls send_view_with_count
        thread = threading.Thread(
            target=send_view_with_count,
            args=(api, proxy, proxy_type),
            daemon=True
        )
        threads.append(thread)
        thread.start()

    # Wait for all threads to finish (they exit when target reached)
    for t in threads:
        t.join()

    print(f"View sender finished. Sent {sent_views}/{target_views} views.")
    # Send final message to Telegram (if chat_id provided)
    if chat_id and context:
        import asyncio
        asyncio.run_coroutine_threadsafe(
            context.bot.send_message(chat_id=chat_id, text=f"✅ Finished. Sent {sent_views}/{target_views} views."),
            asyncio.get_event_loop()
        )

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
            f"Using **{THREADS}** concurrent threads.\nUse /stop to cancel."
        )

        chat_id = update.effective_chat.id

        # Start the original CLI threads
        threading.Thread(target=view_updater, args=(api,), daemon=True).start()
        threading.Thread(target=cli, daemon=True).start()
        # Start the thread-per-proxy sender
        threading.Thread(target=start, args=(api, auto_proxies, chat_id, context), daemon=True).start()

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
