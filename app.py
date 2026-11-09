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
RATE_LIMIT_SECONDS = 35

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

def fetch_klines(symbol, interval, limit=250):
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200:
            df = pd.DataFrame(r.json(), columns=[
                'open_time','open','high','low','close','volume',
                'close_time','qav','num_trades','taker_base','taker_quote','ignore'
            ])
            for c in ['open','high','low','close','volume']:
                df[c] = df[c].astype(float)
            return df
    except:
        pass
    return None

def get_funding(symbol):
    try:
        r = requests.get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}", headers=HEADERS, timeout=8)
        if r.status_code == 200:
            d = r.json()
            rate = float(d.get('lastFundingRate', 0)) * 100
            nxt = datetime.fromtimestamp(int(d.get('nextFundingTime',0))/1000, tz=timezone.utc).strftime('%H:%M UTC')
            mark = float(d.get('markPrice', 0))
            return rate, nxt, mark
    except:
        pass
    return None, None, None

def get_crypto_news(limit=5):
    try:
        url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json().get("Data", [])
            news = []
            for item in data[:limit]:
                title = item.get("title", "").strip()
                source = item.get("source", "")
                if title:
                    news.append(f"• {title}\n  _{source}_")
            if news:
                return news
    except:
        pass
    return ["• دریافت خودکار اخبار فعلاً محدود است.", "• Coinglass و CryptoPanic را دستی بررسی کنید."]

def safe_rsi(s, p=14):
    d = s.diff()
    g = d.where(d > 0, 0).rolling(p).mean()
    l = (-d.where(d < 0, 0)).rolling(p).mean()
    rs = g / l.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)

def calc_atr(df, p=14):
    tr = pd.concat([
        df['high']-df['low'],
        (df['high']-df['close'].shift()).abs(),
        (df['low']-df['close'].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(p).mean()

def add_ichimoku(df):
    df['tenkan'] = (df['high'].rolling(9).max() + df['low'].rolling(9).min()) / 2
    df['kijun']  = (df['high'].rolling(26).max() + df['low'].rolling(26).min()) / 2
    df['senkou_a'] = ((df['tenkan'] + df['kijun']) / 2).shift(26)
    df['senkou_b'] = ((df['high'].rolling(52).max() + df['low'].rolling(52).min()) / 2).shift(26)
    return df

def calc_fib(df, lookback=90):
    h = df['high'].tail(lookback).max()
    l = df['low'].tail(lookback).min()
    d = h - l
    return {
        '0.0': h, '0.236': h-d*0.236, '0.382': h-d*0.382,
        '0.5': h-d*0.5, '0.618': h-d*0.618, '0.786': h-d*0.786, '1.0': l
    }

def find_nodes(df, mult=1.1, lb=4):
    if df is None or len(df) < 40: return []
    atr = calc_atr(df).bfill()
    nodes = []
    hs, ls = df['high'].values, df['low'].values
    for i in range(lb, len(df)-lb):
        if hs[i] == max(hs[i-lb:i+lb+1]) and (hs[i]-min(ls[i-lb:i])) > atr.iloc[i]*mult:
            nodes.append({'type':'high','price':float(hs[i])})
        if ls[i] == min(ls[i-lb:i+lb+1]) and (max(hs[i-lb:i])-ls[i]) > atr.iloc[i]*mult:
            nodes.append({'type':'low','price':float(ls[i])})
    return nodes

def analyze_nds(df):
    if df is None or len(df) < 50:
        return {'bias':'خنثی','cycle':'—','info':'—'}
    nodes = find_nodes(df)
    price = float(df['close'].iloc[-1])
    if len(nodes) < 4:
        return {'bias':'خنثی','cycle':'ناقص','info':'—'}
    highs = [n['price'] for n in nodes if n['type']=='high']
    lows  = [n['price'] for n in nodes if n['type']=='low']
    lh = highs[-1] if highs else price
    ph = highs[-2] if len(highs)>1 else lh
    ll = lows[-1] if lows else price
    pl = lows[-2] if len(lows)>1 else ll
    if lh > ph and ll > pl:
        bias, cycle = "صعودی", "HH+HL"
    elif lh < ph and ll < pl:
        bias, cycle = "نزولی", "LH+LL"
    else:
        bias, cycle = "خنثی", "رنج"
    return {'bias':bias, 'cycle':cycle, 'info':f"{lh:.2f} / {ll:.2f}"}

def safe_fmt(v, fmt=".4f"):
    if v is None: return None
    try: return format(v, fmt)
    except: return str(v)

# ===================== دستورات =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    await update.message.reply_text(
        "🤖 **Kronos | دستیار جامع ترید**\n\n"
        "نقش‌ها: مدیر ریسک + وکیل مدافع + مربی ژورنال\n\n"
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
        f"آدرس:\n`{USDT_WALLET}`\n\n"
        f"پس از واریز، رسید را ارسال کنید.",
        parse_mode="Markdown"
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if is_subscribed(uid):
        await update.message.reply_text(f"✅ فعال — **{remaining_days(uid)} روز** باقی‌مانده", parse_mode="Markdown")
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
        "• [GMGN](https://gmgn.ai)\n"
        "• [Bubblemaps](https://bubblemaps.io)\n"
        "• [Tokenomist](https://tokenomist.ai)"
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)

async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    msg = await update.message.reply_text("⏳ دریافت اخبار...")
    try:
        news_list = get_crypto_news(6)
        text = "📰 **آخرین اخبار بازار**\n\n" + "\n\n".join(news_list)
        await msg.edit_text(text, parse_mode="Markdown")
    except:
        await msg.edit_text("❌ خطا در دریافت اخبار.")

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
        await update.message.reply_text("`/log نماد جهت ورود خروج [pnl]`\nمثال: `/log BTCUSDT LONG 76000 76500 +40`", parse_mode="Markdown")
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
        await update.message.reply_text("✅ معامله ثبت شد.")
    except:
        await update.message.reply_text("فرمت اشتباه است.")

async def performance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    trades = load_json(JOURNAL_FILE).get(str(uid), [])
    if not trades:
        await update.message.reply_text("هنوز معامله‌ای ثبت نکرده‌اید.")
        return

    total = len(trades)
    pnls = [t.get("pnl", 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_count = len(wins)
    loss_count = len(losses)
    total_pnl = sum(pnls)
    winrate = (win_count / total * 100) if total > 0 else 0
    avg_win = (sum(wins)/win_count) if win_count else 0
    avg_loss = (sum(losses)/loss_count) if loss_count else 0
    profit_factor = (sum(wins)/abs(sum(losses))) if losses and sum(losses) != 0 else 0

    last_trades = trades[-5:]
    last_text = ""
    for t in reversed(last_trades):
        sign = "+" if t.get("pnl",0) >= 0 else ""
        last_text += f"`{t['symbol']}` {t['direction']} → {sign}{t.get('pnl',0):.1f}\n"

    text = (
        f"📊 **گزارش عملکرد**\n\n"
        f"تعداد کل: `{total}`\n"
        f"سودده: `{win_count}` | زیان‌ده: `{loss_count}`\n"
        f"وین‌ریت: `{winrate:.1f}%`\n\n"
        f"مجموع PnL: `{total_pnl:.2f}`\n"
        f"میانگین سود: `{avg_win:.2f}`\n"
        f"میانگین ضرر: `{avg_loss:.2f}`\n"
        f"فاکتور سود: `{profit_factor:.2f}`\n\n"
        f"**۵ معامله آخر:**\n{last_text}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

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

    status = await update.message.reply_text(f"⏳ تحلیل کامل `{symbol}` ...")

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
        atr = float(calc_atr(df_1h).iloc[-1])
        sma20 = float(df_1h['close'].rolling(20).mean().iloc[-1])
        sma50 = float(df_1h['close'].rolling(50).mean().iloc[-1])
        sma200 = float(df_1h['close'].rolling(200).mean().iloc[-1]) if len(df_1h) >= 200 else sma50
        tenkan = float(df_1h['tenkan'].iloc[-1])
        kijun = float(df_1h['kijun'].iloc[-1])

        funding, next_f, mark = get_funding(symbol)
        nds_d = analyze_nds(df_1d)
        nds_4h = analyze_nds(df_4h)
        nds_15 = analyze_nds(df_15m)
        higher = nds_d['bias'] if nds_d['bias'] != "خنثی" else nds_4h['bias']

        rets = np.diff(np.log(df_1h['close'].values))
        mu, sigma = np.mean(rets), np.std(rets)
        sims = np.array([price * np.exp(np.cumsum(np.random.normal(mu, sigma, 24))) for _ in range(70)])
        mean_path = np.mean(sims, axis=0)

        if higher == "صعودی" and mean_path[-1] > price and rsi < 65:
            signal, entry, sl, tp = "🟢 LONG", price, price - atr*1.55, price + atr*2.4
            reason = "NDS بالاتر صعودی + هم‌راستایی ابزارها"
        elif higher == "نزولی" and mean_path[-1] < price and rsi > 35:
            signal, entry, sl, tp = "🔴 SHORT", price, price + atr*1.55, price - atr*2.4
            reason = "NDS بالاتر نزولی + هم‌راستایی ابزارها"
        else:
            signal, entry, sl, tp = "⚪️ صبر", price, price - atr*1.25, price + atr*1.25
            reason = "عدم تأیید کافی ساختار NDS بالاتر"

        risk = abs(entry - sl) * (margin * lev / entry)

        # ===================== نمودار کامل =====================
        plot_df = df_1h.tail(110).copy()
        fig, ax = plt.subplots(figsize=(12.4, 6.8))

        # قیمت و میانگین‌ها
        ax.plot(plot_df['close'].values, color='#1f77b4', lw=1.8, label='قیمت', zorder=5)
        ax.plot(plot_df['close'].rolling(20).mean().values, color='#9467bd', lw=1.15, label='SMA20', alpha=0.9)
        ax.plot(plot_df['close'].rolling(50).mean().values, color='#ff7f0e', lw=1.35, label='SMA50')

        # ایچیموکو
        ax.plot(plot_df['tenkan'].values, color='#2ca02c', lw=1.1, label='Tenkan', alpha=0.9)
        ax.plot(plot_df['kijun'].values, color='#d62728', lw=1.1, label='Kijun', alpha=0.9)
        ax.fill_between(range(len(plot_df)), plot_df['senkou_a'].values, plot_df['senkou_b'].values,
                        where=plot_df['senkou_a'] >= plot_df['senkou_b'], color='#90ee90', alpha=0.25)
        ax.fill_between(range(len(plot_df)), plot_df['senkou_a'].values, plot_df['senkou_b'].values,
                        where=plot_df['senkou_a'] < plot_df['senkou_b'], color='#ffb6c1', alpha=0.25)

        # فیبوناچی با قیمت و رنگ
        fibs = calc_fib(df_1h)
        fib_colors = {
            '0.0': '#111111', '0.236': '#8B0000', '0.382': '#FF4500',
            '0.5': '#DAA520', '0.618': '#228B22', '0.786': '#1E90FF', '1.0': '#4B0082'
        }
        for k, v in fibs.items():
            c = fib_colors.get(k, '#555555')
            ax.axhline(v, color=c, ls='--', lw=0.9, alpha=0.85)
            ax.text(len(plot_df) + 1.7, v, f' {k}  {v:.1f}', fontsize=8, va='center', color=c, fontweight='bold')

        # هاله مونت‌کارلو
        last = len(plot_df) - 1
        for s in sims:
            ax.plot(range(last, last+24), s, color='gray', alpha=0.13, lw=0.7)
        ax.plot(range(last, last+24), mean_path, color='#c0392b', lw=2.2, label='پیش‌بینی ۲۴h', zorder=6)

        # محور قیمت سمت راست و مقیاس ریزتر
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")
        ax.yaxis.set_major_locator(MaxNLocator(nbins=13))
        ax.yaxis.set_major_formatter(FormatStrFormatter('%.1f'))
        ax.set_ylabel("قیمت")
        ax.set_title(f"Kronos | {symbol} | NDS: {higher}", fontsize=12, pad=10)
        ax.legend(loc='upper left', fontsize=8, framealpha=0.92)
        ax.grid(True, alpha=0.3)
        ax.set_xlim(0, len(plot_df) + 32)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=140)
        buf.seek(0)
        plt.close()

        # فاندینگ
        base = symbol.replace("USDT", "")
        glass = f"https://www.coinglass.com/currencies/{base}/funding-rate"
        fund_line = f"`{safe_fmt(funding, '.4f')}%`" if funding is not None else f"[مشاهده در Coinglass]({glass})"

        # پیام ۱
        cap = (
            f"🎯 **{symbol}**\n\n"
            f"سیگنال: **{signal}**\n"
            f"ورود: `{entry:.2f}`\n"
            f"حد ضرر: `{sl:.2f}`\n"
            f"حد سود: `{tp:.2f}`\n"
            f"دلیل: {reason}\n\n"
            f"💰 فاندینگ ریت: {fund_line}"
        )
        await update.message.reply_photo(photo=buf, caption=cap, parse_mode="Markdown")

        # پیام ۲ - تحلیل جامع
        text2 = (
            f"📌 **مدیر ریسک (XT.com)**\n"
            f"مارجین: `${margin}`\n"
            f"اهرم: `{lev}x Isolated`\n"
            f"حجم پوزیشن: `${margin*lev:.1f}`\n"
            f"ریسک این ستاپ: `${risk:.2f}`\n\n"
            f"🧬 **وکیل مدافع + NDS**\n"
            f"بایاس غالب: **{higher}**\n"
            f"• روزانه: {nds_d['cycle']} → **{nds_d['bias']}**\n"
            f"• ۴ ساعته: {nds_4h['cycle']} → **{nds_4h['bias']}**\n"
            f"• ۱۵ دقیقه: {nds_15['cycle']} → **{nds_15['bias']}**\n\n"
            f"**اندیکاتورها و ساختار**\n"
            f"• RSI: `{rsi:.1f}`\n"
            f"• ATR: `{atr:.2f}`\n"
            f"• SMA20: `{sma20:.1f}` | SMA50: `{sma50:.1f}` | SMA200: `{sma200:.1f}`\n"
            f"• Tenkan: `{tenkan:.1f}` | Kijun: `{kijun:.1f}`\n\n"
            f"**سناریوها**\n"
            f"• صعودی: حفظ ساختار HH+HL و شکست مقاومت‌های پیش‌رو\n"
            f"• نزولی: ادامه LH+LL و از دست رفتن حمایت‌های کلیدی\n"
            f"• رنج: نوسان بین سطوح مهم تا زمان شکست معتبر"
        )
        await update.message.reply_text(text2, parse_mode="Markdown")

        await update.message.reply_text(
            "🛡 اولویت همیشه با ساختار NDS تایم‌فریم بالاتر است.\n"
            "در صورت تناقض بین ابزارها، صبر منطقی‌ترین انتخاب است.",
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
    app.add_handler(CommandHandler("news", news_command))
    app.add_handler(CommandHandler("predict", predict_command))
    app.add_handler(CommandHandler("log", log_trade))
    app.add_handler(CommandHandler("performance", performance))
    app.add_handler(CommandHandler("adduser", adduser))
    app.add_handler(CommandHandler("removeuser", removeuser))
    app.add_handler(CommandHandler("listusers", listusers))
    print("Kronos آماده است")
    app.run_polling()

if __name__ == '__main__':
    main()