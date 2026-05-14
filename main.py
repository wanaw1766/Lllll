import os
import threading
import time
from re import search
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Your original modules (unchanged)
from utilitys import config_loader, LOGO
from auto_proxy import Proxy
from tg_views import Api   # your renamed telegram.py

# -------------------------------------------------------------------
# Original global variables from your CLI
THREADS = 400
stop_flag = False
current_channel = None
current_post = None
target_views = 0
views_sent = 0
real_views = 0

# -------------------------------------------------------------------
# Original CLI view sender (exactly as it was, with a target stop condition)
def original_view_sender():
    global views_sent, stop_flag, current_channel, current_post, target_views
    http, socks4, socks5 = config_loader()
    auto_proxies = Proxy(http_sources=http, socks4_sources=socks4, socks5_sources=socks5)
    auto_proxies.init()
    api = Api(channel=current_channel, post=current_post)

    # Get proxy list
    proxy_list = list(auto_proxies.proxies)
    if not proxy_list:
        print("No proxies available.")
        return

    views_sent = 0
    proxy_index = 0
    total_proxies = len(proxy_list)

    # Original direct mode (no random, just fast cycling)
    while views_sent < target_views and not stop_flag:
        proxy_type, proxy = proxy_list[proxy_index % total_proxies]
        try:
            api.send_view(proxy, proxy_type)
            views_sent += 1
            proxy_index += 1
            # Small delay to avoid being too aggressive (adjust as needed)
            time.sleep(0.05)
        except Exception:
            proxy_index += 1
            continue

    print(f"View sender finished: sent {views_sent} / {target_views}")

# -------------------------------------------------------------------
# Real views updater (original logic)
def real_views_updater(chat_id, context):
    global real_views, stop_flag, current_channel, current_post
    api = Api(channel=current_channel, post=current_post)
    while not stop_flag:
        try:
            Api.views(api)
            real_views = Api.real_views
            # Send progress update to Telegram every 5 seconds
            if chat_id and context:
                context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📈 *Progress*\nSent: {views_sent}/{target_views}\nReal views: {real_views}",
                    parse_mode='Markdown'
                )
        except Exception:
            pass
        time.sleep(5)

# -------------------------------------------------------------------
# Telegram bot handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    global current_channel, current_post, target_views, views_sent, stop_flag
    if context.user_data.get('waiting_for_url'):
        url = update.message.text
        match = search(r'(https?:\/\/t\.me\/)?([^/]+)/(\d+)', url)
        if not match:
            await update.message.reply_text("❌ Invalid URL. Send again or /cancel.")
            return
        _, channel, post = match.groups()
        current_channel = channel
        current_post = post
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
        context.user_data['waiting_for_count'] = False
        stop_flag = False
        views_sent = 0

        await update.message.reply_text(
            f"🚀 Starting view sender for `{current_channel}/{current_post}`.\n"
            f"Target: **{target_views}** views.\n"
            f"I will send progress updates every few seconds.\nUse /stop to cancel."
        )

        chat_id = update.effective_chat.id

        # Start original view sender in a background thread
        sender_thread = threading.Thread(target=original_view_sender, daemon=True)
        sender_thread.start()

        # Start real views updater in another background thread
        updater_thread = threading.Thread(target=real_views_updater, args=(chat_id, context), daemon=True)
        updater_thread.start()

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
