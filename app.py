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
    return "Kronos Professional is active"

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
RISK_PER_TRADE = 0.8
RATE_LIMIT_SECONDS = 40

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

user_last_request = defaultdict(float)

def load_json(path):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except:
        pass
    return {}

def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print("Save error:", e)

def is_admin(uid): return uid in ADMIN_IDS

def is_subscribed(uid):
    users = load_json(USERS_FILE)
    u = users.get(str(uid))
    if not u: return False
    return datetime.now(timezone.utc) < datetime.fromisoformat(u["expiry"])

def remaining_days(uid):
    users = load_json(USERS_FILE)
    u = users.get(str(uid))
    if not u: return 0
    return max(0, (datetime.fromisoformat(u["expiry"]) - datetime.now(timezone.utc)).days)

def check_rate(uid):
    now = time.time()
    if now - user_last_request[uid] < RATE_LIMIT_SECONDS:
        return False
    user_last_request[uid] = now
    return True

def is_private(update): return update.effective_chat.type == "private"

def fetch_klines(symbol, interval, limit=300):
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=14)
        if r.status_code == 200:
            df = pd.DataFrame(r.json(), columns=[
                'open_time','open','high','low','close','volume',
                'close_time','qav','num_trades','taker_base','taker_quote','ignore'
            ])
            for c in ['open','high','low','close','volume']:
                df[c] = df[c].astype(float)
            return df
    except Exception as e:
        print("Fetch error:", e)
    return None

def get_funding_rate(symbol):
    try:
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            return float(r.json().get('lastFundingRate', 0)) * 100
    except:
        pass
    return None

def safe_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)

def calc_atr(df, period=14):
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low'] - df['close'].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def add_ichimoku(df):
    df['tenkan'] = (df['high'].rolling(9).max() + df['low'].rolling(9).min()) / 2
    df['kijun'] = (df['high'].rolling(26).max() + df['low'].rolling(26).min()) / 2
    df['senkou_a'] = ((df['tenkan'] + df['kijun']) / 2).shift(26)
    df['senkou_b'] = ((df['high'].rolling(52).max() + df['low'].rolling(52).min()) / 2).shift(26)
    return df

def calc_fib(df, lookback=100):
    h = df['high'].tail(lookback).max()
    l = df['low'].tail(lookback).min()
    d = h - l
    return {
        '0.0': h, '0.236': h - d*0.236, '0.382': h - d*0.382,
        '0.5': h - d*0.5, '0.618': h - d*0.618, '0.786': h - d*0.786, '1.0': l
    }

def find_nodes(df, mult=1.15, lb=4):
    if df is None or len(df) < 50:
        return []
    atr = calc_atr(df).bfill()
    nodes = []
    hs, ls = df['high'].values, df['low'].values
    for i in range(lb, len(df)-lb):
        if hs[i] == max(hs[i-lb:i+lb+1]) and (hs[i] - min(ls[i-lb:i])) > atr.iloc[i]*mult:
            nodes.append({'type': 'high', 'price': float(hs[i])})
        if ls[i] == min(ls[i-lb:i+lb+1]) and (max(hs[i-lb:i]) - ls[i]) > atr.iloc[i]*mult:
            nodes.append({'type': 'low', 'price': float(ls[i])})
    return nodes

def analyze_nds(df):
    if df is None or len(df) < 60:
        return {'bias': 'خنثی', 'cycle': '—'}
    nodes = find_nodes(df)
    price = float(df['close'].iloc[-1])
    if len(nodes) < 4:
        return {'bias': 'خنثی', 'cycle': 'ناقص'}
    highs = [n['price'] for n in nodes if n['type'] == 'high']
    lows = [n['price'] for n in nodes if n['type'] == 'low']
    lh = highs[-1] if highs else price
    ph = highs[-2] if len(highs) > 1 else lh
    ll = lows[-1] if lows else price
    pl = lows[-2] if len(lows) > 1 else ll
    if lh > ph and ll > pl:
        return {'bias': 'صعودی', 'cycle': 'HH+HL'}
    elif lh < ph and ll < pl:
        return {'bias': 'نزولی', 'cycle': 'LH+LL'}
    else:
        return {'bias': 'خنثی', 'cycle': 'رنج'}

def calculate_dollar_sl_tp(entry, direction, atr, margin=5.0, leverage=8, risk_dollar=0.8):
    position_size = margin * leverage
    price_risk = (risk_dollar / position_size) * entry
    atr_distance = atr * 1.5
    final_distance = max(price_risk, atr_distance)
    if direction == "LONG":
        sl = entry - final_distance
        tp = entry + final_distance * 2.2
    else:
        sl = entry + final_distance
        tp = entry - final_distance * 2.2
    return round(sl, 2), round(tp, 2)

def create_professional_chart(df, title, fibs=None, sims=None, mean_path=None, 
                               entry=None, sl=None, tp=None, show_prev_day=False):
    """ساخت نمودار حرفه‌ای"""
    plot_df = df.tail(120).copy() if len(df) > 120 else df.copy()
    
    fig, ax = plt.subplots(figsize=(13, 7.2))
    
    # قیمت
    ax.plot(plot_df['close'].values, color='#1f77b4', lw=1.9, label='قیمت', zorder=5)
    
    # میانگین‌ها
    ax.plot(plot_df['close'].rolling(20).mean().values, color='#9467bd', lw=1.2, label='SMA20', alpha=0.9)
    ax.plot(plot_df['close'].rolling(50).mean().values, color='#ff7f0e', lw=1.35, label='SMA50')
    if len(plot_df) >= 200:
        ax.plot(plot_df['close'].rolling(200).mean().values, color='#17becf', lw=1.4, label='SMA200', alpha=0.85)
    
    # ایچیموکو
    if 'tenkan' in plot_df.columns:
        ax.plot(plot_df['tenkan'].values, color='#2ca02c', lw=1.15, label='Tenkan', alpha=0.9)
        ax.plot(plot_df['kijun'].values, color='#d62728', lw=1.15, label='Kijun', alpha=0.9)
        ax.fill_between(range(len(plot_df)), plot_df['senkou_a'].values, plot_df['senkou_b'].values,
                        where=plot_df['senkou_a'] >= plot_df['senkou_b'], color='#90ee90', alpha=0.28)
        ax.fill_between(range(len(plot_df)), plot_df['senkou_a'].values, plot_df['senkou_b'].values,
                        where=plot_df['senkou_a'] < plot_df['senkou_b'], color='#ffb6c1', alpha=0.28)
    
    # فیبوناچی
    if fibs:
        colors = {
            '0.0': '#111111', '0.236': '#8B0000', '0.382': '#FF4500',
            '0.5': '#DAA520', '0.618': '#228B22', '0.786': '#1E90FF', '1.0': '#4B0082'
        }
        for k, v in fibs.items():
            c = colors.get(k, '#555')
            ax.axhline(v, color=c, ls='--', lw=0.95, alpha=0.85)
            ax.text(len(plot_df) + 1.8, v, f' {k}  {v:.1f}', fontsize=8.2, va='center', color=c, fontweight='bold')
    
    # مونت‌کارلو
    if sims is not None and mean_path is not None:
        last = len(plot_df) - 1
        for s in sims[::3]:
            ax.plot(range(last, last + len(mean_path)), s, color='gray', alpha=0.11, lw=0.7)
        ax.plot(range(last, last + len(mean_path)), mean_path, color='#c0392b', lw=2.15, label='پیش‌بینی', zorder=6)
    
    # سطوح سیگنال
    if entry:
        ax.axhline(entry, color='#3498db', ls='-', lw=1.4, alpha=0.9, label=f'Entry {entry:.1f}')
    if sl:
        ax.axhline(sl, color='#e74c3c', ls='-', lw=1.4, alpha=0.9, label=f'SL {sl:.1f}')
    if tp:
        ax.axhline(tp, color='#27ae60', ls='-', lw=1.4, alpha=0.9, label=f'TP {tp:.1f}')
    
    # خط Open و Close روز قبل (برای نمودار روزانه)
    if show_prev_day and len(df) >= 2:
        prev_open = df['open'].iloc[-2]
        prev_close = df['close'].iloc[-2]
        ax.axhline(prev_open, color='#8e44ad', ls=':', lw=1.3, alpha=0.8, label=f'Prev Open {prev_open:.1f}')
        ax.axhline(prev_close, color='#f39c12', ls=':', lw=1.3, alpha=0.8, label=f'Prev Close {prev_close:.1f}')
    
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.yaxis.set_major_locator(MaxNLocator(nbins=14))
    ax.yaxis.set_major_formatter(FormatStrFormatter('%.1f'))
    ax.set_title(title, fontsize=13, pad=11)
    ax.legend(loc='upper left', fontsize=8, framealpha=0.93)
    ax.grid(True, alpha=0.28)
    ax.set_xlim(0, len(plot_df) + 28)
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=145)
    buf.seek(0)
    plt.close()
    return buf

# ===================== دستورات =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    await update.message.reply_text(
        "🤖 **Kronos | دستیار جامع و حرفه‌ای ترید**\n\n"
        "`/predict`  `/news`\n"
        "`/buy`  `/status`\n"
        "`/log`  `/performance`\n"
        "`/tools`",
        parse_mode="Markdown"
    )

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    await update.message.reply_text(
        f"💳 **اشتراک ۳۰ روزه**\n\n"
        f"مبلغ: **{SUBSCRIPTION_PRICE} USDT** (TRC20)\n\n"
        f"آدرس:\n`{USDT_WALLET}`",
        parse_mode="Markdown"
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if is_subscribed(uid):
        await update.message.reply_text(f"✅ فعال — **{remaining_days(uid)} روز**", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ اشتراک فعال نیست.")

async def tools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    text = (
        "🛠 **منابع معتبر**\n\n"
        "• [Coinglass](https://www.coinglass.com)\n"
        "• [Arkham](https://www.arkham.com)\n"
        "• [Lookonchain](https://www.lookonchain.com)\n"
        "• [DexScreener](https://dexscreener.com)\n"
        "• [Birdeye](https://birdeye.so)\n"
        "• [DefiLlama](https://defillama.com)\n"
        "• [CryptoQuant](https://cryptoquant.com)\n"
        "• [GMGN](https://gmgn.ai)"
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)

async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    await update.message.reply_text("📰 دریافت خودکار اخبار فعلاً محدود است.\nپیشنهاد: Coinglass + CryptoPanic")

async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or not is_admin(update.effective_user.id): return
    if not context.args: return
    try:
        uid = str(int(context.args[0]))
        days = int(context.args[1]) if len(context.args)>1 else SUBSCRIPTION_DAYS
        users = load_json(USERS_FILE)
        users[uid] = {"expiry": (datetime.now(timezone.utc)+timedelta(days=days)).isoformat()}
        save_json(USERS_FILE, users)
        await update.message.reply_text(f"✅ {uid} برای {days} روز فعال شد.")
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
    lines = [f"`{u}` → {remaining_days(int(u))} روز" for u in users]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def log_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if len(context.args) < 4:
        await update.message.reply_text("`/log نماد جهت ورود خروج [pnl]`")
        return
    try:
        symbol = context.args[0].upper()
        direction = context.args[1].upper()
        entry = float(context.args[2])
        exit_p = float(context.args[3])
        pnl = float(context.args[4]) if len(context.args)>4 else 0.0
        journal = load_json(JOURNAL_FILE)
        trades = journal.get(str(uid), [])
        trades.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "exit": exit_p,
            "pnl": pnl
        })
        journal[str(uid)] = trades
        save_json(JOURNAL_FILE, journal)
        await update.message.reply_text("✅ ثبت شد.")
    except:
        await update.message.reply_text("فرمت اشتباه.")

async def performance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    trades = load_json(JOURNAL_FILE).get(str(uid), [])
    if not trades:
        await update.message.reply_text("معامله‌ای ثبت نشده.")
        return
    total = len(trades)
    pnls = [t.get("pnl", 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    winrate = (len(wins)/total*100) if total else 0
    total_pnl = sum(pnls)
    await update.message.reply_text(
        f"📊 **عملکرد**\nتعداد: `{total}`\nوین‌ریت: `{winrate:.1f}%`\nمجموع PnL: `{total_pnl:.2f}`",
        parse_mode="Markdown"
    )

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not check_rate(uid):
        await update.message.reply_text("⏳ کمی صبر کنید.")
        return
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return

    args = context.args
    symbol = args[0].upper() if args else "BTCUSDT"
    try:
        margin = float(args[1]) if len(args)>1 else DEFAULT_MARGIN
        lev = float(args[2]) if len(args)>2 else DEFAULT_LEVERAGE
    except:
        margin, lev = DEFAULT_MARGIN, DEFAULT_LEVERAGE

    status = await update.message.reply_text(f"⏳ تحلیل حرفه‌ای `{symbol}` (۴H + Daily) ...")

    try:
        # داده‌ها
        df_4h = fetch_klines(symbol, "4h", 300)
        df_1d = fetch_klines(symbol, "1d", 200)
        df_15m = fetch_klines(symbol, "15m", 150)

        if df_4h is None or len(df_4h) < 80:
            await status.edit_text("❌ خطا در دریافت داده.")
            return

        df_4h = add_ichimoku(df_4h)
        df_1d = add_ichimoku(df_1d)

        price = float(df_4h['close'].iloc[-1])
        rsi = float(safe_rsi(df_4h['close']).iloc[-1])
        atr = float(calc_atr(df_4h).iloc[-1])
        sma20 = float(df_4h['close'].rolling(20).mean().iloc[-1])
        sma50 = float(df_4h['close'].rolling(50).mean().iloc[-1])
        sma200 = float(df_4h['close'].rolling(200).mean().iloc[-1]) if len(df_4h) >= 200 else sma50

        funding = get_funding_rate(symbol)
        nds_d = analyze_nds(df_1d)
        nds_4h = analyze_nds(df_4h)
        nds_15 = analyze_nds(df_15m)
        higher = nds_d['bias'] if nds_d['bias'] != "خنثی" else nds_4h['bias']

        # مونت‌کارلو روی ۴ ساعته
        rets = np.diff(np.log(df_4h['close'].values[-100:]))
        mu, sigma = np.mean(rets), np.std(rets)
        sims = np.array([price * np.exp(np.cumsum(np.random.normal(mu, sigma, 18))) for _ in range(55)])
        mean_path = np.mean(sims, axis=0)

        # تصمیم
        if higher == "صعودی" and mean_path[-1] > price and rsi < 67:
            direction = "LONG"
            signal = "🟢 LONG"
            reason = "ساختار NDS تایم‌فریم بالاتر صعودی است و با مونت‌کارلو و RSI هم‌راستا شده."
        elif higher == "نزولی" and mean_path[-1] < price and rsi > 33:
            direction = "SHORT"
            signal = "🔴 SHORT"
            reason = "ساختار NDS تایم‌فریم بالاتر نزولی است و با مونت‌کارلو و RSI هم‌راستا شده."
        else:
            direction = None
            signal = "⚪️ صبر"
            reason = "عدم تأیید کافی از ساختار NDS تایم‌فریم‌های بالاتر."

        if direction:
            sl, tp = calculate_dollar_sl_tp(price, direction, atr, margin, lev, RISK_PER_TRADE)
            entry = price
        else:
            entry = price
            sl = price - atr * 1.35
            tp = price + atr * 1.35

        position_size = margin * lev
        risk_usd = abs(entry - sl) / entry * position_size
        reward_usd = abs(tp - entry) / entry * position_size
        rr = reward_usd / risk_usd if risk_usd > 0 else 0

        fibs_4h = calc_fib(df_4h)
        fibs_1d = calc_fib(df_1d)

        nearest_res = min([v for v in fibs_4h.values() if v > price], default=None)
        nearest_sup = max([v for v in fibs_4h.values() if v < price], default=None)

        # ===== نمودار ۱: ۴ ساعته =====
        buf1 = create_professional_chart(
            df_4h, 
            title=f"Kronos | {symbol} | 4H | NDS: {higher}",
            fibs=fibs_4h,
            sims=sims,
            mean_path=mean_path,
            entry=entry if direction else None,
            sl=sl if direction else None,
            tp=tp if direction else None
        )

        # ===== نمودار ۲: روزانه =====
        buf2 = create_professional_chart(
            df_1d,
            title=f"Kronos | {symbol} | Daily | Signal Levels",
            fibs=fibs_1d,
            entry=entry,
            sl=sl,
            tp=tp,
            show_prev_day=True
        )

        base = symbol.replace("USDT", "")
        glass = f"https://www.coinglass.com/currencies/{base}/funding-rate"
        fund_text = f"`{funding:.4f}%`" if funding is not None else f"[Coinglass]({glass})"

        # پیام ۱ - پلن + نمودار ۴ ساعته
        plan = (
            f"📋 **پلن معاملاتی | {symbol}**\n\n"
            f"سیگنال: **{signal}**\n"
            f"ورود: `{entry:.2f}`\n"
            f"حد ضرر: `{sl:.2f}`\n"
            f"حد سود: `{tp:.2f}`\n\n"
            f"💵 ریسک دلاری: `${risk_usd:.2f}`\n"
            f"💵 سود احتمالی: `${reward_usd:.2f}`\n"
            f"نسبت R:R ← `1 : {rr:.2f}`\n\n"
        )
        if nearest_res:
            plan += f"مقاومت نزدیک: `{nearest_res:.2f}`\n"
        if nearest_sup:
            plan += f"حمایت نزدیک: `{nearest_sup:.2f}`\n"
        plan += f"\nدلیل: {reason}\n\n💰 فاندینگ: {fund_text}"

        await update.message.reply_photo(photo=buf1, caption=plan, parse_mode="Markdown")

        # پیام ۲ - نمودار روزانه
        await update.message.reply_photo(
            photo=buf2, 
            caption=f"📅 **نمودار روزانه {symbol}**\nسطوح سیگنال + Open/Close روز قبل مشخص شده‌اند.",
            parse_mode="Markdown"
        )

        # پیام ۳ - جزئیات
        text3 = (
            f"📌 **جزئیات**\n"
            f"مارجین: `${margin}` | اهرم: `{lev}x` | حجم: `${position_size:.1f}`\n\n"
            f"🧬 **NDS**\n"
            f"بایاس غالب: **{higher}**\n"
            f"• روزانه: {nds_d['cycle']} → {nds_d['bias']}\n"
            f"• ۴ ساعته: {nds_4h['cycle']} → {nds_4h['bias']}\n"
            f"• ۱۵ دقیقه: {nds_15['cycle']} → {nds_15['bias']}\n\n"
            f"RSI: `{rsi:.1f}` | ATR: `{atr:.2f}`\n"
            f"SMA20: `{sma20:.1f}` | SMA50: `{sma50:.1f}` | SMA200: `{sma200:.1f}`"
        )
        await update.message.reply_text(text3, parse_mode="Markdown")

        await update.message.reply_text(
            "🛡 اولویت همیشه با ساختار NDS تایم‌فریم بالاتر است.\nدر صورت تناقض، صبر منطقی‌ترین انتخاب است.",
            parse_mode="Markdown"
        )
        await status.delete()

    except Exception as e:
        await status.edit_text(f"❌ خطا در تحلیل: {str(e)[:80]}")

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
    app.add_handler(CommandHandler("news", news_command))
    app.add_handler(CommandHandler("predict", predict_command))
    app.add_handler(CommandHandler("log", log_trade))
    app.add_handler(CommandHandler("performance", performance))
    app.add_handler(CommandHandler("adduser", adduser))
    app.add_handler(CommandHandler("removeuser", removeuser))
    app.add_handler(CommandHandler("listusers", listusers))
    print("Kronos Professional آماده است")
    app.run_polling()

if __name__ == '__main__':
    main()