import os
import threading
import io
import requests
import pandas as pd
import numpy as np

# تنظیمات ضروری برای اجرای گرافیک در سرورهای ابری بدون مانیتور
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ساخت وب‌سرور کوچک برای Render جهت جلوگیری از خوابیدن سرویس
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Kronos Bot is active 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

# خواندن توکن ربات از تنظیمات امنیتی سرور
BOT_TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! ربات تحلیلگر Kronos فعال است.\n"
        "برای دریافت پیش‌بینی قیمت، دستور زیر را ارسال کنید:\n"
        "/predict BTCUSDT"
    )

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = context.args[0].upper() if context.args else "BTCUSDT"
    await update.message.reply_text(f"⏳ در حال دریافت ۵۱۲ کندل اخیر از بایننس و پردازش تحلیل برای {symbol}...")
    
    try:
        # دریافت داده‌های کندل‌استیک از بایننس (بدون نیاز به API Key)
        url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval=1h&limit=512"
        res = requests.get(url)
        if res.status_code != 200:
            await update.message.reply_text("خطا در دریافت اطلاعات از بایننس. لطفا نام ارز (مثل BTCUSDT) را بررسی کنید.")
            return
            
        data = res.json()
        df = pd.DataFrame(data, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
        ])
        df['close'] = df['close'].astype(float)
        
        # شبیه‌سازی پیش‌بینی قیمت (مونت‌کارلو / الگوبرداری از Kronos)
        last_price = df['close'].iloc[-1]
        returns = np.diff(np.log(df['close'].values))
        num_predictions = 24
        num_samples = 100
        
        simulations = np.zeros((num_samples, num_predictions))
        for i in range(num_samples):
            sim_returns = np.random.normal(np.mean(returns), np.std(returns), num_predictions)
            simulations[i] = last_price * np.exp(np.cumsum(sim_returns))
        
        std_end = np.std(simulations[:, -1])
        regime = "TRENDING (روند دار / اطمینان بالا)" if std_end / last_price < 0.03 else "CHOPPY (نوسانی / پرریسک)"
        
        # رسم نمودار تحلیل
        plt.figure(figsize=(10, 5))
        plt.plot(df['close'].values, color="blue", label="کندل‌های گذشته")
        
        last_idx = len(df['close']) - 1
        for sim in simulations:
            plt.plot(range(last_idx, last_idx + num_predictions), sim, color="gray", alpha=0.1)
        
        mean_path = np.mean(simulations, axis=0)
        plt.plot(range(last_idx, last_idx + num_predictions), mean_path, color="red", linewidth=2, label="میانگین پیش‌بینی")
        
        plt.title(f"Kronos Prediction - {symbol} | Regime: {regime}")
        plt.legend()
        plt.grid(True)
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close()
        
        caption = (
            f"📊 **تحلیل پیش‌بینی {symbol}**\n\n"
            f"🔹 قیمت فعلی: `{last_price}`\n"
            f"🔹 وضعیت بازار: **{regime}**\n"
            f"🔹 پیش‌بینی ۲۴ کندل آینده آماده شد."
        )
        
        await update.message.reply_photo(photo=buf, caption=caption, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"خطا در پردازش: {str(e)}")

def main():
    if not BOT_TOKEN:
        print("خطا: توکن ربات (BOT_TOKEN) تنظیم نشده است!")
        return
        
    # اجرای وب‌سرور در پس‌زمینه برای بیدار نگه داشتن سرویس
    t = threading.Thread(target=run_web, daemon=True)
    t.start()
    
    # راه‌اندازی ربات تلگرام
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("predict", predict_command))
    
    print("ربات با موفقیت روی سرور فعال شد و آماده دریافت پیام است...")
    application.run_polling()

if __name__ == '__main__':
    main()
