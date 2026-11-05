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
    return "Kronos Bot is active 24/7 with Advanced Analytics!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **سلام! ربات پیشرفته تحلیلگر Kronos فعال است.**\n\n"
        "دستورات موجود:\n"
        "📊 `/predict BTCUSDT` -> دریافت تحلیل قیمت، شبیه‌سازی و سیگنال معامله\n"
        "🛠 `/tools` -> دسترسی به لیست سایت‌های مهم آن‌چین، امنیت و ابزارهای ترید"
    )

async def tools_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tools_text = (
        "🛠 **لیست مرجع سایت‌های تخصصی بازار کریپتو**\n\n"
        "🔍 **آن‌چین و ردیابی نهنگ‌ها:**\n"
        "• [Arkham](https://arkm.com) | [Lookonchain](https://lookonchain.com)\n"
        "• [Whale Alert](https://whale-alert.ie) | [Debank](https://debank.com)\n"
        "• [GMGN](https://gmgn.ai) | [Kolscan](https://kolscan.ie)\n\n"
        "🛡 **امنیت و اسکن اسکم / هانی‌پات:**\n"
        "• [RugCheck](https://rugcheck.xyz) | [Honeypot Is](https://honeypot.is)\n"
        "• [TokenSniffer](https://tokensniffer.com) | [GoPlus](https://gopluslabs.io)\n"
        "• [SolSniffer](https://solsniffer.com)\n\n"
        "📈 **چارت‌ها و دکس‌ها:**\n"
        "• [DexScreener](https://dexscreener.com) | [Birdeye](https://birdeye.so)\n"
        "• [GeckoTerminal](https://geckoterminal.com) | [SolScan](https://solscan.io)\n\n"
        "⚡️ **مشتقات، فاندینگ و لیکوییدیتی:**\n"
        "• [Coinglass](https://www.coinglass.com) | [Coinalyze](https://coinalyze.net)\n"
        "• [Coinank](https://coinank.com) | [Hyperliquid](https://app.hyperliquid.xyz)\n\n"
        "🔓 **توکناومیکس و آنلاک‌ها:**\n"
        "• [DefiLlama Unlocks](https://defillama.com/unlocks)\n"
        "• [Tokenomist](https://tokenomist.ai) | [CryptoRank](https://cryptorank.ie)\n"
        "• [Token Terminal](https://tokenterminal.com) | [Ultrasound Money](https://ultrasound.money)"
    )
    await update.message.reply_text(tools_text, parse_mode="Markdown", disable_web_page_preview=True)

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = context.args[0].upper() if context.args else "BTCUSDT"
    await update.message.reply_text(f"⏳ در حال استخراج ۵۱۲ کندل از بایننس، محاسبه اندیکاتورها و پردازش سیگنال برای {symbol}...")
    
    try:
        # استفاده از آدرس پایدار بایننس برای دورزدن تحریم دیتاسنتر
        url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval=1h&limit=512"
        res = requests.get(url)
        if res.status_code != 200:
            await update.message.reply_text("❌ خطا در دریافت اطلاعات. لطفا نماد را درست وارد کنید (مثلا: /predict ETHUSDT).")
            return
            
        data = res.json()
        df = pd.DataFrame(data, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
        ])
        df['close'] = df['close'].astype(float)
        
        # محاسبه اندیکاتور RSI (دوره ۱۴)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]
        
        # محاسبه میانگین متحرک ساده (SMA 50)
        sma_50 = df['close'].rolling(window=50).mean().iloc[-1]
        last_price = df['close'].iloc[-1]
        
        # شبیه‌سازی مونت‌کارلو برای ۲۴ کندل آینده
        returns = np.diff(np.log(df['close'].values))
        num_predictions = 24
        num_samples = 100
        
        simulations = np.zeros((num_samples, num_predictions))
        for i in range(num_samples):
            sim_returns = np.random.normal(np.mean(returns), np.std(returns), num_predictions)
            simulations[i] = last_price * np.exp(np.cumsum(sim_returns))
        
        mean_path = np.mean(simulations, axis=0)
        std_end = np.std(simulations[:, -1])
        regime = "رونددار (Trending / باثبات)" if std_end / last_price < 0.03 else "نوسانی پرریسک (Choppy)"
        
        # منطق تولید سیگنال معاملاتی (Long / Short)
        if mean_path[-1] > last_price and current_rsi < 65 and last_price > sma_50:
            signal_dir = "🟢 LONG (خرید)"
            entry_price = last_price
            tp = last_price * 1.025
            sl = last_price * 0.985
        elif mean_path[-1] < last_price and current_rsi > 35:
            signal_dir = "🔴 SHORT (فروش)"
            entry_price = last_price
            tp = last_price * 0.975
            sl = last_price * 1.015
        else:
            signal_dir = "⚪️ NEUTRAL (صبر و نظاره‌گر)"
            entry_price = last_price
            tp = last_price * 1.01
            sl = last_price * 0.99

        # رسم نمودار تحلیلی
        plt.figure(figsize=(10, 5))
        plt.plot(df['close'].values, color="blue", label="کندل‌های گذشته")
        last_idx = len(df['close']) - 1
        for sim in simulations:
            plt.plot(range(last_idx, last_idx + num_predictions), sim, color="gray", alpha=0.1)
        plt.plot(range(last_idx, last_idx + num_predictions), mean_path, color="red", linewidth=2, label="میانگین پیش‌بینی")
        plt.title(f"Kronos Advanced AI - {symbol} | Regime: {regime}")
        plt.legend()
        plt.grid(True)
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close()
        
        # متن جامع گزارش تحلیلی همراه با سیگنال
        caption = (
            f"📊 **گزارش جامع تحلیلگر Kronos برای {symbol}**\n\n"
            f"🔹 **قیمت لحظه‌ای:** `{last_price:,.2f}`\n"
            f"🔹 **شاخص RSI (14):** `{current_rsi:.1f}`\n"
            f"🔹 **وضعیت روند بازار:** `{regime}`\n"
            f"🔹 **میانگین متحرک (SMA 50):** `{sma_50:,.2f}`\n\n"
            f"🎯 **سیگنال و استراتژی پیشنهاد معامله:**\n"
            f"• **جهت پوزیشن:** **{signal_dir}**\n"
            f"• **نقطه ورود (Entry):** `{entry_price:,.2f}`\n"
            f"• **حد سود (TP):** `{tp:,.2f}`\n"
            f"• **حد ضرر (SL):** `{sl:,.2f}`\n\n"
            f"⚡️ پیش‌بینی مسیر ۲۴ کندل آینده با موفقیت رسم شد."
        )
        
        await update.message.reply_photo(photo=buf, caption=caption, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"خطا در پردازش محاسبات: {str(e)}")

def main():
    if not BOT_TOKEN:
        print("خطا: توکن ربات تنظیم نشده است!")
        return
        
    t = threading.Thread(target=run_web, daemon=True)
    t.start()
    
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("tools", tools_command))
    application.add_handler(CommandHandler("predict", predict_command))
    
    print("ربات پیشرفته Kronos آماده به کار است...")
    application.run_polling()

if __name__ == '__main__':
    main()
