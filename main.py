import os
import threading
import asyncio
import random
from re import search
from time import sleep as swait

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Your local modules (make sure utilitys imports from tg_views)
from tg_views import Api
from utilitys import config_loader, LOGO
from auto_proxy import Proxy

# -------------------------------------------------------------------
# Global variables
stop_flag = False
current_status = {
    'active': False,
    'mode': None,
    'channel': None,
    'post': None,
    'target': 0,
    'sent': 0,
    'real_views': 0,
}
progress_msg_id = None
loop = None  # will hold the asyncio event loop

# -------------------------------------------------------------------
def progress_bar(current, total, length=20):
    filled = int(length * current / total)
    return '█' * filled + '░' * (length - filled)

async def update_progress_message(context, chat_id):
    global current_status, stop_flag, progress_msg_id
    if not current_status['active']:
        return
    bar = progress_bar(current_status['sent'], current_status['target'])
    percentage = (current_status['sent'] / current_status['target']) * 100 if current_status['target'] else 0
    text = (
        f"🚀 *{current_status['mode'].upper()} MODE*\n"
        f"📊 Progress:\n`{bar}` {percentage:.1f}%\n"
        f"✅ Sent: `{current_status['sent']}` / `{current_status['target']}` views\n"
        f"👁️ Live Telegram views: `{current_status['real_views']}`\n"
        f"⚡ Status: {'Running...' if not stop_flag else 'Stopping...'}"
    )
    if progress_msg_id is None:
        msg = await context.bot.send_message(chat_id, text, parse_mode='Markdown')
        progress_msg_id = msg.message_id
    else:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_msg_id,
                text=text,
                parse_mode='Markdown'
            )
        except Exception:
            pass  # ignore "Message not modified"

def safe_update_progress(context, chat_id):
    """Thread-safe wrapper to schedule the async progress update."""
    if loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(update_progress_message(context, chat_id), loop)

# -------------------------------------------------------------------
# Original threaded view sender (identical to CLI)
def original_view_sender(channel, post, target, mode, chat_id, context):
    global stop_flag, current_status
    # Load config and proxies exactly as original CLI
    http, socks4, socks5 = config_loader()
    auto_proxies = Proxy(http_sources=http, socks4_sources=socks4, socks5_sources=socks5)
    auto_proxies.init()
    api = Api(channel=channel, post=post)

    proxy_list = list(auto_proxies.proxies)
    if not proxy_list:
        safe_update_progress(context, chat_id)
        return

    views_sent = 0
    if mode == 'direct':
        while views_sent < target and not stop_flag:
            for proxy_type, proxy in proxy_list:
                if views_sent >= target or stop_flag:
                    break
                try:
                    api.send_view(proxy, proxy_type)
                    views_sent += 1
                    current_status['sent'] = views_sent
                    if views_sent % 5 == 0 or views_sent == target:
                        safe_update_progress(context, chat_id)
                except Exception:
                    continue
        safe_update_progress(context, chat_id)

    elif mode == 'random':
        while views_sent < target and not stop_flag:
            proxy_type, proxy = proxy_list[0]
            try:
                api.send_view(proxy, proxy_type)
                views_sent += 1
                current_status['sent'] = views_sent
                safe_update_progress(context, chat_id)
                if views_sent < target:
                    wait_seconds = random.randint(60, 300)
                    for _ in range(wait_seconds):
                        if stop_flag:
                            break
                        swait(1)
            except Exception:
                swait(1)
                continue
        safe_update_progress(context, chat_id)

    current_status['active'] = False

def real_views_updater(channel, post, chat_id, context):
    api = Api(channel=channel, post=post)
    while current_status['active'] and not stop_flag:
        try:
            Api.views(api)
            current_status['real_views'] = Api.real_views
            safe_update_progress(context, chat_id)
        except Exception:
            pass
        swait(5)

# -------------------------------------------------------------------
# Bot command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎯 Direct View", callback_data="direct")],
        [InlineKeyboardButton("⏱️ Random Mode", callback_data="random")],
        [InlineKeyboardButton("🛑 Stop", callback_data="stop")],
        [InlineKeyboardButton("📊 Status", callback_data="status")]
    ]
    await update.message.reply_text(
        "📢 *Telegram Auto Views Bot*\nChoose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "direct":
        await query.edit_message_text("Send me the Telegram post URL (e.g., `https://t.me/username/123`)")
        context.user_data['waiting_for_url'] = True
        context.user_data['mode'] = 'direct'
    elif data == "random":
        await query.edit_message_text("Send me the Telegram post URL")
        context.user_data['waiting_for_url'] = True
        context.user_data['mode'] = 'random'
    elif data == "stop":
        global stop_flag
        stop_flag = True
        await query.edit_message_text("🛑 Stopping...")
    elif data == "status":
        await send_status(update, context)

async def send_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if current_status['active']:
        msg = (
            f"📊 *Status*\n"
            f"Mode: `{current_status['mode']}`\n"
            f"Target: `{current_status['channel']}/{current_status['post']}`\n"
            f"Sent: `{current_status['sent']} / {current_status['target']}`\n"
            f"Real views: `{current_status['real_views']}`\n"
            f"Active: ✅"
        )
    else:
        msg = "No active view task. Use /start to begin."
    await update.message.reply_text(msg, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global progress_msg_id, stop_flag, current_status, loop
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
            view_count = int(update.message.text.strip())
            if view_count <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Please send a valid positive integer (e.g., `500`).")
            return

        context.user_data['waiting_for_count'] = False
        channel = context.user_data['channel']
        post = context.user_data['post']
        mode = context.user_data['mode']
        target = view_count

        # Reset globals
        stop_flag = False
        progress_msg_id = None
        current_status = {
            'active': True,
            'mode': mode,
            'channel': channel,
            'post': post,
            'target': target,
            'sent': 0,
            'real_views': 0,
        }

        # Store the event loop for thread-safe updates
        loop = asyncio.get_running_loop()

        await update.message.reply_text(
            f"🚀 Starting {mode} mode for `{channel}/{post}`.\n"
            f"Target views: **{target}**.\n"
            f"Progress will be shown in real time.\nUse /stop to cancel."
        )

        chat_id = update.effective_chat.id
        # Start threads (daemon so they exit when bot stops)
        view_thread = threading.Thread(
            target=original_view_sender,
            args=(channel, post, target, mode, chat_id, context),
            daemon=True
        )
        views_thread = threading.Thread(
            target=real_views_updater,
            args=(channel, post, chat_id, context),
            daemon=True
        )
        view_thread.start()
        views_thread.start()

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global stop_flag
    stop_flag = True
    await update.message.reply_text("🛑 Stopping view tasks...")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_status(update, context)

# -------------------------------------------------------------------
def main():
    print(LOGO)
    print("🤖 Bot is running. Press Ctrl+C to stop.")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("No TELEGRAM_BOT_TOKEN set in environment variables.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
