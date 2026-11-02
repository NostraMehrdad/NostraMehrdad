import os
import threading
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import requests
import pandas as pd
import numpy as np
import io
import matplotlib.pyplot as plt

# ساخت وب‌سرور کوچک برای Render
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Kronos Bot is active 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! ربات Kronos روی سرور ابری فعال شد. دستور /predict BTCUSDT را بفرستید.")

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = context.args[0].upper() if context.args else "BTCUSDT"
    await update.message.reply_text(f"⏳ در حال دریافت تحلیل {symbol}...")

    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=512"
        res = requests.get(url).json()
        df = pd.DataFrame(res, columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'])
        df['close'] = df['close'].astype(float)

        last_price = df['close'].iloc[-1]
        returns = np.diff(np.log(df['close'].values))
        simulations = np.zeros((100, 24))
        for i in range(100):
            sim_returns = np.random.normal(np.mean(returns), np.std(returns), 24)
            simulations[i] = last_price * np.exp(np.cumsum(sim_returns))

        std_end = np.std(simulations[:, -1])
        regime = "TRENDING (رونددار)" if std_end / last_price < 0.03 else "CHOPPY (نوسانی)"

        plt.figure(figsize=(10, 5))
        plt.plot(df['close'].values, color="blue", label="گذشته")
        for sim in simulations:
            plt.plot(range(len(df), len(df) + 24), sim, color="gray", alpha=0.1)
        plt.title(f"Kronos Prediction - {symbol} | Regime: {regime}")

        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()

        await update.message.reply_photo(photo=buf, caption=f"🔹 قیمت فعلی: {last_price}\n🔹 وضعیت بازار: {regime}")
    except Exception as e:
        await update.message.reply_text(f"خطا در پردازش: {str(e)}")

def main():
    t = threading.Thread(target=run_web)
    t.start()

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("predict", predict_command))
    application.run_polling()

if __name__ == '__main__':
    main()
