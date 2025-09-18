import logging
import sqlite3
import requests
import os
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
from openai import OpenAI
import time

# ================================
# CONFIGURATION
# ================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ALPHA_VANTAGE_API_KEY = os.environ.get("KBZDQXOH1D2LRK0J")  # Stock API

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
FINNHUB_TOKEN = os.environ.get("FINNHUB_TOKEN")
DB_PATH = "/Users/rambodazimi/Desktop/subscriptions.db"
POPULAR_STOCKS = ["AAPL", "TSLA", "MSFT", "AMZN", "GOOG", "META"]

# ================================
# LOGGING
# ================================
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== DB SETUP ==========
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
       chat_id TEXT,
       ticker TEXT,
       interval INTEGER
)""")
conn.commit()

# ========== TEMP STORAGE ==========
user_selected = {}  # chat_id -> list of tickers chosen temporarily

client = OpenAI()  # picks up OPENAI_API_KEY from environment


# ========== HELPER FUNCTIONS ==========
def get_stock_price(symbol):
    """Fetch last and previous close from Alpha Vantage"""
    url = f"https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY&symbol={symbol}&interval=60min&apikey={ALPHA_VANTAGE_API_KEY}"
    r = requests.get(url)
    data = r.json()
    series = data.get("Time Series (60min)")
    if not series or len(series) < 2:
        return None, None
    keys = sorted(series.keys(), reverse=True)
    last_price = float(series[keys[0]]["4. close"])
    prev_price = float(series[keys[1]]["4. close"])
    return last_price, prev_price

def fancy_price_message(symbol, price, prev_price):
    """Return formatted price message with emojis"""
    change = price - prev_price
    pct_change = (change / prev_price) * 100
    arrow = "📈" if change > 0 else "📉"
    color_emoji = "🟩" if change > 0 else "🔴"
    msg = (
        f"<b>{arrow} {symbol}</b>\n"
        f"<b>Price:</b> <code>${price:.2f}</code>\n"
        f"<b>Change:</b> {color_emoji} {change:+.2f} ({pct_change:+.2f}%)"
    )
    return msg

# ========== COMMAND HANDLERS ==========
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 <b>MTL Trader Bot</b>\n\n"
        "Stay up to date with your favorite stocks and receive educational insights.\n\n"
        "📋 <b>Commands:</b>\n"
        "• <code>/start</code> – Start the bot and choose popular stocks to subscribe.\n"
        "• <code>/price SYMBOL</code> – Get the current price and change for a stock.\n"
        "• <code>/my_subscriptions</code> – View your subscriptions, edit intervals, or delete them.\n"
        "• <code>/advisor SYMBOL [budget]</code> – Get an educational AI analysis of a stock using one-year price history and (optional) your budget.\n"
        "• <code>/help</code> – Show this help message.\n\n"
        "ℹ️ Subscriptions can be set to 1 min, 30 min, 1 h, 6 h, 12 h, or 24 h updates.\n\n"
        "⚠️ All information provided is for educational purposes only and not financial advice."
    )
    if update.message:
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)
    elif update.callback_query:
        # In case /help triggered from a button
        await update.callback_query.message.reply_text(help_text, parse_mode=ParseMode.HTML)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(stock, callback_data=f"sub_stock_{stock}") for stock in POPULAR_STOCKS]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🤖 Welcome to <b>MTL Trader Bot</b>!\n"
        "Choose a stock to subscribe (you can add multiple):",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /price SYMBOL")
        return
    symbol = context.args[0].upper()
    price_val, prev_price = get_stock_price(symbol)
    if price_val is None:
        await update.message.reply_text("Could not fetch price.")
        return
    msg = fancy_price_message(symbol, price_val, prev_price)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def my_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    c.execute("SELECT rowid, ticker, interval FROM subscriptions WHERE chat_id=?", (chat_id,))
    rows = c.fetchall()
    if not rows:
        await update.message.reply_text("You have no subscriptions.")
        return

    msg = "📜 <b>Your subscriptions:</b>\nSelect an action below:\n\n"
    keyboard = []
    for rowid, t, inter in rows:
        msg += f"• {t} (every {inter} min)\n"
        keyboard.append([
            InlineKeyboardButton(f"Edit {t}", callback_data=f"edit_{rowid}"),
            InlineKeyboardButton(f"❌ Delete", callback_data=f"delete_{rowid}")
        ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

# ========== CALLBACK HANDLER ==========
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat.id)

    # Selecting a stock
    if query.data.startswith("sub_stock_"):
        ticker = query.data.split("_")[-1]
        user_selected.setdefault(chat_id, []).append(ticker)
        await query.edit_message_text(
            f"✅ Added {ticker}. Currently selected: {', '.join(user_selected[chat_id])}\n"
            "Now choose update interval:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("1 min", callback_data="interval_1"),
                 InlineKeyboardButton("30 min", callback_data="interval_30"),
                 InlineKeyboardButton("1 h", callback_data="interval_60")],
                [InlineKeyboardButton("6 h", callback_data="interval_360"),
                 InlineKeyboardButton("12 h", callback_data="interval_720"),
                 InlineKeyboardButton("24 h", callback_data="interval_1440")]
            ])
        )

    # Interval chosen for new subscription
    elif query.data.startswith("interval_"):
        interval = int(query.data.split("_")[1])
        tickers = user_selected.get(chat_id, [])
        for t in tickers:
            c.execute("INSERT INTO subscriptions (chat_id, ticker, interval) VALUES (?,?,?)",
                      (chat_id, t, interval))
        conn.commit()
        user_selected[chat_id] = []
        await query.edit_message_text(f"✅ Subscribed to {', '.join(tickers)} every {interval} min.")

    # Deleting subscription
    elif query.data.startswith("delete_"):
        rowid = int(query.data.split("_")[1])
        c.execute("DELETE FROM subscriptions WHERE rowid=? AND chat_id=?", (rowid, chat_id))
        conn.commit()
        await query.edit_message_text("✅ Subscription deleted. Use /my_subscriptions to refresh.")

    # Editing subscription (show interval options)
    elif query.data.startswith("edit_"):
        rowid = int(query.data.split("_")[1])
        await query.edit_message_text(
            "Choose a new interval:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("1 min", callback_data=f"update_{rowid}_1"),
                 InlineKeyboardButton("30 min", callback_data=f"update_{rowid}_30"),
                 InlineKeyboardButton("1 h", callback_data=f"update_{rowid}_60")],
                [InlineKeyboardButton("6 h", callback_data=f"update_{rowid}_360"),
                 InlineKeyboardButton("12 h", callback_data=f"update_{rowid}_720"),
                InlineKeyboardButton("24 h", callback_data=f"update_{rowid}_1440")]
            ])
        )

    # Updating interval of existing subscription
    elif query.data.startswith("update_"):
        _, rowid, interval = query.data.split("_")
        rowid = int(rowid)
        interval = int(interval)
        c.execute("UPDATE subscriptions SET interval=? WHERE rowid=? AND chat_id=?", (interval, rowid, chat_id))
        conn.commit()
        await query.edit_message_text(f"✅ Updated interval to {interval} min. Use /my_subscriptions to refresh.")

# ========== SCHEDULER ==========
async def send_updates(app):
    now = int(time.time() // 60)  # current time in minutes
    c.execute("SELECT chat_id, ticker, interval FROM subscriptions")
    rows = c.fetchall()
    for chat_id, ticker, interval in rows:
        if now % interval == 0:  # due for update
            price_val, prev_price = get_stock_price(ticker)
            if price_val is None:
                continue
            msg = fancy_price_message(ticker, price_val, prev_price)
            try:
                await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Error sending message to {chat_id}: {e}")


async def advisor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    if len(context.args) < 1:
        await update.message.reply_text("Usage: /advisor SYMBOL (optionally add your budget after the symbol)")
        return

    symbol = context.args[0].upper()
    budget = None
    if len(context.args) > 1:
        try:
            budget = float(context.args[1])
        except ValueError:
            budget = None

    await update.message.reply_text(f"🔄 Gathering one year of data for {symbol}…")


    # Optionally include user subscriptions
    c.execute("SELECT ticker, interval FROM subscriptions WHERE chat_id=?", (chat_id,))
    rows = c.fetchall()
    tickers = [t for t, _ in rows]

    # Build prompt
    prompt = (
        "You are an educational AI stock analyst.\n\n"
        f"The user asked for an analysis of {symbol}.\n"
        f"Here is the last year of daily closing prices for {symbol}:\n\n"
    )
    if budget:
        prompt += f"The user has an approximate budget of ${budget:.2f}.\n"
    if tickers:
        prompt += f"The user is also subscribed to: {', '.join(tickers)}.\n"

    prompt += (
        "\nProvide an educational analysis of trends, volatility, and risk factors. "
        "Do NOT give direct buy/sell advice; only educational insights."
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        advice_text = completion.choices[0].message.content.strip()
        await update.message.reply_text(
            f"📊 <b>Your Educational Stock Analysis for {symbol}:</b>\n\n{advice_text}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        await update.message.reply_text("Sorry, I couldn’t get AI analysis right now.")

async def job_send_updates(context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    await send_updates(app)


# ========== MAIN ==========
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("my_subscriptions", my_subscriptions))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(CommandHandler("advisor", advisor))

    # Use PTB job_queue instead of APScheduler
    job_queue = app.job_queue
    job_queue.run_repeating(job_send_updates, interval=60, first=10)

    app.run_polling()


if __name__ == "__main__":
    main()