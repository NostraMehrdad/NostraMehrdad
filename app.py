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
from matplotlib.patches import Circle, Rectangle
from matplotlib.gridspec import GridSpec

from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "𓄂 ηοsτrαμεhrδαδ𓆃 | Kronos Professional is active"

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
POSITION_FILE = "position.json"

DEFAULT_MARGIN = 5.0
DEFAULT_LEVERAGE = 8
RISK_PER_TRADE = 0.5          # ریسک محافظه‌کارانه‌تر (۰.۵ دلار)
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

# ===================== مدیریت پوزیشن =====================

def get_open_position(uid):
    data = load_json(POSITION_FILE)
    return data.get(str(uid))

def set_open_position(uid, symbol, direction, entry, sl, tp):
    data = load_json(POSITION_FILE)
    data[str(uid)] = {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "opened_at": datetime.now(timezone.utc).isoformat()
    }
    save_json(POSITION_FILE, data)

def close_position(uid):
    data = load_json(POSITION_FILE)
    if str(uid) in data:
        del data[str(uid)]
        save_json(POSITION_FILE, data)
        return True
    return False

# ===================== داده و اندیکاتور =====================

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

def get_funding_info(symbol):
    result = {"rate": None, "next_time": None, "mark": None}
    try:
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
        r = requests.get(url, headers=HEADERS, timeout=9)
        if r.status_code == 200:
            d = r.json()
            result["rate"] = float(d.get("lastFundingRate", 0)) * 100
            result["mark"] = float(d.get("markPrice", 0))
            next_ts = d.get("nextFundingTime")
            if next_ts:
                result["next_time"] = datetime.fromtimestamp(int(next_ts)/1000, tz=timezone.utc).strftime("%H:%M UTC")
    except Exception as e:
        print("Funding error:", e)
    return result

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

def add_indicators(df):
    df = df.copy()
    df['sma20'] = df['close'].rolling(20).mean()
    df['sma50'] = df['close'].rolling(50).mean()
    df['sma200'] = df['close'].rolling(200).mean()
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + 2 * df['bb_std']
    df['bb_lower'] = df['bb_mid'] - 2 * df['bb_std']
    
    df['tenkan'] = (df['high'].rolling(9).max() + df['low'].rolling(9).min()) / 2
    df['kijun'] = (df['high'].rolling(26).max() + df['low'].rolling(26).min()) / 2
    df['senkou_a'] = ((df['tenkan'] + df['kijun']) / 2).shift(26)
    df['senkou_b'] = ((df['high'].rolling(52).max() + df['low'].rolling(52).min()) / 2).shift(26)
    
    df['rsi'] = safe_rsi(df['close'])
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
            nodes.append({'type': 'high', 'price': float(hs[i]), 'index': i})
        if ls[i] == min(ls[i-lb:i+lb+1]) and (max(hs[i-lb:i]) - ls[i]) > atr.iloc[i]*mult:
            nodes.append({'type': 'low', 'price': float(ls[i]), 'index': i})
    return nodes

def analyze_nds(df):
    if df is None or len(df) < 60:
        return {'bias': 'خنثی', 'cycle': '—', 'last_high': None, 'last_low': None, 'last_high_idx': None, 'last_low_idx': None, 'nodes': []}
    nodes = find_nodes(df)
    price = float(df['close'].iloc[-1])
    if len(nodes) < 4:
        return {'bias': 'خنثی', 'cycle': 'ناقص', 'last_high': None, 'last_low': None, 'last_high_idx': None, 'last_low_idx': None, 'nodes': nodes}
    
    highs = [n for n in nodes if n['type'] == 'high']
    lows = [n for n in nodes if n['type'] == 'low']
    
    last_high = highs[-1]['price'] if highs else price
    last_high_idx = highs[-1]['index'] if highs else len(df)-1
    prev_high = highs[-2]['price'] if len(highs) > 1 else last_high
    
    last_low = lows[-1]['price'] if lows else price
    last_low_idx = lows[-1]['index'] if lows else len(df)-1
    prev_low = lows[-2]['price'] if len(lows) > 1 else last_low

    if last_high > prev_high and last_low > prev_low:
        bias, cycle = "صعودی", "HH+HL"
    elif last_high < prev_high and last_low < prev_low:
        bias, cycle = "نزولی", "LH+LL"
    else:
        bias, cycle = "خنثی", "رنج"
        
    return {
        'bias': bias, 'cycle': cycle,
        'last_high': last_high, 'last_low': last_low,
        'last_high_idx': last_high_idx, 'last_low_idx': last_low_idx,
        'nodes': nodes
    }

def calculate_dollar_sl_tp(entry, direction, atr, margin=5.0, leverage=8, risk_dollar=0.5):
    """مدیریت سرمایه محافظه‌کارانه"""
    position_size = margin * leverage
    # فاصله بر اساس ریسک دلاری ثابت
    price_risk = (risk_dollar / position_size) * entry
    # فاصله ATR با ضریب کمتر
    atr_distance = atr * 1.25
    # اولویت با ریسک دلاری (سخت‌گیرانه)
    final_distance = min(price_risk * 1.15, atr_distance) if atr_distance > 0 else price_risk
    
    if direction == "LONG":
        sl = entry - final_distance
        tp = entry + final_distance * 2.2
    else:
        sl = entry + final_distance
        tp = entry - final_distance * 2.2
    return round(sl, 2), round(tp, 2)

def create_pro_chart(df, title, fibs=None, entry=None, sl=None, tp=None, 
                     sims=None, mean_path=None, nds_info=None, bias=None, mark_hugs=False):
    plot_df = df.tail(100).copy()
    n = len(plot_df)
    
    fig = plt.figure(figsize=(14, 9.2), facecolor='#0d0d0d')
    gs = GridSpec(4, 1, height_ratios=[3.2, 0.9, 0.9, 0.05], hspace=0.08)
    
    ax = fig.add_subplot(gs[0])
    ax_vol = fig.add_subplot(gs[1], sharex=ax)
    ax_rsi = fig.add_subplot(gs[2], sharex=ax)
    
    for a in [ax, ax_vol, ax_rsi]:
        a.set_facecolor('#0d0d0d')
        a.tick_params(colors='#cccccc', labelsize=8)
        for spine in a.spines.values():
            spine.set_color('#333333')
    
    ax.plot(plot_df['close'].values, color='#00bfff', lw=1.7, label='Price', zorder=5)
    ax.plot(plot_df['sma20'].values, color='#da70d6', lw=1.1, label='MA20', alpha=0.9)
    ax.plot(plot_df['sma50'].values, color='#ffa500', lw=1.2, label='MA50')
    if 'sma200' in plot_df.columns:
        ax.plot(plot_df['sma200'].values, color='#1e90ff', lw=1.25, label='MA200', alpha=0.85)
    ax.plot(plot_df['ema20'].values, color='#00fa9a', lw=1.0, label='EMA20', alpha=0.85)
    ax.plot(plot_df['ema50'].values, color='#ff6347', lw=1.05, label='EMA50', alpha=0.85)
    
    ax.plot(plot_df['bb_upper'].values, color='#888888', lw=0.9, ls='--', alpha=0.7)
    ax.plot(plot_df['bb_lower'].values, color='#888888', lw=0.9, ls='--', alpha=0.7)
    ax.fill_between(range(n), plot_df['bb_upper'].values, plot_df['bb_lower'].values, color='#444444', alpha=0.15)
    
    if 'senkou_a' in plot_df.columns:
        ax.fill_between(range(n), plot_df['senkou_a'].values, plot_df['senkou_b'].values,
                        where=plot_df['senkou_a'] >= plot_df['senkou_b'], color='#00ff7f', alpha=0.12)
        ax.fill_between(range(n), plot_df['senkou_a'].values, plot_df['senkou_b'].values,
                        where=plot_df['senkou_a'] < plot_df['senkou_b'], color='#ff69b4', alpha=0.12)
    
    if fibs:
        colors = {'0.0':'#ffffff', '0.236':'#ff6b6b', '0.382':'#ff9f43', '0.5':'#feca57',
                  '0.618':'#1dd1a1', '0.786':'#54a0ff', '1.0':'#a55eea'}
        for k, v in fibs.items():
            c = colors.get(k, '#aaaaaa')
            ax.axhline(v, color=c, ls=':', lw=0.9, alpha=0.8)
            ax.text(n + 1.2, v, f'{k} {v:.0f}', fontsize=7.5, va='center', color=c)
    
    if entry: ax.axhline(entry, color='#1e90ff', lw=1.4, label='Entry')
    if sl: ax.axhline(sl, color='#ff4757', lw=1.4, label='SL')
    if tp: ax.axhline(tp, color='#2ed573', lw=1.4, label='TP')
    
    if sims is not None and mean_path is not None:
        last = n - 1
        horizon = len(mean_path)
        for s in sims[::3]:
            ax.plot(range(last, last+horizon), s[:horizon], color='#ffffff', alpha=0.18, lw=0.8)
        ax.plot(range(last, last+horizon), mean_path, color='#ffffff', lw=2.0, label='Pred 4-6h', zorder=6)
    
    if nds_info:
        last_high = nds_info.get('last_high')
        last_low = nds_info.get('last_low')
        nodes = nds_info.get('nodes', [])
        offset = len(df) - n
        
        if mark_hugs and nodes:
            recent_nodes = nodes[-6:] if len(nodes) >= 6 else nodes
            hug_count = 0
            flag_count = 0
            for node in recent_nodes:
                x = node['index'] - offset
                if not (0 <= x < n): continue
                y = node['price']
                if node['type'] == 'high':
                    hug_count += 1
                    color = '#ff6b6b'
                    label = f"H{hug_count}"
                else:
                    flag_count += 1
                    color = '#1dd1a1'
                    label = f"F{flag_count}"
                size = n * 0.008
                rect = Rectangle((x - size/2, y - (y*0.004)), size, y*0.008, 
                                 linewidth=1.5, edgecolor=color, facecolor=color, alpha=0.85, zorder=11)
                ax.add_patch(rect)
                ax.text(x, y + (y*0.012), label, fontsize=7.5, ha='center', color=color, fontweight='bold', zorder=12)
        
        if bias == "نزولی" and last_low:
            ym = last_low - (last_high - last_low)*0.28 if last_high else last_low*0.985
            ax.scatter([n-5], [ym], marker='x', s=140, color='#ff4757', linewidths=2.4, zorder=12)
            ax.text(n-5, ym*0.992, f"{ym:.0f}", fontsize=8, ha='center', color='#ff4757', fontweight='bold')
        elif bias == "صعودی" and last_high:
            ym = last_high + (last_high - last_low)*0.28 if last_low else last_high*1.015
            ax.scatter([n-5], [ym], marker='x', s=140, color='#ff4757', linewidths=2.4, zorder=12)
            ax.text(n-5, ym*1.008, f"{ym:.0f}", fontsize=8, ha='center', color='#ff4757', fontweight='bold')
    
    ax.set_title(title, color='white', fontsize=13, pad=10)
    ax.legend(loc='upper left', fontsize=7.5, facecolor='#1a1a1a', edgecolor='#333', labelcolor='white', ncol=3)
    ax.grid(True, alpha=0.2, color='#444')
    ax.yaxis.tick_right()
    ax.yaxis.set_major_formatter(FormatStrFormatter('%.0f'))
    ax.set_xlim(0, n+16)
    
    colors_vol = ['#2ed573' if plot_df['close'].iloc[i] >= plot_df['open'].iloc[i] else '#ff4757' for i in range(n)]
    ax_vol.bar(range(n), plot_df['volume'].values, color=colors_vol, alpha=0.7, width=0.8)
    ax_vol.plot(plot_df['volume'].rolling(5).mean().values, color='#00bfff', lw=1.0, alpha=0.8)
    ax_vol.set_ylabel('Vol', color='#aaaaaa', fontsize=8)
    ax_vol.grid(True, alpha=0.15, color='#444')
    
    ax_rsi.plot(plot_df['rsi'].values, color='#e056fd', lw=1.2)
    ax_rsi.axhline(70, color='#ff4757', ls='--', lw=0.8, alpha=0.6)
    ax_rsi.axhline(30, color='#2ed573', ls='--', lw=0.8, alpha=0.6)
    ax_rsi.axhline(50, color='#888888', ls=':', lw=0.7, alpha=0.5)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel('RSI', color='#aaaaaa', fontsize=8)
    ax_rsi.grid(True, alpha=0.15, color='#444')
    
    plt.setp(ax.get_xticklabels(), visible=False)
    plt.setp(ax_vol.get_xticklabels(), visible=False)
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=155, facecolor='#0d0d0d')
    buf.seek(0)
    plt.close()
    return buf

# ===================== دستورات =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    await update.message.reply_text(
        "🤖 **𓄂 ηοsτrαμεhrδαδ𓆃 | Kronos**\n\n"
        "`/predict`  `/nds`\n"
        "`/position`  `/close`\n"
        "`/buy`  `/status`\n"
        "`/log`  `/performance`\n"
        "`/tools`",
        parse_mode="Markdown"
    )

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    await update.message.reply_text(
        f"💳 **اشتراک ۳۰ روزه**\n\nمبلغ: **{SUBSCRIPTION_PRICE} USDT** (TRC20)\n\nآدرس:\n`{USDT_WALLET}`",
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
        "🛠 **منابع**\n\n"
        "• [Coinglass](https://www.coinglass.com)\n"
        "• [Arkham](https://www.arkham.com)\n"
        "• [Lookonchain](https://www.lookonchain.com)\n"
        "• [DexScreener](https://dexscreener.com)\n"
        "• [Birdeye](https://birdeye.so)\n"
        "• [DefiLlama](https://defillama.com)\n"
        "• [CryptoQuant](https://cryptoquant.com)"
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)

async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    await update.message.reply_text("📰 دریافت خودکار اخبار فعلاً محدود است.")

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
            "symbol": symbol, "direction": direction,
            "entry": entry, "exit": exit_p, "pnl": pnl
        })
        journal[str(uid)] = trades
        save_json(JOURNAL_FILE, journal)
        # اگر پوزیشن باز بود، آن را هم ببند
        close_position(uid)
        await update.message.reply_text("✅ ثبت شد و پوزیشن بسته شد.")
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

async def position_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    pos = get_open_position(uid)
    if not pos:
        await update.message.reply_text("📭 هیچ پوزیشن بازی وجود ندارد.")
        return
    text = (
        f"📌 **پوزیشن باز فعلی**\n\n"
        f"نماد: `{pos['symbol']}`\n"
        f"جهت: **{pos['direction']}**\n"
        f"ورود: `{pos['entry']}`\n"
        f"حد ضرر: `{pos['sl']}`\n"
        f"حد سود: `{pos['tp']}`\n"
        f"زمان باز شدن: `{pos['opened_at'][:16]}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def close_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if close_position(uid):
        await update.message.reply_text("✅ پوزیشن با موفقیت بسته شد.\nحالا می‌توانید سیگنال جدید بگیرید.")
    else:
        await update.message.reply_text("📭 پوزیشن بازی برای بستن وجود ندارد.")

async def nds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    status = await update.message.reply_text(f"⏳ تحلیل ساختار NDS `{symbol}` ...")

    try:
        df_4h = fetch_klines(symbol, "4h", 300)
        df_1d = fetch_klines(symbol, "1d", 200)
        df_15m = fetch_klines(symbol, "15m", 150)

        if df_4h is None or len(df_4h) < 80:
            await status.edit_text("❌ خطا در دریافت داده.")
            return

        df_4h = add_indicators(df_4h)
        price = float(df_4h['close'].iloc[-1])
        atr = float(calc_atr(df_4h).iloc[-1])
        rsi = float(df_4h['rsi'].iloc[-1])

        nds_d = analyze_nds(df_1d)
        nds_4h = analyze_nds(df_4h)
        nds_15 = analyze_nds(df_15m)
        higher = nds_d['bias'] if nds_d['bias'] != "خنثی" else nds_4h['bias']

        fibs = calc_fib(df_4h)
        last_high = nds_4h.get('last_high')
        last_low = nds_4h.get('last_low')

        if higher == "صعودی":
            cont_text = f"ساختار **صعودی (HH+HL)** است.\nناحیه ادامه حرکت بالای حمایت مهم (`{last_low:.1f}`) قرار دارد."
        elif higher == "نزولی":
            cont_text = f"ساختار **نزولی (LH+LL)** است.\nناحیه ادامه حرکت زیر مقاومت مهم (`{last_high:.1f}`) قرار دارد."
        else:
            cont_text = "ساختار رنج یا انتقالی است."

        buf = create_pro_chart(df_4h, f"𓄂 ηοsτrαμεhrδαδ𓆃 | {symbol} | 4H | {higher}",
                               fibs=fibs, nds_info=nds_4h, bias=higher, mark_hugs=True)

        text = (
            f"🧬 **تحلیل ساختار NDS | {symbol}**\n\n"
            f"**بایاس غالب:** {higher}\n\n"
            f"• روزانه: {nds_d['cycle']} → **{nds_d['bias']}**\n"
            f"• ۴ ساعته: {nds_4h['cycle']} → **{nds_4h['bias']}**\n"
            f"• ۱۵ دقیقه: {nds_15['cycle']} → **{nds_15['bias']}**\n\n"
            f"**حمایت و مقاومت**\n"
        )
        if last_high: text += f"• مقاومت مهم: `{last_high:.1f}`\n"
        if last_low: text += f"• حمایت مهم: `{last_low:.1f}`\n"
        text += f"\n**نواحی ادامه حرکت**\n{cont_text}\n\n"
        text += f"RSI: `{rsi:.1f}` | ATR: `{atr:.2f}` | قیمت: `{price:.2f}`"

        await update.message.reply_photo(photo=buf, caption=text, parse_mode="Markdown")
        await status.delete()

    except Exception as e:
        await status.edit_text(f"❌ خطا: {str(e)[:90]}")

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not check_rate(uid):
        await update.message.reply_text("⏳ کمی صبر کنید.")
        return
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return

    # بررسی محدودیت یک پوزیشن
    open_pos = get_open_position(uid)
    if open_pos:
        await update.message.reply_text(
            f"⚠️ **محدودیت مدیریت سرمایه**\n\n"
            f"شما یک پوزیشن باز دارید:\n"
            f"`{open_pos['symbol']}` | {open_pos['direction']}\n"
            f"ورود: `{open_pos['entry']}`\n\n"
            f"تا زمانی که این پوزیشن را با دستور `/close` نبندید، سیگنال جدید صادر نمی‌شود.",
            parse_mode="Markdown"
        )
        return

    args = context.args
    symbol = args[0].upper() if args else "BTCUSDT"
    try:
        margin = float(args[1]) if len(args)>1 else DEFAULT_MARGIN
        lev = float(args[2]) if len(args)>2 else DEFAULT_LEVERAGE
    except:
        margin, lev = DEFAULT_MARGIN, DEFAULT_LEVERAGE

    status = await update.message.reply_text(f"⏳ تحلیل کوتاه‌مدت `{symbol}` ...")

    try:
        df_4h = fetch_klines(symbol, "4h", 250)
        df_1d = fetch_klines(symbol, "1d", 150)
        df_15m = fetch_klines(symbol, "15m", 120)

        if df_4h is None or len(df_4h) < 80:
            await status.edit_text("❌ خطا در دریافت داده.")
            return

        df_4h = add_indicators(df_4h)
        price = float(df_4h['close'].iloc[-1])
        rsi = float(df_4h['rsi'].iloc[-1])
        atr = float(calc_atr(df_4h).iloc[-1])

        funding_info = get_funding_info(symbol)
        funding = funding_info["rate"]
        next_fund = funding_info["next_time"]

        nds_d = analyze_nds(df_1d)
        nds_4h = analyze_nds(df_4h)
        nds_15 = analyze_nds(df_15m)
        higher = nds_d['bias'] if nds_d['bias'] != "خنثی" else nds_4h['bias']

        rets = np.diff(np.log(df_4h['close'].values[-80:]))
        mu, sigma = np.mean(rets), np.std(rets)
        horizon = 2
        sims = np.array([price * np.exp(np.cumsum(np.random.normal(mu, sigma, horizon))) for _ in range(50)])
        mean_path = np.mean(sims, axis=0)

        short_bias = mean_path[-1] > price
        if higher == "صعودی" and short_bias and rsi < 68:
            direction = "LONG"
            signal = "🟢 LONG"
            reason = "ساختار NDS بالاتر صعودی + تأیید کوتاه‌مدت"
            is_strong = True
        elif higher == "نزولی" and not short_bias and rsi > 32:
            direction = "SHORT"
            signal = "🔴 SHORT"
            reason = "ساختار NDS بالاتر نزولی + تأیید کوتاه‌مدت"
            is_strong = True
        else:
            direction = None
            signal = "⚪️ صبر"
            reason = "عدم تأیید کافی ساختار + افق کوتاه‌مدت"
            is_strong = False

        if direction:
            sl, tp = calculate_dollar_sl_tp(price, direction, atr, margin, lev, RISK_PER_TRADE)
            entry = price
        else:
            entry = price
            sl = price - atr * 1.25
            tp = price + atr * 1.25

        position_size = margin * lev
        risk_usd = abs(entry - sl) / entry * position_size
        reward_usd = abs(tp - entry) / entry * position_size
        rr = reward_usd / risk_usd if risk_usd > 0 else 0

        fibs = calc_fib(df_4h)
        buf = create_pro_chart(df_4h, f"𓄂 ηοsτrαμεhrδαδ𓆃 | {symbol} | 4H | {higher}",
                               fibs=fibs, entry=entry, sl=sl, tp=tp,
                               sims=sims, mean_path=mean_path)

        base = symbol.replace("USDT", "")
        glass = f"https://www.coinglass.com/currencies/{base}/funding-rate"

        if funding is not None:
            fund_line = f"`{funding:.4f}%`"
            if next_fund:
                fund_line += f" | بعدی: `{next_fund}`"
        else:
            fund_line = f"[مشاهده در Coinglass]({glass})"

        plan = (
            f"📋 **پلن معاملاتی | {symbol}**\n\n"
            f"سیگنال: **{signal}**\n"
            f"ورود: `{entry:.2f}`\n"
            f"حد ضرر: `{sl:.2f}`\n"
            f"حد سود: `{tp:.2f}`\n\n"
            f"💵 ریسک: `${risk_usd:.2f}` | سود احتمالی: `${reward_usd:.2f}`\n"
            f"R:R ← `1:{rr:.2f}`\n\n"
            f"دلیل: {reason}\n\n"
            f"💰 **فاندینگ ریت:** {fund_line}"
        )
        await update.message.reply_photo(photo=buf, caption=plan, parse_mode="Markdown")

        # اگر سیگنال قوی بود، پوزیشن را ثبت کن
        if is_strong and direction:
            set_open_position(uid, symbol, direction, entry, sl, tp)
            await update.message.reply_text(
                f"🔒 **پوزیشن ثبت شد**\n\n"
                f"تا زمان بستن این پوزیشن با دستور `/close`، سیگنال جدید صادر نمی‌شود.\n"
                f"برای مشاهده وضعیت: `/position`",
                parse_mode="Markdown"
            )

        text2 = (
            f"🧬 **NDS**\n"
            f"بایاس غالب: **{higher}**\n"
            f"• روزانه: {nds_d['cycle']} → {nds_d['bias']}\n"
            f"• ۴ ساعته: {nds_4h['cycle']} → {nds_4h['bias']}\n"
            f"• ۱۵ دقیقه: {nds_15['cycle']} → {nds_15['bias']}\n\n"
            f"RSI: `{rsi:.1f}` | ATR: `{atr:.2f}`"
        )
        await update.message.reply_text(text2, parse_mode="Markdown")
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
    app.add_handler(CommandHandler("news", news_command))
    app.add_handler(CommandHandler("predict", predict_command))
    app.add_handler(CommandHandler("nds", nds_command))
    app.add_handler(CommandHandler("log", log_trade))
    app.add_handler(CommandHandler("performance", performance))
    app.add_handler(CommandHandler("position", position_cmd))
    app.add_handler(CommandHandler("close", close_cmd))
    app.add_handler(CommandHandler("adduser", adduser))
    app.add_handler(CommandHandler("removeuser", removeuser))
    app.add_handler(CommandHandler("listusers", listusers))
    print("𓄂 ηοsτrαμεhrδαδ𓆃 آماده است")
    app.run_polling()

if __name__ == '__main__':
    main()