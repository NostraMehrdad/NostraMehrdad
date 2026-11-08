import os
import json
import threading
import io
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, FormatStrFormatter

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
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

USDT_WALLET = "TTrC39T9eHNwpGmUu5FKzMWyEHELAmFV5S"
SUBSCRIPTION_PRICE = 10
SUBSCRIPTION_DAYS = 30
USERS_FILE = "users.json"
JOURNAL_FILE = "journal.json"

DEFAULT_MARGIN = 5.0
DEFAULT_LEVERAGE = 8
RATE_LIMIT_SECONDS = 30

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

user_last_request = defaultdict(float)

def load_json(file):
    try:
        if os.path.exists(file):
            with open(file, "r") as f:
                return json.load(f)
    except:
        pass
    return {}

def save_json(file, data):
    try:
        with open(file, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print("Save error:", e)

def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_subscribed(user_id):
    users = load_json(USERS_FILE)
    user = users.get(str(user_id))
    if not user:
        return False
    return datetime.now(timezone.utc) < datetime.fromisoformat(user["expiry"])

def get_remaining_days(user_id):
    users = load_json(USERS_FILE)
    user = users.get(str(user_id))
    if not user:
        return 0
    delta = datetime.fromisoformat(user["expiry"]) - datetime.now(timezone.utc)
    return max(0, delta.days)

def check_rate_limit(user_id):
    now = time.time()
    if now - user_last_request.get(user_id, 0) < RATE_LIMIT_SECONDS:
        return False
    user_last_request[user_id] = now
    return True

def is_private(update):
    return update.effective_chat.type == "private"

# ===================== توابع تحلیلی =====================

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
        pass
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
    high = recent['high'].max()
    low = recent['low'].min()
    diff = high - low
    return {
        '0.0': high, '0.236': high - diff*0.236, '0.382': high - diff*0.382,
        '0.5': high - diff*0.5, '0.618': high - diff*0.618,
        '0.786': high - diff*0.786, '1.0': low
    }

def find_nodes(df, atr_mult=1.1, lookback=4):
    if df is None or len(df) < 40:
        return []
    atr = calculate_atr(df).bfill()
    nodes = []
    highs, lows = df['high'].values, df['low'].values
    for i in range(lookback, len(df)-lookback):
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
    return {'bias': bias, 'cycle': cycle, 'info': f"{last_h:.2f} / {last_l:.2f}"}

def safe_fmt(v, fmt=".4f"):
    if v is None: return None
    try: return format(v, fmt)
    except: return str(v)

# ===================== دستورات =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    text = (
        "🤖 **Kronos | دستیار جامع ترید**\n\n"
        "نقش‌ها: مدیر ریسک + وکیل مدافع + مربی ژورنال\n\n"
        "`/predict BTCUSDT`\n"
        "`/buy`  `/status`\n"
        "`/log`  `/performance`\n"
        "`/tools`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    text = (
        f"💳 **اشتراک ۳۰ روزه**\n\n"
        f"مبلغ: **{SUBSCRIPTION_PRICE} USDT** (TRC20)\n\n"
        f"آدرس:\n`{USDT_WALLET}`\n\n"
        f"پس از واریز، رسید را ارسال کنید."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if is_subscribed(uid):
        await update.message.reply_text(f"✅ فعال — {get_remaining_days(uid)} روز باقی‌مانده", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ اشتراک فعال نیست.")

async def tools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    await update.message.reply_text(
        "منابع:\nCoinglass • Arkham • Lookonchain • DexScreener • Birdeye • DefiLlama • CryptoQuant"
    )

async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or not is_admin(update.effective_user.id): return
    if not context.args: return
    try:
        uid = str(int(context.args[0]))
        days = int(context.args[1]) if len(context.args) > 1 else SUBSCRIPTION_DAYS
        users = load_json(USERS_FILE)
        users[uid] = {"expiry": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()}
        save_json(USERS_FILE, users)
        await update.message.reply_text(f"✅ کاربر {uid} فعال شد ({days} روز)")
    except Exception as e:
        await update.message.reply_text(str(e))

async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or not is_admin(update.effective_user.id): return
    if not context.args: return
    uid = str(context.args[0])
    users = load_json(USERS_FILE)
    if uid in users:
        del users[uid]
        save_json(USERS_FILE, users)
        await update.message.reply_text("حذف شد.")

async def listusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or not is_admin(update.effective_user.id): return
    users = load_json(USERS_FILE)
    if not users:
        await update.message.reply_text("خالی")
        return
    lines = [f"`{u}` → {get_remaining_days(int(u))} روز" for u in users]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ----- ژورنال -----
async def log_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    # فرمت: /log BTCUSDT LONG 75700 75300 +45
    if len(context.args) < 4:
        await update.message.reply_text("فرمت:\n`/log نماد جهت ورود خروج سود/ضرر`\nمثال:\n`/log BTCUSDT LONG 75700 76100 +35`", parse_mode="Markdown")
        return
    try:
        symbol = context.args[0].upper()
        direction = context.args[1].upper()
        entry = float(context.args[2])
        exit_p = float(context.args[3])
        pnl = float(context.args[4]) if len(context.args) > 4 else 0
        journal = load_json(JOURNAL_FILE)
        user_trades = journal.get(str(uid), [])
        user_trades.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "exit": exit_p,
            "pnl": pnl
        })
        journal[str(uid)] = user_trades
        save_json(JOURNAL_FILE, journal)
        await update.message.reply_text("✅ معامله ثبت شد.")
    except:
        await update.message.reply_text("فرمت اشتباه است.")

async def performance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    journal = load_json(JOURNAL_FILE)
    trades = journal.get(str(uid), [])
    if not trades:
        await update.message.reply_text("هنوز معامله‌ای ثبت نکرده‌اید.")
        return
    total = len(trades)
    wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    winrate = (wins / total * 100) if total > 0 else 0
    text = (
        f"📊 **عملکرد شما**\n\n"
        f"تعداد معاملات: `{total}`\n"
        f"تعداد سودده: `{wins}`\n"
        f"وین‌ریت: `{winrate:.1f}%`\n"
        f"مجموع PnL: `{total_pnl:.2f}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# ----- تحلیل اصلی -----
async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not check_rate_limit(uid):
        await update.message.reply_text("⏳ کمی صبر کنید.")
        return
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return

    args = context.args
    symbol = args[0].upper() if args else "BTCUSDT"
    try:
        margin = float(args[1]) if len(args) > 1 else DEFAULT_MARGIN
        lev = float(args[2]) if len(args) > 2 else DEFAULT_LEVERAGE
    except:
        margin, lev = DEFAULT_MARGIN, DEFAULT_LEVERAGE

    status = await update.message.reply_text(f"⏳ تحلیل `{symbol}` ...")

    try:
        df_1h = fetch_klines(symbol, "1h", 250)
        df_15m = fetch_klines(symbol, "15m", 180)
        df_4h = fetch_klines(symbol, "4h", 150)
        df_1d = fetch_klines(symbol, "1d", 120)
        if df_1h is None or len(df_1h) < 60:
            await status.edit_text("❌ داده دریافت نشد.")
            return

        df_1h = add_ichimoku(df_1h)
        price = float(df_1h['close'].iloc[-1])
        rsi = float(safe_rsi(df_1h['close']).iloc[-1])
        atr = float(calculate_atr(df_1h).iloc[-1])
        sma50 = float(df_1h['close'].rolling(50).mean().iloc[-1])

        funding, next_f, mark = get_funding_rate(symbol)
        nds_d = analyze_nds(df_1d)
        nds_4h = analyze_nds(df_4h)
        nds_15 = analyze_nds(df_15m)
        higher_bias = nds_d['bias'] if nds_d['bias'] != "خنثی" else nds_4h['bias']

        returns = np.diff(np.log(df_1h['close'].values))
        mu, sigma = np.mean(returns), np.std(returns)
        sims = np.array([price * np.exp(np.cumsum(np.random.normal(mu, sigma, 24))) for _ in range(70)])
        mean_path = np.mean(sims, axis=0)

        # سیگنال
        if higher_bias == "صعودی" and mean_path[-1] > price and rsi < 65:
            signal, entry, sl, tp = "🟢 LONG", price, price - atr*1.55, price + atr*2.4
            reason = "NDS بالاتر صعودی + هم‌راستایی"
        elif higher_bias == "نزولی" and mean_path[-1] < price and rsi > 35:
            signal, entry, sl, tp = "🔴 SHORT", price, price + atr*1.55, price - atr*2.4
            reason = "NDS بالاتر نزولی + هم‌راستایی"
        else:
            signal, entry, sl, tp = "⚪️ صبر", price, price - atr*1.25, price + atr*1.25
            reason = "عدم تأیید کافی NDS بالاتر"

        risk_usd = abs(entry - sl) * (margin * lev / entry)

        # نمودار
        plot_df = df_1h.tail(100).copy()
        fig, ax = plt.subplots(figsize=(12, 6.5))
        ax.plot(plot_df['close'].values, color='#1f77b4', lw=1.7, label='قیمت')
        ax.plot(plot_df['close'].rolling(50).mean().values, color='#ff7f0e', lw=1.3, label='SMA50')
        ax.plot(plot_df['tenkan'].values, color='#2ca02c', lw=1.0, label='Tenkan', alpha=0.9)
        ax.plot(plot_df['kijun'].values, color='#d62728', lw=1.0, label='Kijun', alpha=0.9)
        fibs = calculate_fibonacci(df_1h)
        for k, v in fibs.items():
            ax.axhline(v, color='gray', ls='--', lw=0.8, alpha=0.7)
            ax.text(len(plot_df)+1, v, f'{k} {v:.1f}', fontsize=7, color='gray')
        last = len(plot_df)-1
        for s in sims[::3]:
            ax.plot(range(last, last+24), s, color='gray', alpha=0.12)
        ax.plot(range(last, last+24), mean_path, color='red', lw=2, label='پیش‌بینی')
        ax.yaxis.tick_right()
        ax.set_title(f"Kronos | {symbol} | {higher_bias}")
        ax.legend(loc='upper left', fontsize=8)
        ax.grid(True, alpha=0.3)
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=130)
        buf.seek(0)
        plt.close()

        # پیام ۱
        fund_text = f"`{safe_fmt(funding, '.4f')}%`" if funding else "نامشخص (Coinglass)"
        cap = (
            f"🎯 **{symbol}**\n\n"
            f"سیگنال: **{signal}**\n"
            f"ورود: `{entry:.2f}`\n"
            f"حد ضرر: `{sl:.2f}`\n"
            f"حد سود: `{tp:.2f}`\n"
            f"دلیل: {reason}\n\n"
            f"💰 فاندینگ: {fund_text}"
        )
        await update.message.reply_photo(photo=buf, caption=cap, parse_mode="Markdown")

        # پیام ۲ - مدیر ریسک + وکیل مدافع
        text2 = (
            f"📌 **مدیر ریسک (XT.com)**\n"
            f"مارجین: `${margin}`\n"
            f"اهرم: `{lev}x Isolated`\n"
            f"حجم پوزیشن: `${margin*lev:.1f}`\n"
            f"ریسک این ستاپ: `${risk_usd:.2f}`\n\n"
            f"🧬 **وکیل مدافع ترید**\n"
            f"بایاس NDS غالب: **{higher_bias}**\n"
            f"• روزانه: {nds_d['cycle']} → {nds_d['bias']}\n"
            f"• ۴ ساعته: {nds_4h['cycle']} → {nds_4h['bias']}\n"
            f"• ۱۵ دقیقه: {nds_15['cycle']} → {nds_15['bias']}\n\n"
            f"**سناریوها:**\n"
            f"• صعودی: در صورت شکست مقاومت و حفظ ساختار HH+HL\n"
            f"• نزولی: در صورت ادامه LH+LL و از دست رفتن حمایت‌ها\n"
            f"• رنج: نوسان بین سطوح کلیدی تا زمان شکست\n\n"
            f"RSI: `{rsi:.1f}` | ATR: `{atr:.2f}` | SMA50: `{sma50:.2f}`"
        )
        await update.message.reply_text(text2, parse_mode="Markdown")

        await update.message.reply_text(
            "🛡 اولویت همیشه با ساختار NDS تایم‌فریم بالاتر است.\nدر صورت تناقض، صبر ترجیح داده می‌شود.",
            parse_mode="Markdown"
        )
        await status.delete()

    except Exception as e:
        await status.edit_text("❌ خطا در تحلیل.")

def main():
    if not BOT_TOKEN:
        print("BOT_TOKEN missing")
        return
    t = threading.Thread(target=run_web, daemon=True)
    t.start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("tools", tools))
    app.add_handler(CommandHandler("predict", predict_command))
    app.add_handler(CommandHandler("log", log_trade))
    app.add_handler(CommandHandler("performance", performance))
    app.add_handler(CommandHandler("adduser", adduser))
    app.add_handler(CommandHandler("removeuser", removeuser))
    app.add_handler(CommandHandler("listusers", listusers))
    print("Kronos آماده است...")
    app.run_polling()

if __name__ == '__main__':
    main()