import os
import threading
import io
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ---------------- وب‌سرور Render ----------------
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Kronos XT.com Professional AI Analyst is active 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DEFAULT_MARGIN = 5.0
DEFAULT_LEVERAGE = 8

# =========================================================
# توابع دریافت داده واقعی
# =========================================================

def fetch_klines(symbol: str, interval: str, limit: int = 200):
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        res = requests.get(url, timeout=12)
        if res.status_code == 200:
            data = res.json()
            df = pd.DataFrame(data, columns=[
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
            ])
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
            return df
    except Exception:
        pass
    return None

def get_funding_rate(symbol: str):
    """دریافت فاندینگ ریت واقعی با چند تلاش"""
    endpoints = [
        f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}",
        f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
    ]
    for url in endpoints:
        try:
            res = requests.get(url, timeout=8)
            if res.status_code == 200:
                data = res.json()
                rate = float(data.get('lastFundingRate', 0)) * 100
                next_time = int(data.get('nextFundingTime', 0)) / 1000
                mark = float(data.get('markPrice', 0))
                next_dt = datetime.fromtimestamp(next_time, tz=timezone.utc).strftime('%H:%M UTC')
                return rate, next_dt, mark
        except Exception:
            continue
    return None, None, None

def get_recent_news(symbol: str = "BTC"):
    """دریافت اخبار اخیر از CryptoCompare (منبع واقعی و رایگان)"""
    try:
        url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&categories=BTC,ETH,Trading,Market"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            news_list = data.get('Data', [])[:4]
            result = []
            for n in news_list:
                title = n.get('title', '')[:90]
                source = n.get('source', '')
                result.append(f"• {title} ({source})")
            return result if result else ["خبر جدیدی در دسترس نیست"]
    except Exception:
        pass
    return ["دریافت اخبار با مشکل مواجه شد"]

# =========================================================
# اندیکاتورها و NDS
# =========================================================

def safe_rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def calculate_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def find_significant_nodes(df, atr_mult=1.15, lookback=4):
    if df is None or len(df) < 30:
        return []
    atr = calculate_atr(df).bfill()
    highs = df['high'].values
    lows = df['low'].values
    nodes = []
    for i in range(lookback, len(df) - lookback):
        if highs[i] == max(highs[i-lookback:i+lookback+1]):
            if (highs[i] - min(lows[i-lookback:i])) > atr.iloc[i] * atr_mult:
                nodes.append({'type': 'high', 'price': float(highs[i])})
        if lows[i] == min(lows[i-lookback:i+lookback+1]):
            if (max(highs[i-lookback:i]) - lows[i]) > atr.iloc[i] * atr_mult:
                nodes.append({'type': 'low', 'price': float(lows[i])})
    return nodes

def analyze_nds_timeframe(df, tf_name: str):
    if df is None or len(df) < 50:
        return {'tf': tf_name, 'cycle': 'داده ناکافی', 'structure': '-', 'bias': 'خنثی', 'comment': '-'}

    nodes = find_significant_nodes(df)
    last_price = float(df['close'].iloc[-1])

    if len(nodes) < 4:
        return {'tf': tf_name, 'cycle': 'ساختار ناقص', 'structure': f'{len(nodes)} نود', 'bias': 'خنثی', 'comment': '-'}

    highs = [n for n in nodes if n['type'] == 'high']
    lows  = [n for n in nodes if n['type'] == 'low']

    last_high = highs[-1]['price'] if highs else last_price
    prev_high = highs[-2]['price'] if len(highs) >= 2 else last_high
    last_low  = lows[-1]['price'] if lows else last_price
    prev_low  = lows[-2]['price'] if len(lows) >= 2 else last_low

    hh = last_high > prev_high
    hl = last_low > prev_low
    lh = last_high < prev_high
    ll = last_low < prev_low

    if hh and hl:
        cycle, bias = "سایکل صعودی (HH+HL)", "صعودی"
    elif lh and ll:
        cycle, bias = "سایکل نزولی (LH+LL)", "نزولی"
    else:
        cycle, bias = "رنج / انتقالی", "خنثی"

    wave = abs(last_high - last_low)
    level_86_up = last_low + wave * 0.86
    level_86_down = last_high - wave * 0.86

    structure = f"سقف: {last_high:.2f} | کف: {last_low:.2f}"
    comment = f"۸۶٪↑ {level_86_up:.2f} | ۸۶٪↓ {level_86_down:.2f}"

    return {
        'tf': tf_name,
        'cycle': cycle,
        'structure': structure,
        'bias': bias,
        'comment': comment,
        'level_86_up': level_86_up,
        'level_86_down': level_86_down
    }

def safe_format(value, fmt=".4f"):
    if value is None:
        return "نامشخص"
    try:
        return format(value, fmt)
    except:
        return str(value)

# =========================================================
# دستورات ربات
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 **Kronos | تحلیلگر حرفه‌ای XT.com**\n\n"
        "این ربات بر پایه داده‌های واقعی کار می‌کند و اولویت اصلی آن **سبک NDS** است.\n\n"
        "قابلیت‌ها:\n"
        "• تحلیل NDS چندتایم‌فریمی (۱۵م / ۴س / روزانه)\n"
        "• فاندینگ ریت واقعی\n"
        "• مدیریت ریسک XT.com\n"
        "• مونت‌کارلو + اندیکاتورهای کلاسیک\n"
        "• اخبار مرتبط بازار\n\n"
        "دستورات:\n"
        "`/predict BTCUSDT`\n"
        "`/predict ETHUSDT 10 8`\n"
        "`/tools`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def tools_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tools = (
        "🛠 **منابع حرفه‌ای مورد نیاز تریدر**\n\n"
        "📊 چارت و ساختار:\n"
        "• TradingView | DexScreener | Coinglass\n\n"
        "🧬 NDS و ساختار بازار:\n"
        "• بررسی نودها و سطح ۸۶٪ روی چارت شخصی\n\n"
        "💰 مشتقات و فاندینگ:\n"
        "• Coinglass | Coinalyze | Binance Futures\n\n"
        "🐋 آن‌چین و نهنگ:\n"
        "• Arkham | Lookonchain | Whale Alert | Debank\n\n"
        "📰 اخبار:\n"
        "• CryptoPanic | CoinDesk | The Block\n\n"
        "🔓 توکناومیکس:\n"
        "• DefiLlama | Tokenomist | CryptoRank"
    )
    await update.message.reply_text(tools, parse_mode="Markdown", disable_web_page_preview=True)

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    symbol = args[0].upper() if args else "BTCUSDT"

    try:
        margin_capital = float(args[1]) if len(args) > 1 else DEFAULT_MARGIN
        leverage = float(args[2]) if len(args) > 2 else DEFAULT_LEVERAGE
    except:
        margin_capital = DEFAULT_MARGIN
        leverage = DEFAULT_LEVERAGE

    await update.message.reply_text(f"⏳ در حال تحلیل حرفه‌ای `{symbol}` با اولویت NDS...")

    try:
        # دریافت داده‌های واقعی
        df_1h  = fetch_klines(symbol, "1h", 300)
        df_15m = fetch_klines(symbol, "15m", 200)
        df_4h  = fetch_klines(symbol, "4h", 150)
        df_1d  = fetch_klines(symbol, "1d", 120)

        if df_1h is None or len(df_1h) < 60:
            await update.message.reply_text("❌ خطا در دریافت داده واقعی از بایننس.")
            return

        last_price = float(df_1h['close'].iloc[-1])
        current_volume = float(df_1h['volume'].iloc[-1])
        avg_volume = float(df_1h['volume'].tail(20).mean())

        current_rsi = safe_rsi(df_1h['close']).iloc[-1]
        sma_50 = df_1h['close'].rolling(50).mean().iloc[-1]
        atr = calculate_atr(df_1h).iloc[-1]
        atr_pct = (atr / last_price) * 100

        funding_rate, next_funding, mark_price = get_funding_rate(symbol)
        news = get_recent_news(symbol.replace("USDT", ""))

        # تحلیل NDS (اولویت اصلی)
        nds_15m = analyze_nds_timeframe(df_15m, "۱۵ دقیقه")
        nds_4h  = analyze_nds_timeframe(df_4h, "۴ ساعته")
        nds_1d  = analyze_nds_timeframe(df_1d, "روزانه")

        # بایاس غالب از تایم‌فریم بالاتر (اولویت NDS)
        higher_bias = nds_1d['bias']
        if higher_bias == "خنثی":
            higher_bias = nds_4h['bias']

        # مونت‌کارلو
        returns = np.diff(np.log(df_1h['close'].values))
        mu, sigma = np.mean(returns), np.std(returns)
        simulations = np.zeros((70, 24))
        for i in range(70):
            sim_returns = np.random.normal(mu, sigma, 24)
            simulations[i] = last_price * np.exp(np.cumsum(sim_returns))
        mean_path = np.mean(simulations, axis=0)
        regime = "رونددار" if np.std(simulations[:, -1]) / last_price < 0.03 else "نوسانی"

        # ========== منطق سیگنال با اولویت NDS ==========
        position_size = margin_capital * leverage

        # فقط زمانی سیگنال جهت‌دار می‌دهیم که NDS تایم‌فریم بالاتر تأیید کند
        long_condition = (
            higher_bias == "صعودی" and
            mean_path[-1] > last_price and
            current_rsi < 65
        )
        short_condition = (
            higher_bias == "نزولی" and
            mean_path[-1] < last_price and
            current_rsi > 35
        )

        if long_condition:
            signal_dir = "🟢 LONG"
            entry = last_price
            sl = last_price - (atr * 1.6)
            tp = last_price + (atr * 2.4)
            reason = "NDS تایم‌فریم بالاتر صعودی است و با مونت‌کارلو و RSI هم‌راستا شده."
        elif short_condition:
            signal_dir = "🔴 SHORT"
            entry = last_price
            sl = last_price + (atr * 1.6)
            tp = last_price - (atr * 2.4)
            reason = "NDS تایم‌فریم بالاتر نزولی است و با مونت‌کارلو و RSI هم‌راستا شده."
        else:
            signal_dir = "⚪️ صبر / RANGE"
            entry = last_price
            sl = last_price - (atr * 1.3)
            tp = last_price + (atr * 1.3)
            reason = "اولویت با NDS است. تایم‌فریم‌های بالاتر هم‌راستایی کافی برای ورود جهت‌دار ندارند."

        risk_usd = abs(entry - sl) * (position_size / entry)
        risk_pct = (risk_usd / margin_capital) * 100

        # رسم نمودار
        plt.figure(figsize=(11, 5.2))
        plt.plot(df_1h['close'].values[-100:], color="#1f77b4", linewidth=1.4, label="قیمت ۱H")
        last_idx = 99
        for sim in simulations[::6]:
            plt.plot(range(last_idx, last_idx+24), sim, color="gray", alpha=0.06)
        plt.plot(range(last_idx, last_idx+24), mean_path, color="red", linewidth=2, label="مسیر میانگین")
        plt.axhline(sma_50, color="orange", linestyle="--", alpha=0.7, label="SMA50")
        plt.title(f"Kronos Professional | {symbol} | NDS Bias: {higher_bias}", fontsize=11)
        plt.legend(loc="upper left", fontsize=8)
        plt.grid(True, alpha=0.3)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=120)
        buf.seek(0)
        plt.close('all')

        # ساخت گزارش حرفه‌ای
        funding_text = f"{safe_format(funding_rate, '.4f')}%" if funding_rate is not None else "نامشخص"
        mark_text = safe_format(mark_price, ",.2f")
        next_fund_text = next_funding if next_funding else "-"

        news_text = "\n".join(news)

        caption = (
            f"🎯 **تحلیل حرفه‌ای Kronos | `{symbol}`**\n\n"
            f"📌 **مدیریت ریسک XT.com**\n"
            f"• مارجین: `${margin_capital}` | اهرم: `{leverage}x Isolated`\n"
            f"• حجم پوزیشن: `${position_size:.1f}`\n"
            f"• ریسک این ستاپ: `${risk_usd:.2f}` ({risk_pct:.1f}% از مارجین)\n"
            f"• قیمت: `{last_price:,.2f}`\n\n"
            f"📊 **اندیکاتورهای واقعی**\n"
            f"• RSI(14): `{current_rsi:.1f}`\n"
            f"• SMA50: `{sma_50:,.2f}`\n"
            f"• ATR(14): `{atr:.2f}` ({atr_pct:.2f}%)\n"
            f"• حجم نسبی: `{current_volume/avg_volume:.2f}x`\n"
            f"• رژیم: `{regime}`\n\n"
            f"💰 **فاندینگ ریت**\n"
            f"• نرخ فعلی: `{funding_text}`\n"
            f"• زمان بعدی: `{next_fund_text}`\n"
            f"• Mark Price: `{mark_text}`\n\n"
            f"⚖️ **سیگنال نهایی (اولویت NDS)**\n"
            f"• جهت: **{signal_dir}**\n"
            f"• ورود: `{entry:,.2f}`\n"
            f"• حد ضرر: `{sl:,.2f}`\n"
            f"• حد سود: `{tp:,.2f}`\n"
            f"• دلیل: {reason}\n\n"
            f"🧬 **تحلیل NDS (اولویت اصلی)**\n\n"
            f"**روزانه:** {nds_1d['cycle']} → **{nds_1d['bias']}**\n"
            f"{nds_1d['structure']}\n{nds_1d['comment']}\n\n"
            f"**۴ ساعته:** {nds_4h['cycle']} → **{nds_4h['bias']}**\n"
            f"{nds_4h['structure']}\n{nds_4h['comment']}\n\n"
            f"**۱۵ دقیقه:** {nds_15m['cycle']} → **{nds_15m['bias']}**\n"
            f"{nds_15m['structure']}\n{nds_15m['comment']}\n\n"
            f"📰 **اخبار مرتبط اخیر**\n{news_text}\n\n"
            f"🛡 **جمع‌بندی وکیل مدافع**\n"
            f"بایاس غالب NDS: **{higher_bias}**\n"
            f"ورود فقط زمانی توصیه می‌شود که ساختار NDS تایم‌فریم بالاتر با سایر ابزارها هم‌راستا باشد."
        )

        await update.message.reply_photo(photo=buf, caption=caption, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در تحلیل:\n`{str(e)}`")

def main():
    if not BOT_TOKEN:
        print("خطا: BOT_TOKEN تنظیم نشده است!")
        return

    t = threading.Thread(target=run_web, daemon=True)
    t.start()

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("tools", tools_command))
    application.add_handler(CommandHandler("predict", predict_command))

    print("Kronos Professional Analyst آماده است...")
    application.run_polling()

if __name__ == '__main__':
    main()