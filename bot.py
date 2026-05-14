import os
import threading
import time
import asyncio
from re import search
from threading import active_count
from time import sleep as swait

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from utilitys import config_loader, LOGO, logger, THREADS
from auto_proxy import Proxy
from tg_views import Api

# Global state
stop_flag = False
target_views = 0
sent_views = 0
real_views = 0
lock = threading.Lock()
progress_msg_id = None
chat_id = None
app_context = None

def progress_bar(current, total, length=20):
    filled = int(length * current / total) if total else 0
    return '█' * filled + '░' * (length - filled)

async def update_progress():
    global progress_msg_id, chat_id, app_context
    if not chat_id or not app_context:
        return
    bar = progress_bar(sent_views, target_views)
    percent = (sent_views / target_views) * 100 if target_views else 0
    text = (
        f"🚀 *View Sender*\n"
        f"📊 `{bar}` {percent:.1f}%\n"
        f"✅ Sent: `{sent_views}` / `{target_views}`\n"
        f"👁️ Live views: `{real_views}`\n"
        f"⚡ Status: {'Running' if not stop_flag else 'Stopping'}"
    )
    if progress_msg_id is None:
        msg = await app_context.bot.send_message(chat_id, text, parse_mode='Markdown')
        progress_msg_id = msg.message_id
    else:
        try:
            await app_context.bot.edit_message_text(chat_id, progress_msg_id, text, parse_mode='Markdown')
        except Exception:
            pass

def view_updater(api):
    global real_views, stop_flag
    while not stop_flag:
        try:
            Api.views(api)
            real_views = Api.real_views
            asyncio.run_coroutine_threadsafe(update_progress(), asyncio.get_event_loop())
        except Exception:
            pass
        swait(5)

def cli():
    from utilitys import display
    _display = display()
    while not stop_flag:
        try:
            print("\n" * 2)
            _display()
            print(f"Sent: {sent_views}/{target_views}")
        except Exception:
            pass
        swait(2)

def send_view_with_count(api, proxy, proxy_type):
    global sent_views, stop_flag, target_views, lock
    with lock:
        if sent_views >= target_views or stop_flag:
            return
    api.send_view(proxy, proxy_type)
    with lock:
        sent_views += 1
        if sent_views % 10 == 0 or sent_views == target_views:
            asyncio.run_coroutine_threadsafe(update_progress(), asyncio.get_event_loop())

def start_sender(api, auto_proxies):
    global sent_views, stop_flag, target_views
    auto_proxies.init()
    proxy_list = list(auto_proxies.proxies)
    if not proxy_list:
        print("No proxies available.")
        return

    print(f"Starting with {len(proxy_list)} proxy entries (will cycle continuously)")
    while not stop_flag and sent_views < target_views:
        threads = []
        for proxy_type, proxy in proxy_list:
            while active_count() > THREADS:
                if stop_flag or sent_views >= target_views:
                    break
                swait(0.05)
            if stop_flag or sent_views >= target_views:
                break
            t = threading.Thread(target=send_view_with_count, args=(api, proxy, proxy_type), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        print(f"Completed one proxy cycle. Total sent: {sent_views}/{target_views}")

    print(f"Sender finished. Sent {sent_views} views.")

# ---------------- Bot handlers ----------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global chat_id, app_context, progress_msg_id
    chat_id = update.effective_chat.id
    app_context = context
    progress_msg_id = None
    keyboard = [[InlineKeyboardButton("🎯 Start Viewing", callback_data="start")],
                [InlineKeyboardButton("🛑 Stop", callback_data="stop")]]
    await update.message.reply_text("📢 *Telegram Auto Views Bot*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "start":
        await query.edit_message_text("Send me the Telegram post URL (e.g., `https://t.me/username/123`)")
        context.user_data['waiting_for_url'] = True
    elif query.data == "stop":
        global stop_flag
        stop_flag = True
        await query.edit_message_text("🛑 Stopping...")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global target_views, sent_views, stop_flag, progress_msg_id
    if context.user_data.get('waiting_for_url'):
        url = update.message.text
        match = search(r'(https?:\/\/t\.me\/)?([^/]+)/(\d+)', url)
        if not match:
            await update.message.reply_text("❌ Invalid URL.")
            return
        _, channel, post = match.groups()
        context.user_data['channel'] = channel
        context.user_data['post'] = post
        context.user_data['waiting_for_url'] = False
        context.user_data['waiting_for_count'] = True
        await update.message.reply_text("✅ URL accepted. Now send the **number of views** (e.g., `1000`):")
        return

    if context.user_data.get('waiting_for_count'):
        try:
            target = int(update.message.text.strip())
            if target <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Please send a positive integer.")
            return

        target_views = target
        sent_views = 0
        stop_flag = False
        progress_msg_id = None
        context.user_data['waiting_for_count'] = False

        channel = context.user_data['channel']
        post = context.user_data['post']

        http, socks4, socks5 = config_loader()
        auto_proxies = Proxy(http, socks4, socks5)
        api = Api(channel, post)

        await update.message.reply_text(f"🚀 Starting for {channel}/{post}. Target: {target_views} views.\nProgress bar will appear here.")
        threading.Thread(target=view_updater, args=(api,), daemon=True).start()
        threading.Thread(target=cli, daemon=True).start()
        threading.Thread(target=start_sender, args=(api, auto_proxies), daemon=True).start()

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global stop_flag
    stop_flag = True
    await update.message.reply_text("🛑 Stopping...")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")

def main():
    print(LOGO)
    print("🤖 Bot running. Press Ctrl+C to stop.")
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
