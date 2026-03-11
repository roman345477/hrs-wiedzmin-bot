import os
import logging
from threading import Thread
from flask import Flask, send_file
from telegram import Update, WebAppInfo, MenuButtonWebApp
from telegram.ext import Application, CommandHandler, ContextTypes

# ─── Config ───
BOT_TOKEN = os.environ.get("BOT_TOKEN")
APP_URL = os.environ.get("APP_URL", "")  # e.g. https://your-app.up.railway.app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Flask: serve the HTML ───
web = Flask(__name__)

@web.route("/")
def index():
    return send_file("index.html")

@web.route("/health")
def health():
    return "ok", 200

def run_web():
    port = int(os.environ.get("PORT", 8080))
    web.run(host="0.0.0.0", port=port)

# ─── Telegram Bot ───
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = APP_URL if APP_URL else f"https://{os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'localhost')}"
    await update.message.reply_text(
        "👋 Привіт! Натисни кнопку нижче, щоб відкрити ОБЛІК.",
        reply_markup={
            "inline_keyboard": [[{
                "text": "📊 Відкрити ОБЛІК",
                "web_app": {"url": url}
            }]]
        }
    )

async def post_init(application):
    """Set the bot menu button to open the web app."""
    url = APP_URL if APP_URL else f"https://{os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'localhost')}"
    try:
        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="ОБЛІК",
                web_app=WebAppInfo(url=url)
            )
        )
        logger.info(f"Menu button set to {url}")
    except Exception as e:
        logger.warning(f"Could not set menu button: {e}")

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return

    # Start Flask in background thread
    Thread(target=run_web, daemon=True).start()
    logger.info("Web server started")

    # Start Telegram bot
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    
    logger.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
