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
    return "Kronos XT.com AI Trading Assistant & Risk Manager is active 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DEFAULT_MARGIN = 5.0
DEFAULT_LEVERAGE = 8

# =========================================================
# توابع کمکی واقعی
# =========================================================

def fetch_klines(symbol: str, interval: str, limit: int = 200):
    """دریافت کندل واقعی از Binance"""
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        res = requests.get(url, timeout=12)
        if res.status_code != 200:
            return None
        data = res.json()
        df = pd.DataFrame(data, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
        ])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df
    except Exception:
        return None

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

def find_significant_nodes(df, atr_mult=1.2, lookback=4):
    """
    شناسایی نودهای معنادار (سقف و کف سوئینگ) با فیلتر ATR
    برای کاهش نویز در تایم‌فریم‌های پایین
    """
    if df is None or len(df) < 30:
        return []

    atr = calculate_atr(df).fillna(method='bfill')
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    nodes = []

    for i in range(lookback, len(df) - lookback):
        # سقف
        if highs[i] == max(highs[i-lookback:i+lookback+1]):
            # فیلتر معناداری نسبت به ATR
            if i > 0 and (highs[i] - min(lows[i-lookback:i])) > atr.iloc[i] * atr_mult:
                nodes.append({
                    'type': 'high',
                    'price': float(highs[i]),
                    'idx': i,
                    'time': df['open_time'].iloc[i]
                })
        # کف
        if lows[i] == min(lows[i-lookback:i+lookback+1]):
            if i > 0 and (max(highs[i-lookback:i]) - lows[i]) > atr.iloc[i] * atr_mult:
                nodes.append({
                    'type': 'low',
                    'price': float(lows[i]),
                    'idx': i,
                    'time': df['open_time'].iloc[i]
                })

    return nodes

def analyze_nds_timeframe(df, tf_name: str):
    """
    تحلیل حرفه‌ای‌تر سبک NDS روی یک تایم‌فریم
    """
    if df is None or len(df) < 50:
        return {
            'tf': tf_name,
            'cycle': 'داده ناکافی',
            'structure': '-',
            'last_high': None,
            'last_low': None,
            'level_86_up': None,
            'level_86_down': None,
            'bias': 'خنثی',
            'comment': 'داده کافی نیست'
        }

    nodes = find_significant_nodes(df)
    last_price = float(df['close'].iloc[-1])

    if len(nodes) < 4:
        return {
            'tf': tf_name,
            'cycle': 'ساختار ناقص',
            'structure': f'فقط {len(nodes)} نود معنادار',
            'last_high': None,
            'last_low': None,
            'level_86_up': None,
            'level_86_down': None,
            'bias': 'خنثی',
            'comment': 'نودهای کافی برای تشخیص سایکل وجود ندارد'
        }

    # جدا کردن سقف و کف
    highs = [n for n in nodes if n['type'] == 'high']
    lows = [n for n in nodes if n['type'] == 'low']

    last_high = highs[-1]['price'] if highs else last_price
    prev_high = highs[-2]['price'] if len(highs) >= 2 else last_high
    last_low = lows[-1]['price'] if lows else last_price
    prev_low = lows[-2]['price'] if len(lows) >= 2 else last_low

    # تشخیص ساختار کلاسیک
    hh = last_high > prev_high
    hl = last_low > prev_low
    lh = last_high < prev_high
    ll = last_low < prev_low

    if hh and hl:
        cycle = "سایکل صعودی (HH + HL)"
        bias = "صعودی"
    elif lh and ll:
        cycle = "سایکل نزولی (LH + LL)"
        bias = "نزولی"
    elif hh and ll:
        cycle = "گسترش / بی‌نظمی"
        bias = "خنثی"
    else:
        cycle = "رنج یا انتقالی"
        bias = "خنثی"

    # محاسبه سطح ۸۶٪ از آخرین موج معتبر
    wave = abs(last_high - last_low)
    level_86_up = last_low + wave * 0.86
    level_86_down = last_high - wave * 0.86

    structure = f"آخرین سقف: {last_high:.4f} | آخرین کف: {last_low:.4f}"

    comment = (
        f"سطح ۸۶٪ صعودی ≈ {level_86_up:.4f} | "
        f"سطح ۸۶٪ نزولی ≈ {level_86_down:.4f}"
    )

    return {
        'tf': tf_name,
        'cycle': cycle,
        'structure': structure,
        'last_high': last_high,
        'last_low': last_low,
        'level_86_up': level_86_up,
        'level_86_down': level_86_down,
        'bias': bias,
        'comment': comment
    }

def get_funding_rate(symbol: str):
    """فاندینگ ریت واقعی لحظه‌ای از Binance Futures"""
    try:
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
        res = requests.get(url, timeout=8)
        if res.status_code == 200:
            data = res.json()
            rate = float(data.get('lastFundingRate', 0)) * 100
            next_time = int(data.get('nextFundingTime', 0)) / 1000
            mark = float(data.get('markPrice', 0))
            next_dt = datetime.fromtimestamp(next_time, tz=timezone.utc).strftime('%H:%M UTC')
            return rate, next_dt, mark
    except Exception:
        pass
    return None, None, None

# =========================================================
# دستورات ربات
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 **سلام! دستیار جامع ترید XT.com و تحلیلگر Kronos فعال است.**\n\n"
        "سیستم‌های فعال:\n"
        "• مونت‌کارلو + اندیکاتورهای واقعی\n"
        "• مدیر ریسک XT.com (مارجین و اهرم قابل تنظیم)\n"
        "• تحلیل حرفه‌ای سبک NDS روی سه تایم‌فریم (۱۵م - ۴س - روزانه)\n"
        "• فاندینگ ریت لحظه‌ای واقعی\n\n"
        "دستورات:\n"
        "`/predict BTCUSDT`\n"
        "`/predict ETHUSDT 7 10`  ← مارجین ۷$ + اهرم ۱۰x\n"
        "`/tools`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def tools_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tools_text = (
        "🛠 **لیست مرجع سایت‌های تخصصی**\n\n"
        "🔍 آن‌چین: Arkham | Lookonchain | Whale Alert | Debank | GMGN\n"
        "🛡 امنیت: RugCheck | Honeypot | TokenSniffer | GoPlus\n"
        "📈 چارت: DexScreener | Birdeye | GeckoTerminal | SolScan\n"
        "⚡️ مشتقات: Coinglass | Coinalyze | Coinank | Hyperliquid\n"
        "🔓 توکناومیکس: DefiLlama Unlocks | Tokenomist | CryptoRank"
    )
    await update.message.reply_text(tools_text, parse_mode="Markdown", disable_web_page_preview=True)

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    symbol = args[0].upper() if args else "BTCUSDT"

    try:
        margin_capital = float(args[1]) if len(args) > 1 else DEFAULT_MARGIN
        leverage = float(args[2]) if len(args) > 2 else DEFAULT_LEVERAGE
    except:
        margin_capital = DEFAULT_MARGIN
        leverage = DEFAULT_LEVERAGE

    await update.message.reply_text(
        f"⏳ دریافت داده‌های واقعی از سه تایم‌فریم + فاندینگ ریت + تحلیل NDS برای `{symbol}`..."
    )

    try:
        # ---------- دریافت داده‌های واقعی ----------
        df_1h = fetch_klines(symbol, "1h", 300)
        df_15m = fetch_klines(symbol, "15m", 200)
        df_4h = fetch_klines(symbol, "4h", 150)
        df_1d = fetch_klines(symbol, "1d", 100)

        if df_1h is None or len(df_1h) < 60:
            await update.message.reply_text("❌ خطا در دریافت داده ۱ ساعته. نماد را بررسی کنید.")
            return

        last_price = float(df_1h['close'].iloc[-1])
        current_volume = float(df_1h['volume'].iloc[-1])
        avg_volume = float(df_1h['volume'].tail(20).mean())

        # ---------- اندیکاتورهای واقعی ----------
        current_rsi = safe_rsi(df_1h['close']).iloc[-1]
        sma_50 = df_1h['close'].rolling(50).mean().iloc[-1]
        atr = calculate_atr(df_1h).iloc[-1]
        atr_pct = (atr / last_price) * 100

        # ---------- فاندینگ ریت واقعی ----------
        funding_rate, next_funding, mark_price = get_funding_rate(symbol)

        # ---------- تحلیل NDS روی سه تایم‌فریم ----------
        nds_15m = analyze_nds_timeframe(df_15m, "۱۵ دقیقه")
        nds_4h = analyze_nds_timeframe(df_4h, "۴ ساعته")
        nds_1d = analyze_nds_timeframe(df_1d, "روزانه")

        # بایاس کلی از تایم‌فریم بالاتر
        higher_bias = nds_1d['bias']
        if higher_bias == "خنثی":
            higher_bias = nds_4h['bias']

        # ---------- مونت‌کارلو ----------
        returns = np.diff(np.log(df_1h['close'].values))
        mu, sigma = np.mean(returns), np.std(returns)
        simulations = np.zeros((80, 24))
        for i in range(80):
            sim_returns = np.random.normal(mu, sigma, 24)
            simulations[i] = last_price * np.exp(np.cumsum(sim_returns))
        mean_path = np.mean(simulations, axis=0)
        std_end = np.std(simulations[:, -1])
        regime = "رونددار" if std_end / last_price < 0.028 else "نوسانی/پرریسک"

        # ---------- سیگنال + مدیریت ریسک ----------
        position_size = margin_capital * leverage

        if mean_path[-1] > last_price * 1.006 and current_rsi < 67 and higher_bias != "نزولی":
            signal_dir = "🟢 LONG"
            entry = last_price
            sl = last_price - (atr * 1.35)
            tp = last_price + (atr * 2.1)
            defense = "تز صعودی با تأیید نسبی تایم‌فریم بالاتر."
        elif mean_path[-1] < last_price * 0.994 and current_rsi > 33 and higher_bias != "صعودی":
            signal_dir = "🔴 SHORT"
            entry = last_price
            sl = last_price + (atr * 1.35)
            tp = last_price - (atr * 2.1)
            defense = "فشار فروش با تأیید نسبی تایم‌فریم بالاتر."
        else:
            signal_dir = "⚪️ RANGE / صبر"
            entry = last_price
            sl = last_price - (atr * 1.1)
            tp = last_price + (atr * 1.1)
            defense = "عدم هم‌راستایی کافی بین تایم‌فریم‌ها یا رژیم نوسانی."

        risk_usd = abs(entry - sl) * (position_size / entry)

        # ---------- رسم نمودار ----------
        plt.figure(figsize=(11, 5.5))
        plt.plot(df_1h['close'].values[-120:], color="#1f77b4", label="قیمت ۱H", linewidth=1.4)
        last_idx = 119
        for sim in simulations[::5]:
            plt.plot(range(last_idx, last_idx + 24), sim, color="gray", alpha=0.07)
        plt.plot(range(last_idx, last_idx + 24), mean_path, color="red", linewidth=2, label="میانگین مونت‌کارلو")
        plt.axhline(sma_50, color="orange", linestyle="--", alpha=0.7, label="SMA50")
        plt.title(f"Kronos XT.com | {symbol} | Bias: {higher_bias} | {regime}", fontsize=11)
        plt.legend(loc="upper left")
        plt.grid(True, alpha=0.3)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=120)
        buf.seek(0)
        plt.close('all')

        # ---------- گزارش نهایی ----------
        funding_text = f"`{funding_rate:.4f}%`" if funding_rate is not None else "`نامشخص`"
        next_fund_text = next_funding if next_funding else "-"

        caption = (
            f"🎯 **گزارش کامل ترید XT.com + Kronos برای `{symbol}`**\n\n"

            f"📌 **۱. مدیر ریسک**\n"
            f"• مارجین: `${margin_capital}` | اهرم: `{leverage}x Isolated`\n"
            f"• حجم پوزیشن: `${position_size:.1f}`\n"
            f"• ریسک تقریبی این ستاپ: `${risk_usd:.2f}`\n"
            f"• قیمت لحظه‌ای: `{last_price:,.4f}`\n\n"

            f"📊 **۲. اندیکاتورهای واقعی (۱H)**\n"
            f"• RSI(14): `{current_rsi:.1f}`\n"
            f"• SMA 50: `{sma_50:,.4f}` (فاصله: `{((last_price/sma_50)-1)*100:+.2f}%`)\n"
            f"• ATR(14): `{atr:.4f}` (`{atr_pct:.2f}%`)\n"
            f"• حجم فعلی / میانگین ۲۰: `{current_volume/avg_volume:.2f}x`\n"
            f"• رژیم: `{regime}`\n\n"

            f"💰 **۳. فاندینگ ریت لحظه‌ای**\n"
            f"• فاندینگ فعلی: {funding_text}\n"
            f"• زمان فاندینگ بعدی: `{next_fund_text}`\n"
            f"• Mark Price: `{mark_price:,.4f}`\n\n"

            f"⚖️ **۴. سیگنال پیشنهادی**\n"
            f"• جهت: **{signal_dir}**\n"
            f"• ورود: `{entry:,.4f}`\n"
            f"• حد ضرر (ATR): `{sl:,.4f}`\n"
            f"• حد سود: `{tp:,.4f}`\n\n"

            f"🧬 **۵. تحلیل سبک NDS (چندتایم‌فریمی)**\n\n"
            f"**▸ روزانه (1D)**\n"
            f"• سایکل: {nds_1d['cycle']}\n"
            f"• بایاس: **{nds_1d['bias']}**\n"
            f"• {nds_1d['structure']}\n"
            f"• {nds_1d['comment']}\n\n"

            f"**▸ ۴ ساعته (4H)**\n"
            f"• سایکل: {nds_4h['cycle']}\n"
            f"• بایاس: **{nds_4h['bias']}**\n"
            f"• {nds_4h['structure']}\n"
            f"• {nds_4h['comment']}\n\n"

            f"**▸ ۱۵ دقیقه (15m)**\n"
            f"• سایکل: {nds_15m['cycle']}\n"
            f"• بایاس: **{nds_15m['bias']}**\n"
            f"• {nds_15m['structure']}\n"
            f"• {nds_15m['comment']}\n\n"

            f"🛡 **۶. وکیل مدافع و جمع‌بندی**\n"
            f"• بایاس غالب تایم‌فریم بالاتر: **{higher_bias}**\n"
            f"• {defense}\n"
            f"• سناریوی همسو با NDS روزانه: اولویت بالاتر\n"
            f"• سناریوی مخالف تایم‌فریم بالاتر: ریسک بیشتر، سایز کمتر\n\n"

            f"🔗 پیشنهاد: فاندینگ را در Coinglass و جریان نهنگ را در Arkham همزمان بررسی کنید."
        )

        await update.message.reply_photo(photo=buf, caption=caption, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در پردازش:\n`{str(e)}`")

def main():
    if not BOT_TOKEN:
        print("خطا: توکن ربات تنظیم نشده!")
        return

    t = threading.Thread(target=run_web, daemon=True)
    t.start()

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("tools", tools_command))
    application.add_handler(CommandHandler("predict", predict_command))

    print("Kronos XT.com AI (NDS Multi-TF) آماده است...")
    application.run_polling()

if __name__ == '__main__':
    main()