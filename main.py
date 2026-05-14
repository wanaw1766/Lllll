import os
import asyncio
import random
from re import search

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from utilitys import config_loader, LOGO
from auto_proxy import Proxy
from telegram import Api

stop_flag = False

# -------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎯 Direct View", callback_data="direct")],
        [InlineKeyboardButton("⏱️ Random Mode", callback_data="random")],
        [InlineKeyboardButton("🛑 Stop", callback_data="stop")]
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
        await query.edit_message_text("🛑 Stopped.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Step 1: waiting for URL
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

    # Step 2: waiting for view count
    if context.user_data.get('waiting_for_count'):
        try:
            view_count = int(update.message.text.strip())
            if view_count <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Please send a valid positive integer (e.g., `500`).")
            return

        context.user_data['view_count'] = view_count
        context.user_data['waiting_for_count'] = False

        channel = context.user_data['channel']
        post = context.user_data['post']
        mode = context.user_data['mode']

        await update.message.reply_text(
            f"🚀 Starting {mode} mode for `{channel}/{post}`.\n"
            f"Target views: **{view_count}**.\n"
            f"Use /stop to cancel."
        )
        asyncio.create_task(run_viewer(update, context, channel, post, view_count, mode))

async def run_viewer(update, context, channel, post, view_count, mode):
    global stop_flag
    stop_flag = False

    http, socks4, socks5 = config_loader()
    auto_proxies = Proxy(http_sources=http, socks4_sources=socks4, socks5_sources=socks5)
    auto_proxies.init()
    api = Api(channel=channel, post=post)

    proxy_list = list(auto_proxies.proxies)
    if not proxy_list:
        await update.message.reply_text("❌ No proxies available. Stopping.")
        return

    views_sent = 0

    if mode == 'direct':
        while views_sent < view_count and not stop_flag:
            for proxy_type, proxy in proxy_list:
                if views_sent >= view_count or stop_flag:
                    break
                api.send_view(proxy, proxy_type)
                views_sent += 1
                await asyncio.sleep(0.2)
        await update.message.reply_text(f"✅ Direct mode finished. Sent {views_sent} / {view_count} views.")

    elif mode == 'random':
        while views_sent < view_count and not stop_flag:
            proxy_type, proxy = proxy_list[0]
            api.send_view(proxy, proxy_type)
            views_sent += 1
            if views_sent < view_count:
                wait_seconds = random.randint(60, 300)
                await asyncio.sleep(wait_seconds)
        await update.message.reply_text(f"✅ Random mode finished. Sent {views_sent} / {view_count} views.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global stop_flag
    stop_flag = True
    await update.message.reply_text("🛑 Stopping...")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")

# -------------------------------------------------------------------
def main():
    print(LOGO)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("No TELEGRAM_BOT_TOKEN set.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
