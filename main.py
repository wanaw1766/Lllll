import os
import threading
import asyncio
import random
from re import search
from time import sleep as swait

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Your local modules (unchanged except import from tg_views)
from tg_views import Api
from utilitys import config_loader, LOGO
from auto_proxy import Proxy

# -------------------------------------------------------------------
# Global variables (same as original CLI)
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
view_thread = None
real_views_thread = None

# -------------------------------------------------------------------
def progress_bar(current, total, length=20):
    filled = int(length * current / total)
    return '█' * filled + '░' * (length - filled)

async def update_progress_message(context, chat_id):
    global current_status, stop_flag, progress_msg_id
    if not current_status['active']:
        return
    bar = progress_bar(current_status['sent'], current_status['target'])
    percentage = (current_status['sent'] / current_status['target']) * 100
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
            pass

# -------------------------------------------------------------------
# THE ORIGINAL VIEW SENDER (threaded, exactly as in CLI)
def original_view_sender(channel, post, target, mode, chat_id, context):
    global stop_flag, current_status
    # Load proxies exactly as before
    http, socks4, socks5 = config_loader()
    auto_proxies = Proxy(http_sources=http, socks4_sources=socks4, socks5_sources=socks5)
    auto_proxies.init()
    api = Api(channel=channel, post=post)

    proxy_list = list(auto_proxies.proxies)
    if not proxy_list:
        asyncio.run_coroutine_threadsafe(
            context.bot.send_message(chat_id, "❌ No proxies available. Stopping."),
            asyncio.get_event_loop()
        )
        current_status['active'] = False
        return

    views_sent = 0
    if mode == 'direct':
        # Direct mode: cycle through proxies as fast as possible (original CLI style)
        while views_sent < target and not stop_flag:
            for proxy_type, proxy in proxy_list:
                if views_sent >= target or stop_flag:
                    break
                try:
                    api.send_view(proxy, proxy_type)
                    views_sent += 1
                    current_status['sent'] = views_sent
                    # Update progress every 5 views
                    if views_sent % 5 == 0 or views_sent == target:
                        asyncio.run_coroutine_threadsafe(
                            update_progress_message(context, chat_id),
                            asyncio.get_event_loop()
                        )
                except Exception:
                    continue
        # Final update
        asyncio.run_coroutine_threadsafe(
            update_progress_message(context, chat_id),
            asyncio.get_event_loop()
        )
        asyncio.run_coroutine_threadsafe(
            context.bot.send_message(chat_id, f"✅ Direct mode finished. Sent {views_sent} / {target} views."),
            asyncio.get_event_loop()
        )

    elif mode == 'random':
        # Random mode: send one view, then wait 1‑5 minutes
        while views_sent < target and not stop_flag:
            proxy_type, proxy = proxy_list[0]  # use first proxy
            try:
                api.send_view(proxy, proxy_type)
                views_sent += 1
                current_status['sent'] = views_sent
                asyncio.run_coroutine_threadsafe(
                    update_progress_message(context, chat_id),
                    asyncio.get_event_loop()
                )
                if views_sent < target:
                    wait_seconds = random.randint(60, 300)
                    for _ in range(wait_seconds):
                        if stop_flag:
                            break
                        swait(1)  # blocking sleep, but this runs in a thread
            except Exception:
                swait(1)
                continue
        asyncio.run_coroutine_threadsafe(
            context.bot.send_message(chat_id, f"✅ Random mode finished. Sent {views_sent} / {target} views."),
            asyncio.get_event_loop()
        )

    current_status['active'] = False

def real_views_updater(channel, post, chat_id, context):
    api = Api(channel=channel, post=post)
    while current_status['active'] and not stop_flag:
        try:
            Api.views(api)
            current_status['real_views'] = Api.real_views
            asyncio.run_coroutine_threadsafe(
                update_progress_message(context, chat_id),
                asyncio.get_event_loop()
            )
        except Exception:
            pass
        swait(5)  # update every 5 seconds (blocking, but in thread)

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
        global stop_flag, view_thread, real_views_thread
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
    global progress_msg_id, view_thread, real_views_thread, stop_flag, current_status
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

        await update.message.reply_text(
            f"🚀 Starting {mode} mode for `{channel}/{post}`.\n"
            f"Target views: **{target}**.\n"
            f"Progress will be shown in real time.\nUse /stop to cancel."
        )

        # Start original threaded view sender and real‑views updater
        chat_id = update.effective_chat.id
        view_thread = threading.Thread(
            target=original_view_sender,
            args=(channel, post, target, mode, chat_id, context),
            daemon=True
        )
        real_views_thread = threading.Thread(
            target=real_views_updater,
            args=(channel, post, chat_id, context),
            daemon=True
        )
        view_thread.start()
        real_views_thread.start()

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
