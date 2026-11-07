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

app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Kronos Professional Analyst is active 24/7"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DEFAULT_MARGIN = 5.0
DEFAULT_LEVERAGE = 8

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def fetch_klines(symbol, interval, limit=250):
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=12)
        if res.status_code == 200:
            data = res.json()
            df = pd.DataFrame(data, columns=[
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
            ])
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
            return df
    except:
        pass
    return None

def get_funding_rate(symbol):
    """تلاش برای دریافت فاندینگ ریت واقعی از بایننس"""
    for _ in range(2):
        try:
            url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
            res = requests.get(url, headers=HEADERS, timeout=8)
            if res.status_code == 200:
                data = res.json()
                rate = float(data.get('lastFundingRate', 0)) * 100
                next_ts = int(data.get('nextFundingTime', 0)) / 1000
                mark = float(data.get('markPrice', 0))
                next_str = datetime.fromtimestamp(next_ts, tz=timezone.utc).strftime('%H:%M UTC')
                return rate, next_str, mark
        except:
            time.sleep(0.6)
    return None, None, None

def safe_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)

def calculate_atr(df, period=14):
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low'] - df['close'].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def add_ichimoku(df):
    high_9 = df['high'].rolling(9).max()
    low_9 = df['low'].rolling(9).min()
    df['tenkan'] = (high_9 + low_9) / 2
    high_26 = df['high'].rolling(26).max()
    low_26 = df['low'].rolling(26).min()
    df['kijun'] = (high_26 + low_26) / 2
    df['senkou_a'] = ((df['tenkan'] + df['kijun']) / 2).shift(26)
    high_52 = df['high'].rolling(52).max()
    low_52 = df['low'].rolling(52).min()
    df['senkou_b'] = ((high_52 + low_52) / 2).shift(26)
    return df

def calculate_fibonacci(df, lookback=90):
    recent = df.tail(lookback)
    swing_high = recent['high'].max()
    swing_low = recent['low'].min()
    diff = swing_high - swing_low
    return {
        '0.0': swing_high,
        '0.236': swing_high - diff * 0.236,
        '0.382': swing_high - diff * 0.382,
        '0.5': swing_high - diff * 0.5,
        '0.618': swing_high - diff * 0.618,
        '0.786': swing_high - diff * 0.786,
        '1.0': swing_low
    }

def find_nodes(df, atr_mult=1.1, lookback=4):
    if df is None or len(df) < 40:
        return []
    atr = calculate_atr(df).bfill()
    nodes = []
    highs = df['high'].values
    lows = df['low'].values
    for i in range(lookback, len(df) - lookback):
        if highs[i] == max(highs[i-lookback:i+lookback+1]):
            if (highs[i] - min(lows[i-lookback:i])) > atr.iloc[i] * atr_mult:
                nodes.append({'type': 'high', 'price': float(highs[i])})
        if lows[i] == min(lows[i-lookback:i+lookback+1]):
            if (max(highs[i-lookback:i]) - lows[i]) > atr.iloc[i] * atr_mult:
                nodes.append({'type': 'low', 'price': float(lows[i])})
    return nodes

def analyze_nds(df):
    if df is None or len(df) < 50:
        return {'bias': 'خنثی', 'cycle': '—', 'info': '—'}
    nodes = find_nodes(df)
    price = float(df['close'].iloc[-1])
    if len(nodes) < 4:
        return {'bias': 'خنثی', 'cycle': 'ناقص', 'info': '—'}

    highs = [n['price'] for n in nodes if n['type'] == 'high']
    lows  = [n['price'] for n in nodes if n['type'] == 'low']
    last_h = highs[-1] if highs else price
    prev_h = highs[-2] if len(highs) > 1 else last_h
    last_l = lows[-1] if lows else price
    prev_l = lows[-2] if len(lows) > 1 else last_l

    if last_h > prev_h and last_l > prev_l:
        bias, cycle = "صعودی", "HH+HL"
    elif last_h < prev_h and last_l < prev_l:
        bias, cycle = "نزولی", "LH+LL"
    else:
        bias, cycle = "خنثی", "رنج"

    info = f"{last_h:.2f} / {last_l:.2f}"
    return {'bias': bias, 'cycle': cycle, 'info': info}

def safe_fmt(v, fmt=".4f"):
    if v is None:
        return None
    try:
        return format(v, fmt)
    except:
        return str(v)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 **Kronos | تحلیلگر حرفه‌ای XT.com**\n\n"
        "اولویت اصلی: سبک NDS\n"
        "فاندینگ + مونت‌کارلو + ایچیموکو + فیبوناچی + مدیریت ریسک\n\n"
        "`/predict BTCUSDT`\n"
        "`/predict XRPUSDT 6 10`\n"
        "`/tools`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def tools_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🛠 **منابع اصلی**\n\n"
        "• Coinglass (فاندینگ و لیکوئیدیشن)\n"
        "• Arkham + Lookonchain (نهنگ‌ها)\n"
        "• TradingView\n"
        "• DexScreener + GeckoTerminal\n"
        "• CryptoPanic + DefiLlama"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    symbol = args[0].upper() if args else "BTCUSDT"
    try:
        margin = float(args[1]) if len(args) > 1 else DEFAULT_MARGIN
        lev = float(args[2]) if len(args) > 2 else DEFAULT_LEVERAGE
    except:
        margin, lev = DEFAULT_MARGIN, DEFAULT_LEVERAGE

    status = await update.message.reply_text(f"⏳ تحلیل کامل `{symbol}` ...")

    try:
        df_1h = fetch_klines(symbol, "1h", 250)
        df_15m = fetch_klines(symbol, "15m", 180)
        df_4h = fetch_klines(symbol, "4h", 150)
        df_1d = fetch_klines(symbol, "1d", 120)

        if df_1h is None or len(df_1h) < 60:
            await status.edit_text("❌ خطا در دریافت داده.")
            return

        df_1h = add_ichimoku(df_1h)
        price = float(df_1h['close'].iloc[-1])
        rsi = float(safe_rsi(df_1h['close']).iloc[-1])
        atr = float(calculate_atr(df_1h).iloc[-1])

        funding, next_fund, mark = get_funding_rate(symbol)

        nds_d = analyze_nds(df_1d)
        nds_4h = analyze_nds(df_4h)
        nds_15 = analyze_nds(df_15m)
        higher_bias = nds_d['bias'] if nds_d['bias'] != "خنثی" else nds_4h['bias']

        # مونت‌کارلو
        returns = np.diff(np.log(df_1h['close'].values))
        mu, sigma = np.mean(returns), np.std(returns)
        n_sims, n_ahead = 70, 24
        simulations = np.zeros((n_sims, n_ahead))
        for i in range(n_sims):
            simulations[i] = price * np.exp(np.cumsum(np.random.normal(mu, sigma, n_ahead)))
        mean_path = np.mean(simulations, axis=0)

        # سیگنال با اولویت NDS
        if higher_bias == "صعودی" and mean_path[-1] > price and rsi < 67:
            signal = "🟢 LONG"
            entry = price
            sl = price - atr * 1.55
            tp = price + atr * 2.4
            reason = "NDS تایم‌فریم بالاتر صعودی است"
        elif higher_bias == "نزولی" and mean_path[-1] < price and rsi > 33:
            signal = "🔴 SHORT"
            entry = price
            sl = price + atr * 1.55
            tp = price - atr * 2.4
            reason = "NDS تایم‌فریم بالاتر نزولی است"
        else:
            signal = "⚪️ صبر"
            entry = price
            sl = price - atr * 1.25
            tp = price + atr * 1.25
            reason = "عدم تأیید کافی از NDS بالاتر"

        risk_usd = abs(entry - sl) * (margin * lev / entry)

        # ---------- نمودار ----------
        plot_df = df_1h.tail(110).copy()
        fig, ax = plt.subplots(figsize=(12, 6.3))

        ax.plot(plot_df['close'].values, color='#1f77b4', linewidth=1.7, label='قیمت', zorder=5)
        ax.plot(plot_df['close'].rolling(50).mean().values, color='#ff7f0e', linewidth=1.3, label='SMA50')

        ax.plot(plot_df['tenkan'].values, color='#2ca02c', linewidth=1.0, label='Tenkan', alpha=0.9)
        ax.plot(plot_df['kijun'].values, color='#d62728', linewidth=1.0, label='Kijun', alpha=0.9)
        ax.fill_between(range(len(plot_df)), plot_df['senkou_a'].values, plot_df['senkou_b'].values,
                        where=plot_df['senkou_a'] >= plot_df['senkou_b'], color='#90ee90', alpha=0.22)
        ax.fill_between(range(len(plot_df)), plot_df['senkou_a'].values, plot_df['senkou_b'].values,
                        where=plot_df['senkou_a'] < plot_df['senkou_b'], color='#ffb6c1', alpha=0.22)

        fibs = calculate_fibonacci(df_1h, 90)
        fib_colors = ['#000000', '#8B0000', '#FF4500', '#FFD700', '#32CD32', '#1E90FF', '#4B0082']
        for idx, (k, v) in enumerate(fibs.items()):
            ax.axhline(v, color=fib_colors[idx], linestyle='--', linewidth=0.85, alpha=0.75)
            ax.text(len(plot_df) + 1, v, f'{k}', fontsize=7.5, va='center', color=fib_colors[idx])

        last_idx = len(plot_df) - 1
        for sim in simulations:
            ax.plot(range(last_idx, last_idx + n_ahead), sim, color='gray', alpha=0.055, linewidth=0.8)
        ax.plot(range(last_idx, last_idx + n_ahead), mean_path, color='red', linewidth=2.1, label='پیش‌بینی ۲۴h', zorder=6)

        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")
        ax.set_ylabel("قیمت")
        ax.set_title(f"Kronos | {symbol} | NDS: {higher_bias}", fontsize=12)
        ax.legend(loc='upper left', fontsize=8, framealpha=0.92)
        ax.grid(True, alpha=0.3)
        ax.set_xlim(0, len(plot_df) + n_ahead + 3)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=130)
        buf.seek(0)
        plt.close('all')

        # ساخت متن فاندینگ
        base_symbol = symbol.replace("USDT", "")
        coinglass_url = f"https://www.coinglass.com/currencies/{base_symbol}/funding-rate"

        if funding is not None:
            funding_text = f"`{safe_fmt(funding, '.4f')}%`\nزمان بعدی: `{next_fund}`\nMark: `{safe_fmt(mark, '.2f')}`"
        else:
            funding_text = (
                f"در حال حاضر از سرور در دسترس نیست\n"
                f"👉 [مشاهده فاندینگ ریت در Coinglass]({coinglass_url})"
            )

        # پیام ۱
        cap1 = (
            f"🎯 **{symbol}** | Kronos\n\n"
            f"سیگنال: **{signal}**\n"
            f"ورود: `{entry:.2f}`\n"
            f"حد ضرر: `{sl:.2f}`\n"
            f"حد سود: `{tp:.2f}`\n"
            f"دلیل: {reason}\n\n"
            f"💰 **فاندینگ ریت:**\n{funding_text}"
        )
        await update.message.reply_photo(photo=buf, caption=cap1, parse_mode="Markdown", disable_web_page_preview=False)

        # پیام ۲
        text2 = (
            f"🧬 **تحلیل NDS (اولویت اصلی)**\n\n"
            f"▸ روزانه: {nds_d['cycle']} → **{nds_d['bias']}**\n"
            f"   {nds_d['info']}\n\n"
            f"▸ ۴ ساعته: {nds_4h['cycle']} → **{nds_4h['bias']}**\n"
            f"   {nds_4h['info']}\n\n"
            f"▸ ۱۵ دقیقه: {nds_15['cycle']} → **{nds_15['bias']}**\n"
            f"   {nds_15['info']}\n\n"
            f"بایاس غالب: **{higher_bias}**\n\n"
            f"📌 **مدیریت ریسک XT.com**\n"
            f"مارجین: `${margin}` | اهرم: `{lev}x Isolated`\n"
            f"حجم پوزیشن: `${margin * lev:.1f}`\n"
            f"ریسک این ستاپ: `${risk_usd:.2f}`\n"
            f"RSI: `{rsi:.1f}` | ATR: `{atr:.2f}`"
        )
        await update.message.reply_text(text2, parse_mode="Markdown")

        # پیام ۳
        await update.message.reply_text(
            "🛡 **جمع‌بندی وکیل مدافع**\n"
            "اولویت تصمیم‌گیری با ساختار NDS تایم‌فریم بالاتر است.\n"
            "در صورت عدم هم‌راستایی، صبر توصیه می‌شود.\n\n"
            "منابع: Coinglass • Arkham • Lookonchain • TradingView",
            parse_mode="Markdown"
        )

        await status.delete()

    except Exception as e:
        await status.edit_text(f"❌ خطا:\n`{str(e)}`")

def main():
    if not BOT_TOKEN:
        print("BOT_TOKEN تنظیم نشده!")
        return
    t = threading.Thread(target=run_web, daemon=True)
    t.start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tools", tools_command))
    app.add_handler(CommandHandler("predict", predict_command))
    print("Kronos Professional آماده است...")
    app.run_polling()

if __name__ == '__main__':
    main()