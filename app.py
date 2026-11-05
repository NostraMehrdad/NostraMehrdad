import os
import threading
import io
import time
import requests
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ---------------- وب‌سرور برای Render ----------------
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Kronos XT.com AI Trading Assistant & Risk Manager is active 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

# ---------------- تنظیمات ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# پارامترهای پیش‌فرض ریسک (قابل تغییر)
DEFAULT_MARGIN = 5.0
DEFAULT_LEVERAGE = 8

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **سلام! دستیار جامع ترید XT.com و تحلیلگر Kronos فعال است.**\n\n"
        "🎯 **سیستم‌های فعال همزمان:**\n"
        "1️⃣ **چارت و شبیه‌سازی مونت‌کارلو** (به همراه RSI و SMA 50)\n"
        "2️⃣ **مدیر ریسک صرافی XT.com** (مارجین ۵$ و اهرم 8x ایزوله)\n"
        "3️⃣ **وکیل مدافع ترید** (تحلیل سناریوهای صعودی، نزولی و رنج)\n\n"
        "دستورات موجود:\n"
        "📊 `/predict BTCUSDT` -> تحلیل کامل + نمودار\n"
        "📊 `/predict ETHUSDT 10 10` -> مارجین ۱۰ دلار + اهرم ۱۰x\n"
        "🛠 `/tools` -> لیست سایت‌های مرجع"
    )

async def tools_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tools_text = (
        "🛠 **لیست مرجع سایت‌های تخصصی بازار کریپتو**\n\n"
        "🔍 **آن‌چین و نهنگ‌ها:**\n"
        "• [Arkham](https://arkm.com) | [Lookonchain](https://lookonchain.com)\n"
        "• [Whale Alert](https://whale-alert.ie) | [Debank](https://debank.com)\n"
        "• [GMGN](https://gmgn.ai) | [Kolscan](https://kolscan.ie)\n\n"
        "🛡 **امنیت و اسکن اسکم:**\n"
        "• [RugCheck](https://rugcheck.xyz) | [Honeypot](https://honeypot.is)\n"
        "• [TokenSniffer](https://tokensniffer.com) | [GoPlus](https://gopluslabs.io)\n\n"
        "📈 **چارت‌ها و دکس‌ها:**\n"
        "• [DexScreener](https://dexscreener.com) | [Birdeye](https://birdeye.so)\n"
        "• [GeckoTerminal](https://geckoterminal.com) | [SolScan](https://solscan.io)\n\n"
        "⚡️ **مشتقات و فاندینگ:**\n"
        "• [Coinglass](https://www.coinglass.com) | [Coinalyze](https://coinalyze.net)\n"
        "• [Coinank](https://coinank.com) | [Hyperliquid](https://app.hyperliquid.xyz)\n\n"
        "🔓 **توکناومیکس:**\n"
        "• [DefiLlama Unlocks](https://defillama.com/unlocks)\n"
        "• [Tokenomist](https://tokenomist.ai) | [CryptoRank](https://cryptorank.ie)"
    )
    await update.message.reply_text(tools_text, parse_mode="Markdown", disable_web_page_preview=True)

def safe_rsi(close_series, period=14):
    """محاسبه امن RSI با جلوگیری از تقسیم بر صفر"""
    delta = close_series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)          # جلوگیری از ZeroDivision
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)                        # در صورت NaN، مقدار خنثی

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    symbol = args[0].upper() if args else "BTCUSDT"
    
    # امکان تغییر مارجین و اهرم: /predict BTCUSDT 7 10
    try:
        margin_capital = float(args[1]) if len(args) > 1 else DEFAULT_MARGIN
        leverage = float(args[2]) if len(args) > 2 else DEFAULT_LEVERAGE
    except:
        margin_capital = DEFAULT_MARGIN
        leverage = DEFAULT_LEVERAGE

    await update.message.reply_text(
        f"⏳ دریافت کندل‌ها، محاسبات مونت‌کارلو و ارزیابی ریسک برای `{symbol}` "
        f"(مارجین ${margin_capital} | اهرم {leverage}x)..."
    )

    try:
        url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval=1h&limit=512"
        
        # درخواست با timeout و یک بار retry ساده
        for attempt in range(2):
            try:
                res = requests.get(url, timeout=12)
                if res.status_code == 200:
                    break
            except requests.exceptions.RequestException:
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                raise
        
        if res.status_code != 200:
            await update.message.reply_text(
                f"❌ خطا در دریافت داده از Binance Vision (کد {res.status_code}).\n"
                "نماد را به صورت صحیح وارد کنید (مثال: /predict BTCUSDT)"
            )
            return

        data = res.json()
        if not data or len(data) < 60:
            await update.message.reply_text("❌ داده کافی دریافت نشد. نماد را بررسی کنید.")
            return

        df = pd.DataFrame(data, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
        ])
        df['close'] = df['close'].astype(float)
        
        last_price = float(df['close'].iloc[-1])
        
        # ---- مدیر ریسک ----
        position_size = margin_capital * leverage
        
        # ---- اندیکاتورها ----
        current_rsi = safe_rsi(df['close']).iloc[-1]
        sma_50 = df['close'].rolling(window=50).mean().iloc[-1]
        
        # ---- مونت‌کارلو ----
        returns = np.diff(np.log(df['close'].values))
        num_predictions = 24
        num_samples = 100
        simulations = np.zeros((num_samples, num_predictions))
        
        mu = np.mean(returns)
        sigma = np.std(returns)
        
        for i in range(num_samples):
            sim_returns = np.random.normal(mu, sigma, num_predictions)
            simulations[i] = last_price * np.exp(np.cumsum(sim_returns))
        
        mean_path = np.mean(simulations, axis=0)
        std_end = np.std(simulations[:, -1])
        regime = "رونددار (Trending)" if std_end / last_price < 0.03 else "نوسانی و پرریسک (Choppy/Range)"
        
        # ---- سیگنال ----
        if mean_path[-1] > last_price and current_rsi < 65:
            signal_dir = "🟢 LONG (خرید)"
            entry_price = last_price
            tp = last_price * 1.025
            sl = last_price * 0.985
            defense_note = (
                f"تز صعودی تایید می‌شود. با اهرم {leverage}x ایزوله، "
                f"حد ضرر ۱.۵٪ مارجین ${margin_capital} شما را محافظت می‌کند."
            )
        elif mean_path[-1] < last_price and current_rsi > 35:
            signal_dir = "🔴 SHORT (فروش)"
            entry_price = last_price
            tp = last_price * 0.975
            sl = last_price * 1.015
            defense_note = (
                f"فشار فروش در مدل دیده می‌شود. آماده ورود به شورت با "
                f"حد ضرر دقیق در XT.com باشید."
            )
        else:
            signal_dir = "⚪️ RANGE (رنج / کم‌نوسان)"
            entry_price = last_price
            tp = last_price * 1.012
            sl = last_price * 0.988
            defense_note = (
                "بازار بین حمایت و مقاومت سرگردان است. "
                "توصیه وکیل مدافع: از پوزیشن سنگین پرهیز کنید یا منتظر شکست بمانید."
            )

        # ---- رسم نمودار ----
        plt.figure(figsize=(10, 5))
        plt.plot(df['close'].values, color="blue", label="کندل‌های گذشته", linewidth=1.2)
        last_idx = len(df['close']) - 1
        for sim in simulations:
            plt.plot(range(last_idx, last_idx + num_predictions), sim, color="gray", alpha=0.08)
        plt.plot(range(last_idx, last_idx + num_predictions), mean_path, color="red", linewidth=2.2, label="میانگین پیش‌بینی")
        plt.title(f"Kronos XT.com AI - {symbol} | Regime: {regime}", fontsize=11)
        plt.legend(loc="upper left")
        plt.grid(True, alpha=0.3)
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=110)
        buf.seek(0)
        plt.close('all')
        
        # ---- گزارش ----
        caption = (
            f"🎯 **گزارش جامع ترید XT.com و تحلیل Kronos برای `{symbol}`**\n\n"
            f"📌 **۱. بخش مدیر ریسک:**\n"
            f"• مارجین: `${margin_capital}` | اهرم: `{leverage}x Isolated`\n"
            f"• حجم پوزیشن (Notional): `${position_size:.1f}`\n"
            f"• قیمت لحظه‌ای: `{last_price:,.2f}`\n\n"
            f"📊 **۲. تحلیل تکنیکال:**\n"
            f"• RSI (14): `{current_rsi:.1f}`\n"
            f"• SMA 50: `{sma_50:,.2f}`\n"
            f"• رژیم بازار: `{regime}`\n\n"
            f"⚖️ **۳. سیگنال پیشنهادی:**\n"
            f"• جهت: **{signal_dir}**\n"
            f"• ورود: `{entry_price:,.2f}`\n"
            f"• حد سود (TP): `{tp:,.2f}`\n"
            f"• حد ضرر (SL): `{sl:,.2f}`\n\n"
            f"🛡 **۴. وکیل مدافع ترید:**\n"
            f"• سناریوی صعودی: حرکت به سمت مقاومت‌های بالاتر با تایید حجم\n"
            f"• سناریوی نزولی: احتمال فیک‌بریک‌آوت و ریزش تا حمایت‌های کلیدی\n"
            f"• سناریوی رنج: نوسان فرسایشی در محدوده فعلی\n"
            f"• 💡 *ارزیابی و چالش تز:* {defense_note}\n\n"
            f"🔗 پیشنهاد: فاندینگ ریت را در Coinglass و جریان نقدینگی را در Arkham چک کنید."
        )
        
        await update.message.reply_photo(photo=buf, caption=caption, parse_mode="Markdown")
        
    except Exception as e:
        await update.message.reply_text(
            f"❌ خطا در پردازش:\n`{str(e)}`\n\n"
            "اگر نماد را درست وارد کردید و باز هم خطا می‌دهد، چند لحظه بعد دوباره امتحان کنید."
        )

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
    
    print("دستیار جامع XT.com و تحلیلگر Kronos آماده به کار است...")
    application.run_polling()

if __name__ == '__main__':
    main()