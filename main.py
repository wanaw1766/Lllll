import os
import sys
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
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=progress_msg_id,
            text=text,
            parse_mode='Markdown'
        )
    print(f"[DEBUG] Progress update: {sent}/{target}, real views {real_views}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("[DEBUG] /start command")
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
    print(f"[DEBUG] Button: {data}")

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
        print(f"[DEBUG] Received URL: {url}")
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

        print(f"[DEBUG] Target view count: {view_count}")
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
        except Exception as e:
            print(f"[ERROR] real views update: {e}")
        await asyncio.sleep(3)

async def run_viewer(update: Update, context: ContextTypes.DEFAULT_TYPE, channel, post, target, mode):
    global stop_flag, current_status
    stop_flag = False
    current_status = {
        'mode': mode, 'channel': channel, 'post': post,
        'target': target, 'sent': 0, 'real_views': 0, 'active': True
    }
    print(f"[DEBUG] run_viewer started: {mode} {channel}/{post} target={target}")

    # Load configuration and proxies
    try:
        http, socks4, socks5 = config_loader()
        print(f"[DEBUG] config_loader OK: HTTP sources {len(http)}")
    except Exception as e:
        print(f"[ERROR] config_loader failed: {e}")
        await update.message.reply_text("❌ Failed to load config.ini")
        current_status['active'] = False
        return

    try:
        auto_proxies = Proxy(http_sources=http, socks4_sources=socks4, socks5_sources=socks5)
        auto_proxies.init()
        print(f"[DEBUG] Proxy init OK, total proxies: {len(auto_proxies.proxies)}")
    except Exception as e:
        print(f"[ERROR] Proxy init failed: {e}")
        await update.message.reply_text("❌ Failed to fetch proxies")
        current_status['active'] = False
        return

    api = Api(channel=channel, post=post)
    proxy_list = list(auto_proxies.proxies)
    if not proxy_list:
        print("[ERROR] No proxies available")
        await update.message.reply_text("❌ No proxies available. Stopping.")
        current_status['active'] = False
        return

    views_sent = 0
    chat_id = update.effective_chat.id

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
                        await update_progress_message(
                            context, chat_id, views_sent, target,
                            current_status['real_views'], mode
                        )
                    await asyncio.sleep(0.1)
                except Exception as e:
                    print(f"[ERROR] send_view failed: {e}")
            # After one full cycle, we might want to continue from start
            # This loop already continues to next cycle
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
                    print(f"[DEBUG] Random: sent {views_sent}, waiting {wait_seconds}s")
                    for _ in range(wait_seconds // 5):
                        if stop_flag:
                            break
                        await asyncio.sleep(5)
                        # Optionally update progress during wait (no new views)
                        await update_progress_message(context, chat_id, views_sent, target, current_status['real_views'], mode)
            except Exception as e:
                print(f"[ERROR] random send_view failed: {e}")
        await context.bot.send_message(chat_id, f"✅ Random mode finished. Sent {views_sent} / {target} views.")

    current_status['active'] = False

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global stop_flag
    stop_flag = True
    await update.message.reply_text("🛑 Stopping view tasks...")
    print("[DEBUG] Stop command")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    print("[DEBUG] Cancel command")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_status(update, context)

def main():
    print(LOGO)
    print("[DEBUG] Starting bot...")
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
    print("[DEBUG] Bot is running. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
