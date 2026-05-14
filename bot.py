import os
import threading
import time
import asyncio
from re import search
from threading import active_count
from time import sleep as swait

# Real telegram library
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Your local modules (now using tg_views)
from utilitys import config_loader, LOGO, logger, THREADS
from auto_proxy import Proxy
from tg_views import Api

# ---------- Global state ----------
stop_flag = False
target_views = 0
sent_views = 0
real_views = 0
lock = threading.Lock()
progress_message_id = None   # to edit the same message
chat_id = None
app_context = None

# ---------- Progress bar helper ----------
def make_progress_bar(current, total, length=20):
    filled = int(length * current / total)
    return '█' * filled + '░' * (length - filled)

async def update_progress():
    global progress_message_id, chat_id, app_context
    if not chat_id or not app_context:
        return
    bar = make_progress_bar(sent_views, target_views)
    percent = (sent_views / target_views) * 100 if target_views else 0
    text = (
        f"🚀 *View Sender Active*\n"
        f"📊 Progress: `{bar}` {percent:.1f}%\n"
        f"✅ Sent: `{sent_views}` / `{target_views}`\n"
        f"👁️ Live Telegram views: `{real_views}`\n"
        f"⚡ Status: {'Running...' if not stop_flag else 'Stopping...'}"
    )
    if progress_message_id is None:
        msg = await app_context.bot.send_message(chat_id, text, parse_mode='Markdown')
        progress_message_id = msg.message_id
    else:
        try:
            await app_context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_message_id,
                text=text,
                parse_mode='Markdown'
            )
        except Exception:
            pass  # ignore "message not modified"

# ---------- Original view updater (real views) ----------
def view_updater(api):
    global real_views, stop_flag
    while not stop_flag:
        try:
            Api.views(api)
            real_views = Api.real_views
            asyncio.run_coroutine_threadsafe(update_progress(), asyncio.get_event_loop())
        except Exception as e:
            logger(e)
        swait(5)  # update every 5 seconds

# ---------- Original CLI display (prints to console) ----------
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

# ---------- Send view with counting ----------
def send_view_with_count(api, proxy, proxy_type):
    global sent_views, stop_flag, target_views, lock
    with lock:
        if sent_views >= target_views or stop_flag:
            return
    api.send_view(proxy, proxy_type)
    with lock:
        sent_views += 1
        # Update progress every 5 views (or you can do it more often)
        if sent_views % 5 == 0 or sent_views >= target_views:
            asyncio.run_coroutine_threadsafe(update_progress(), asyncio.get_event_loop())

# ---------- Original INFINITE proxy cycling (like your CLI) ----------
def start(api, auto_proxies):
    global sent_views, stop_flag, target_views
    auto_proxies.init()
    proxy_list = list(auto_proxies.proxies)
    if not proxy_list:
        print("No proxies available.")
        return

    # This loop replicates your original CLI: it cycles through all proxies,
    # sends one view per proxy, then restarts (infinite cycle) until target reached.
    while not stop_flag and sent_views < target_views:
        threads = []
        for proxy_type, proxy in proxy_list:
            while active_count() > THREADS:
                if stop_flag or sent_views >= target_views:
                    break
                swait(0.05)
            if stop_flag or sent_views >= target_views:
                break
            thread = threading.Thread(target=send_view_with_count, args=(api, proxy, proxy_type), daemon=True)
            threads.append(thread)
            thread.start()
        for t in threads:
            t.join()
        # After finishing one full cycle, continue to next cycle (if target not reached)
        # This is exactly what your original CLI did with recursive start() call.
        if sent_views >= target_views or stop_flag:
            break
        # Optional: small delay between cycles
        swait(0.5)
    print(f"View sender finished. Sent {sent_views}/{target_views} views.")

# ---------- Telegram bot handlers ----------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global chat_id, app_context, progress_message_id
    chat_id = update.effective_chat.id
    app_context = context
    progress_message_id = None
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
    global stop_flag
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "start_view":
        await query.edit_message_text("Send me the Telegram post URL (e.g., `https://t.me/username/123`)")
        context.user_data['waiting_for_url'] = True
    elif data == "stop":
        stop_flag = True
        await query.edit_message_text("🛑 Stopping...")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global target_views, sent_views, stop_flag, progress_message_id, chat_id
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
        progress_message_id = None
        context.user_data['waiting_for_count'] = False

        channel = context.user_data['channel']
        post = context.user_data['post']

        http, socks4, socks5 = config_loader()
        auto_proxies = Proxy(http_sources=http, socks4_sources=socks4, socks5_sources=socks5)
        api = Api(channel=channel, post=post)

        await update.message.reply_text(
            f"🚀 Starting view sender for `{channel}/{post}`.\n"
            f"Target: **{target_views}** views.\n"
            f"Using **{THREADS}** concurrent threads (cycling through all proxies).\n"
            f"Progress bar will appear here. Use /stop to cancel."
        )

        # Start threads
        threading.Thread(target=view_updater, args=(api,), daemon=True).start()
        threading.Thread(target=cli, daemon=True).start()
        threading.Thread(target=start, args=(api, auto_proxies), daemon=True).start()

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
