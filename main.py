import os
import asyncio
import random
from re import search

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from tg_views import Api
from utilitys import config_loader, LOGO
from auto_proxy import Proxy

stop_flag = False
current_status = {
    'mode': None, 'channel': None, 'post': None,
    'target': 0, 'sent': 0, 'real_views': 0, 'active': False
}
progress_msg_id = None

def progress_bar(current, total, length=20):
    filled = int(length * current / total)
    return '█' * filled + '░' * (length - filled)

async def update_progress_message(context, chat_id, sent, target, real_views, mode):
    global progress_msg_id
    bar = progress_bar(sent, target)
    percentage = (sent / target) * 100 if target > 0 else 0
    text = (
        f"🚀 *{mode.upper()} MODE*\n"
        f"📊 Progress:\n`{bar}` {percentage:.1f}%\n"
        f"✅ Sent: `{sent}` / `{target}` views\n"
        f"👁️ Live Telegram views: `{real_views}`\n"
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
            pass  # Ignore "message not modified" errors

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
    global progress_msg_id
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

        context.user_data['view_count'] = view_count
        context.user_data['waiting_for_count'] = False

        channel = context.user_data['channel']
        post = context.user_data['post']
        mode = context.user_data['mode']
        target = view_count

        progress_msg_id = None

        await update.message.reply_text(
            f"🚀 Starting {mode} mode for `{channel}/{post}`.\n"
            f"Target views: **{target}**.\n"
            f"Progress will be shown in real time.\nUse /stop to cancel."
        )

        asyncio.create_task(run_viewer(update, context, channel, post, target, mode))
        asyncio.create_task(update_real_views(update, context, channel, post))

async def update_real_views(update: Update, context: ContextTypes.DEFAULT_TYPE, channel, post):
    api = Api(channel=channel, post=post)
    while current_status['active'] and not stop_flag:
        try:
            Api.views(api)
            current_status['real_views'] = Api.real_views
            await update_progress_message(
                context, update.effective_chat.id,
                current_status['sent'], current_status['target'],
                current_status['real_views'], current_status['mode']
            )
        except Exception:
            pass
        await asyncio.sleep(5)

async def run_viewer(update: Update, context: ContextTypes.DEFAULT_TYPE, channel, post, target, mode):
    global stop_flag, current_status
    stop_flag = False
    current_status = {
        'mode': mode, 'channel': channel, 'post': post,
        'target': target, 'sent': 0, 'real_views': 0, 'active': True
    }

    http, socks4, socks5 = config_loader()
    auto_proxies = Proxy(http_sources=http, socks4_sources=socks4, socks5_sources=socks5)
    auto_proxies.init()
    api = Api(channel=channel, post=post)

    proxy_list = list(auto_proxies.proxies)
    if not proxy_list:
        await update.message.reply_text("❌ No proxies available. Stopping.")
        current_status['active'] = False
        return

    views_sent = 0
    chat_id = update.effective_chat.id
    proxy_index = 0
    total_proxies = len(proxy_list)

    if mode == 'direct':
        while views_sent < target and not stop_flag:
            proxy_type, proxy = proxy_list[proxy_index % total_proxies]
            try:
                api.send_view(proxy, proxy_type)
                views_sent += 1
                current_status['sent'] = views_sent
                if views_sent % 5 == 0 or views_sent == target:
                    await update_progress_message(
                        context, chat_id, views_sent, target,
                        current_status['real_views'], mode
                    )
                proxy_index += 1
                await asyncio.sleep(0.05)
            except Exception:
                proxy_index += 1
                continue
        await update_progress_message(context, chat_id, views_sent, target, current_status['real_views'], mode)
        await context.bot.send_message(chat_id, f"✅ Direct mode finished. Sent {views_sent} / {target} views.")

    elif mode == 'random':
        while views_sent < target and not stop_flag:
            proxy_type, proxy = proxy_list[0]
            try:
                api.send_view(proxy, proxy_type)
                views_sent += 1
                current_status['sent'] = views_sent
                await update_progress_message(context, chat_id, views_sent, target, current_status['real_views'], mode)
                if views_sent < target:
                    wait_seconds = random.randint(60, 300)
                    await asyncio.sleep(wait_seconds)
            except Exception:
                await asyncio.sleep(1)
                continue
        await context.bot.send_message(chat_id, f"✅ Random mode finished. Sent {views_sent} / {target} views.")

    current_status['active'] = False

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global stop_flag
    stop_flag = True
    await update.message.reply_text("🛑 Stopping view tasks...")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_status(update, context)

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
