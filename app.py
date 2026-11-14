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
from matplotlib.ticker import FormatStrFormatter
from matplotlib.patches import Rectangle
from matplotlib.gridspec import GridSpec

from flask import Flask
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

try:
    import ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False

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
ALERT_STATE_FILE = "alert_state.json"
PENDING_FILE = "pending_signal.json"
WATCHLIST_FILE = "watchlist.json"

DEFAULT_MARGIN = 5.0
DEFAULT_LEVERAGE = 8
RISK_PER_TRADE = 0.5
RATE_LIMIT_SECONDS = 40
MONITOR_INTERVAL = 180
DANGER_PERCENT = 0.35

MARKET_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT"]

# تایم‌فریم‌های NDS (بایننس)
NDS_INTERVALS = [
    ("5m", "۵ دقیقه"),
    ("15m", "۱۵ دقیقه"),
    ("30m", "۳۰ دقیقه"),
    ("1h", "۱ ساعت"),
    ("2h", "۲ ساعت"),
    ("4h", "۴ ساعت"),
    ("6h", "۶ ساعت"),
    ("8h", "۸ ساعت"),
    ("12h", "۱۲ ساعت"),
    ("1d", "۱ روز"),
    ("3d", "۳ روز"),
    ("1w", "۱ هفته"),
    ("1M", "۱ ماه"),
]

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

def get_open_position(uid):
    return load_json(POSITION_FILE).get(str(uid))

def set_open_position(uid, symbol, direction, entry, sl, tp):
    data = load_json(POSITION_FILE)
    data[str(uid)] = {
        "symbol": symbol, "direction": direction,
        "entry": entry, "sl": sl, "tp": tp,
        "opened_at": datetime.now(timezone.utc).isoformat()
    }
    save_json(POSITION_FILE, data)

def close_position(uid):
    data = load_json(POSITION_FILE)
    if str(uid) in data:
        del data[str(uid)]
        save_json(POSITION_FILE, data)
        alerts = load_json(ALERT_STATE_FILE)
        if str(uid) in alerts:
            del alerts[str(uid)]
            save_json(ALERT_STATE_FILE, alerts)
        return True
    return False

def set_pending_signal(uid, data):
    all_p = load_json(PENDING_FILE)
    all_p[str(uid)] = data
    save_json(PENDING_FILE, all_p)

def get_pending_signal(uid):
    return load_json(PENDING_FILE).get(str(uid))

def clear_pending_signal(uid):
    all_p = load_json(PENDING_FILE)
    if str(uid) in all_p:
        del all_p[str(uid)]
        save_json(PENDING_FILE, all_p)

def get_watchlist(uid):
    return load_json(WATCHLIST_FILE).get(str(uid), [])

def add_to_watchlist(uid, symbols):
    data = load_json(WATCHLIST_FILE)
    current = set(data.get(str(uid), []))
    for s in symbols:
        current.add(s.upper())
    data[str(uid)] = list(current)
    save_json(WATCHLIST_FILE, data)
    return data[str(uid)]

def remove_from_watchlist(uid, symbols):
    data = load_json(WATCHLIST_FILE)
    current = set(data.get(str(uid), []))
    for s in symbols:
        current.discard(s.upper())
    data[str(uid)] = list(current)
    save_json(WATCHLIST_FILE, data)
    return data[str(uid)]

def get_binance_price(symbol):
    try:
        url = f"https://data-api.binance.vision/api/v3/ticker/price?symbol={symbol}"
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            return float(r.json()["price"])
    except:
        pass
    return None

def get_multi_exchange_prices(symbol):
    result = {"binance": None, "coinbase": None, "kraken": None, "consensus": None, "spread_pct": None, "sources_ok": 0}
    result["binance"] = get_binance_price(symbol)
    if not CCXT_AVAILABLE:
        result["consensus"] = result["binance"]
        result["sources_ok"] = 1 if result["binance"] else 0
        return result
    base = symbol.replace("USDT", "").replace("USD", "")
    try:
        ex = ccxt.coinbase({"enableRateLimit": True, "timeout": 10000})
        t = ex.fetch_ticker(f"{base}/USD")
        if t and t.get("last"):
            result["coinbase"] = float(t["last"])
    except Exception as e:
        print("Coinbase:", e)
    try:
        ex = ccxt.kraken({"enableRateLimit": True, "timeout": 10000})
        t = ex.fetch_ticker(f"{base}/USD")
        if t and t.get("last"):
            result["kraken"] = float(t["last"])
    except Exception as e:
        print("Kraken:", e)
    prices = [p for p in [result["binance"], result["coinbase"], result["kraken"]] if p is not None]
    result["sources_ok"] = len(prices)
    if prices:
        result["consensus"] = sum(prices) / len(prices)
        if len(prices) >= 2:
            result["spread_pct"] = (max(prices) - min(prices)) / result["consensus"] * 100
    return result

def get_current_price(symbol):
    multi = get_multi_exchange_prices(symbol)
    return multi["consensus"] or multi["binance"]

def format_price_sources(multi):
    parts = []
    if multi["binance"]: parts.append(f"Binance: `{multi['binance']:.4f}`")
    if multi["coinbase"]: parts.append(f"Coinbase: `{multi['coinbase']:.4f}`")
    if multi["kraken"]: parts.append(f"Kraken: `{multi['kraken']:.4f}`")
    if multi["consensus"]: parts.append(f"اجماع: `{multi['consensus']:.4f}`")
    if multi.get("spread_pct") and multi["spread_pct"] > 0.15:
        parts.append(f"⚠️ اختلاف: `{multi['spread_pct']:.3f}%`")
    return " | ".join(parts) if parts else "قیمت در دسترس نیست"

def monitor_positions(bot: Bot):
    while True:
        try:
            positions = load_json(POSITION_FILE)
            alerts = load_json(ALERT_STATE_FILE)
            for uid_str, pos in positions.items():
                try:
                    uid = int(uid_str)
                    symbol = pos["symbol"]
                    direction = pos["direction"]
                    entry = float(pos["entry"])
                    sl = float(pos["sl"])
                    tp = float(pos["tp"])
                    price = get_current_price(symbol)
                    if price is None: continue
                    if direction == "LONG":
                        danger = price <= entry - (entry - sl) * (1 - DANGER_PERCENT)
                        near_tp = price >= entry + (tp - entry) * 0.7
                    else:
                        danger = price >= entry + (sl - entry) * (1 - DANGER_PERCENT)
                        near_tp = price <= entry - (entry - tp) * 0.7
                    user_alerts = alerts.get(uid_str, {})
                    if danger and not user_alerts.get("sl_warned"):
                        msg = f"⚠️ **هشدار محدوده خطر**\n\n`{symbol}` | {direction}\nقیمت: `{price:.2f}`\nحد ضرر: `{sl:.2f}`"
                        try:
                            bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
                            user_alerts["sl_warned"] = True
                        except: pass
                    if near_tp and not user_alerts.get("tp_warned"):
                        msg = f"🎯 **نزدیک به حد سود**\n\n`{symbol}` | {direction}\nقیمت: `{price:.2f}`\nحد سود: `{tp:.2f}`"
                        try:
                            bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
                            user_alerts["tp_warned"] = True
                        except: pass
                    alerts[uid_str] = user_alerts
                except Exception as e:
                    print("Monitor user:", e)
            save_json(ALERT_STATE_FILE, alerts)
        except Exception as e:
            print("Monitor loop:", e)
        time.sleep(MONITOR_INTERVAL)

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
        print("Fetch:", e)
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
    except: pass
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
    if df is None or len(df) < 50: return []
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

def analyze_nds_multi(symbol):
    """تحلیل NDS روی تمام تایم‌فریم‌های تعریف‌شده"""
    results = []
    for interval, label in NDS_INTERVALS:
        limit = 200 if interval in ["1d", "3d", "1w", "1M"] else 150
        df = fetch_klines(symbol, interval, limit)
        if df is None or len(df) < 50:
            results.append({"interval": interval, "label": label, "bias": "—", "cycle": "داده ناکافی", "ok": False})
            continue
        info = analyze_nds(df)
        results.append({
            "interval": interval, "label": label,
            "bias": info["bias"], "cycle": info["cycle"],
            "last_high": info.get("last_high"), "last_low": info.get("last_low"),
            "ok": True
        })
    return results

def calculate_signal_score(nds_results, rsi, funding, volume_ratio, higher_bias, short_bias):
    """مرحله B: امتیازدهی چندعاملی ۰ تا ۱۰۰"""
    score = 50.0
    reasons = []
    risks = []

    # ۱. NDS تایم‌فریم‌های بالاتر (وزن بالا)
    higher_tfs = [r for r in nds_results if r["interval"] in ["1d", "3d", "1w", "1M", "12h", "8h"] and r["ok"]]
    bullish_h = sum(1 for r in higher_tfs if r["bias"] == "صعودی")
    bearish_h = sum(1 for r in higher_tfs if r["bias"] == "نزولی")
    if bullish_h > bearish_h and bullish_h >= 2:
        score += 18
        reasons.append(f"NDS تایم‌فریم بالاتر عمدتاً صعودی ({bullish_h} مورد)")
    elif bearish_h > bullish_h and bearish_h >= 2:
        score -= 18
        reasons.append(f"NDS تایم‌فریم بالاتر عمدتاً نزولی ({bearish_h} مورد)")
    else:
        risks.append("عدم هم‌راستایی کافی در تایم‌فریم‌های بالاتر")

    # ۲. هم‌راستایی میان‌مدت
    mid_tfs = [r for r in nds_results if r["interval"] in ["4h", "6h", "2h", "1h"] and r["ok"]]
    if mid_tfs:
        mid_bias = max(set([r["bias"] for r in mid_tfs]), key=[r["bias"] for r in mid_tfs].count)
        if mid_bias == higher_bias and higher_bias != "خنثی":
            score += 10
            reasons.append(f"هم‌راستایی میان‌مدت با بایاس غالب ({mid_bias})")
        elif mid_bias != higher_bias and higher_bias != "خنثی":
            score -= 8
            risks.append("واگرایی بین تایم‌فریم میان‌مدت و بالاتر")

    # ۳. RSI
    if 35 <= rsi <= 65:
        score += 6
        reasons.append(f"RSI در ناحیه منطقی ({rsi:.1f})")
    elif rsi > 70:
        score -= 8
        risks.append(f"RSI اشباع خرید ({rsi:.1f})")
    elif rsi < 30:
        score -= 8
        risks.append(f"RSI اشباع فروش ({rsi:.1f})")

    # ۴. فاندینگ
    if funding is not None:
        if funding < -0.01:
            score += 5
            reasons.append(f"فاندینگ منفی خفیف/قوی ({funding:.4f}%)")
        elif funding > 0.05:
            score -= 6
            risks.append(f"فاندینگ بالا ({funding:.4f}%)")
        else:
            reasons.append(f"فاندینگ خنثی ({funding:.4f}%)")

    # ۵. حجم نسبی
    if volume_ratio is not None:
        if volume_ratio >= 1.2:
            score += 5
            reasons.append(f"حجم نسبی مناسب ({volume_ratio:.2f}x)")
        elif volume_ratio < 0.6:
            score -= 4
            risks.append(f"حجم ضعیف ({volume_ratio:.2f}x)")

    # ۶. تأیید کوتاه‌مدت مونت‌کارلو
    if higher_bias == "صعودی" and short_bias:
        score += 8
        reasons.append("تأیید کوتاه‌مدت مونت‌کارلو با بایاس صعودی")
    elif higher_bias == "نزولی" and not short_bias:
        score += 8
        reasons.append("تأیید کوتاه‌مدت مونت‌کارلو با بایاس نزولی")
    elif higher_bias != "خنثی":
        score -= 5
        risks.append("عدم تأیید کوتاه‌مدت با بایاس غالب")

    score = max(0, min(100, score))
    return round(score, 1), reasons, risks

def calculate_dollar_sl_tp(entry, direction, atr, margin=5.0, leverage=8, risk_dollar=0.5):
    position_size = margin * leverage
    price_risk = (risk_dollar / position_size) * entry
    atr_distance = atr * 1.25
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
    fig = plt.figure(figsize=(14, 9.4), facecolor='#0d0d0d')
    gs = GridSpec(4, 1, height_ratios=[3.3, 0.9, 0.9, 0.04], hspace=0.07)
    ax = fig.add_subplot(gs[0])
    ax_vol = fig.add_subplot(gs[1], sharex=ax)
    ax_rsi = fig.add_subplot(gs[2], sharex=ax)
    for a in [ax, ax_vol, ax_rsi]:
        a.set_facecolor('#0d0d0d')
        a.tick_params(colors='#cccccc', labelsize=7.2)
        for spine in a.spines.values():
            spine.set_color('#333333')
    ax.plot(plot_df['close'].values, color='#00bfff', lw=1.65, label='Price', zorder=5)
    ax.plot(plot_df['sma20'].values, color='#da70d6', lw=1.0, label='MA20', alpha=0.9)
    ax.plot(plot_df['sma50'].values, color='#ffa500', lw=1.1, label='MA50')
    if 'sma200' in plot_df.columns:
        ax.plot(plot_df['sma200'].values, color='#1e90ff', lw=1.15, label='MA200', alpha=0.85)
    ax.plot(plot_df['ema20'].values, color='#00fa9a', lw=0.95, label='EMA20', alpha=0.85)
    ax.plot(plot_df['ema50'].values, color='#ff6347', lw=1.0, label='EMA50', alpha=0.85)
    ax.plot(plot_df['bb_upper'].values, color='#888888', lw=0.8, ls='--', alpha=0.7)
    ax.plot(plot_df['bb_lower'].values, color='#888888', lw=0.8, ls='--', alpha=0.7)
    ax.fill_between(range(n), plot_df['bb_upper'].values, plot_df['bb_lower'].values, color='#444444', alpha=0.14)
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
            ax.axhline(v, color=c, ls=':', lw=0.85, alpha=0.8)
            ax.text(n + 1.1, v, f'{k} {v:.2f}', fontsize=6.8, va='center', color=c)
    if entry: ax.axhline(entry, color='#1e90ff', lw=1.3, label='Entry')
    if sl: ax.axhline(sl, color='#ff4757', lw=1.3, label='SL')
    if tp: ax.axhline(tp, color='#2ed573', lw=1.3, label='TP')
    if sims is not None and mean_path is not None:
        last = n - 1
        horizon = len(mean_path)
        for s in sims[::3]:
            ax.plot(range(last, last+horizon), s[:horizon], color='#ffffff', alpha=0.18, lw=0.7)
        ax.plot(range(last, last+horizon), mean_path, color='#ffffff', lw=1.9, label='Pred 4-6h', zorder=6)
    if nds_info:
        last_high = nds_info.get('last_high')
        last_low = nds_info.get('last_low')
        nodes = nds_info.get('nodes', [])
        offset = len(df) - n
        if mark_hugs and nodes:
            recent_nodes = nodes[-6:] if len(nodes) >= 6 else nodes
            hug_count = flag_count = 0
            for node in recent_nodes:
                x = node['index'] - offset
                if not (0 <= x < n): continue
                y = node['price']
                if node['type'] == 'high':
                    hug_count += 1
                    color, label = '#ff6b6b', f"H{hug_count}"
                else:
                    flag_count += 1
                    color, label = '#1dd1a1', f"F{flag_count}"
                size = n * 0.008
                rect = Rectangle((x - size/2, y - (y*0.004)), size, y*0.008,
                                 linewidth=1.3, edgecolor=color, facecolor=color, alpha=0.85, zorder=11)
                ax.add_patch(rect)
                ax.text(x, y + (y*0.012), label, fontsize=6.8, ha='center', color=color, fontweight='bold', zorder=12)
        if bias == "نزولی" and last_low:
            ym = last_low - (last_high - last_low)*0.28 if last_high else last_low*0.985
            ax.scatter([n-5], [ym], marker='x', s=120, color='#ff4757', linewidths=2.2, zorder=12)
            ax.text(n-5, ym*0.992, f"{ym:.1f}", fontsize=7, ha='center', color='#ff4757', fontweight='bold')
        elif bias == "صعودی" and last_high:
            ym = last_high + (last_high - last_low)*0.28 if last_low else last_high*1.015
            ax.scatter([n-5], [ym], marker='x', s=120, color='#ff4757', linewidths=2.2, zorder=12)
            ax.text(n-5, ym*1.008, f"{ym:.1f}", fontsize=7, ha='center', color='#ff4757', fontweight='bold')
    ax.set_title(title, color='white', fontsize=11.5, pad=8)
    ax.legend(loc='upper left', fontsize=6.8, facecolor='#1a1a1a', edgecolor='#333', labelcolor='white', ncol=3)
    ax.grid(True, alpha=0.18, color='#444')
    ax.yaxis.tick_right()
    ax.yaxis.set_major_formatter(FormatStrFormatter('%.2f'))
    ax.set_xlim(0, n+14)
    colors_vol = ['#2ed573' if plot_df['close'].iloc[i] >= plot_df['open'].iloc[i] else '#ff4757' for i in range(n)]
    ax_vol.bar(range(n), plot_df['volume'].values, color=colors_vol, alpha=0.7, width=0.8)
    ax_vol.plot(plot_df['volume'].rolling(5).mean().values, color='#00bfff', lw=1.0, alpha=0.8)
    ax_vol.set_ylabel('Vol', color='#aaaaaa', fontsize=7.2)
    ax_vol.grid(True, alpha=0.14, color='#444')
    ax_rsi.plot(plot_df['rsi'].values, color='#e056fd', lw=1.1)
    ax_rsi.axhline(70, color='#ff4757', ls='--', lw=0.7, alpha=0.6)
    ax_rsi.axhline(30, color='#2ed573', ls='--', lw=0.7, alpha=0.6)
    ax_rsi.axhline(50, color='#888888', ls=':', lw=0.6, alpha=0.5)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel('RSI', color='#aaaaaa', fontsize=7.2)
    ax_rsi.grid(True, alpha=0.14, color='#444')
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
        "`/predict`  `/nds`  `/market`\n"
        "`/position`  `/close`  `/confirm`\n"
        "`/watch`  `/watchlist`  `/unwatch`\n"
        "`/log`  `/performance`\n"
        "`/buy`  `/status`  `/tools`  `/news`",
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
    base = (context.args[0].upper() if context.args else "BTC").replace("USDT", "")
    text = (
        f"📰 **اخبار و افراد تأثیرگذار | {base}**\n\n"
        f"• [Coinglass](https://www.coinglass.com)\n"
        f"• [Lookonchain](https://www.lookonchain.com)\n"
        f"• [Arkham](https://www.arkham.com)\n"
        f"• [CryptoPanic](https://cryptopanic.com)\n"
        f"• [CoinMarketCal](https://coinmarketcal.com)\n\n"
        f"💡 اظهارات افراد تأثیرگذار اغلب باعث حرکت سریع بازار می‌شود."
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)

async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if not context.args:
        await update.message.reply_text("مثال:\n`/watch BTCUSDT ETHUSDT`", parse_mode="Markdown")
        return
    result = add_to_watchlist(uid, [s.upper() for s in context.args])
    await update.message.reply_text("✅ واچ‌لیست:\n`" + "`, `".join(result) + "`", parse_mode="Markdown")

async def unwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if not context.args:
        await update.message.reply_text("مثال:\n`/unwatch BTCUSDT`", parse_mode="Markdown")
        return
    result = remove_from_watchlist(uid, [s.upper() for s in context.args])
    if result:
        await update.message.reply_text("✅ واچ‌لیست:\n`" + "`, `".join(result) + "`", parse_mode="Markdown")
    else:
        await update.message.reply_text("واچ‌لیست خالی شد.")

async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    wl = get_watchlist(uid)
    if not wl:
        await update.message.reply_text("واچ‌لیست خالی است.")
        return
    status = await update.message.reply_text("⏳ بررسی واچ‌لیست ...")
    lines = ["👀 **واچ‌لیست**\n"]
    for symbol in wl:
        try:
            df_4h = fetch_klines(symbol, "4h", 100)
            df_1d = fetch_klines(symbol, "1d", 80)
            if df_4h is None:
                lines.append(f"• `{symbol}`: داده نیست")
                continue
            nds_d = analyze_nds(df_1d) if df_1d is not None else {'bias': '—'}
            nds_4h = analyze_nds(df_4h)
            higher = nds_d['bias'] if nds_d.get('bias') != "خنثی" else nds_4h['bias']
            price = float(df_4h['close'].iloc[-1])
            emoji = "🟢" if higher == "صعودی" else "🔴" if higher == "نزولی" else "⚪️"
            lines.append(f"{emoji} `{symbol}` → **{higher}** | `{price:.4f}`")
        except:
            lines.append(f"• `{symbol}`: خطا")
    await status.edit_text("\n".join(lines), parse_mode="Markdown")

async def confirm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    pending = get_pending_signal(uid)
    if not pending:
        await update.message.reply_text("سیگنال معلقی وجود ندارد.")
        return
    if get_open_position(uid):
        await update.message.reply_text("⚠️ پوزیشن باز دارید.")
        return
    set_open_position(uid, pending["symbol"], pending["direction"], pending["entry"], pending["sl"], pending["tp"])
    clear_pending_signal(uid)
    await update.message.reply_text(
        f"✅ **پوزیشن ثبت شد**\n\n`{pending['symbol']}` | {pending['direction']}\n"
        f"ورود: `{pending['entry']}` | SL: `{pending['sl']}` | TP: `{pending['tp']}`\n\n`/position` | `/close`",
        parse_mode="Markdown"
    )

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
        await update.message.reply_text("`/log نماد جهت ورود خروج [pnl] [یادداشت]`", parse_mode="Markdown")
        return
    try:
        symbol = context.args[0].upper()
        direction = context.args[1].upper()
        entry = float(context.args[2])
        exit_p = float(context.args[3])
        pnl, note = 0.0, ""
        if len(context.args) >= 5:
            try:
                pnl = float(context.args[4])
                note = " ".join(context.args[5:]) if len(context.args) > 5 else ""
            except:
                note = " ".join(context.args[4:])
        journal = load_json(JOURNAL_FILE)
        trades = journal.get(str(uid), [])
        trades.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol, "direction": direction,
            "entry": entry, "exit": exit_p, "pnl": pnl, "note": note
        })
        journal[str(uid)] = trades
        save_json(JOURNAL_FILE, journal)
        close_position(uid)
        clear_pending_signal(uid)
        msg = f"✅ ثبت شد.\n`{symbol}` | {direction} | PnL: `{pnl}`"
        if note: msg += f"\nیادداشت: {note}"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"خطا: {str(e)[:80]}")

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
    pnls = [float(t.get("pnl", 0)) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_count, loss_count = len(wins), len(losses)
    winrate = (win_count / total * 100) if total else 0
    total_pnl = sum(pnls)
    avg_win = sum(wins)/win_count if win_count else 0
    avg_loss = sum(losses)/loss_count if loss_count else 0
    best = max(pnls) if pnls else 0
    worst = min(pnls) if pnls else 0
    text = (
        f"📊 **عملکرد ژورنال**\n\n"
        f"کل: `{total}` | برد: `{win_count}` | باخت: `{loss_count}`\n"
        f"وین‌ریت: `{winrate:.1f}%`\n"
        f"مجموع PnL: `{total_pnl:.2f}`\n"
        f"میانگین سود: `{avg_win:.2f}` | میانگین ضرر: `{avg_loss:.2f}`\n"
        f"بهترین: `{best:.2f}` | بدترین: `{worst:.2f}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def position_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    pos = get_open_position(uid)
    if not pos:
        await update.message.reply_text("📭 پوزیشن بازی وجود ندارد.")
        return
    multi = get_multi_exchange_prices(pos["symbol"])
    price = multi["consensus"] or multi["binance"]
    text = (
        f"📌 **پوزیشن باز**\n\n"
        f"نماد: `{pos['symbol']}`\nجهت: **{pos['direction']}**\n"
        f"ورود: `{pos['entry']}`\nحد ضرر: `{pos['sl']}`\nحد سود: `{pos['tp']}`\n"
    )
    if price:
        text += f"قیمت فعلی: `{price:.2f}`\n"
        if pos["direction"] == "LONG":
            pnl_pct = (price - pos["entry"]) / pos["entry"] * 100
        else:
            pnl_pct = (pos["entry"] - price) / pos["entry"] * 100
        text += f"PnL تقریبی: `{pnl_pct:.2f}%`\n"
        text += f"منابع: {format_price_sources(multi)}\n"
    text += f"زمان: `{pos['opened_at'][:16]}`"
    await update.message.reply_text(text, parse_mode="Markdown")

async def close_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if close_position(uid):
        clear_pending_signal(uid)
        await update.message.reply_text("✅ پوزیشن بسته شد.")
    else:
        await update.message.reply_text("📭 پوزیشن بازی وجود ندارد.")

async def market_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not check_rate(uid):
        await update.message.reply_text("⏳ کمی صبر کنید.")
        return
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    status = await update.message.reply_text("⏳ وضعیت بازار ...")
    try:
        lines = ["🌐 **خلاصه بازار (NDS)**\n"]
        for symbol in MARKET_SYMBOLS:
            try:
                df_1d = fetch_klines(symbol, "1d", 120)
                df_4h = fetch_klines(symbol, "4h", 150)
                if df_1d is None or df_4h is None:
                    lines.append(f"• `{symbol}`: داده نیست")
                    continue
                nds_d = analyze_nds(df_1d)
                nds_4h = analyze_nds(df_4h)
                higher = nds_d['bias'] if nds_d['bias'] != "خنثی" else nds_4h['bias']
                multi = get_multi_exchange_prices(symbol)
                price = multi["consensus"] or float(df_4h['close'].iloc[-1])
                emoji = "🟢" if higher == "صعودی" else "🔴" if higher == "نزولی" else "⚪️"
                lines.append(f"{emoji} `{symbol}` → **{higher}** | `{price:.4f}`")
            except:
                lines.append(f"• `{symbol}`: خطا")
        open_pos = get_open_position(uid)
        if open_pos:
            lines.append(f"\n📌 پوزیشن باز: `{open_pos['symbol']}` | {open_pos['direction']}")
        await status.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await status.edit_text(f"❌ خطا: {str(e)[:80]}")

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
    status = await update.message.reply_text(f"⏳ تحلیل NDS چندتایم‌فریم `{symbol}` ...")

    try:
        # نمودار اصلی روی ۴H
        df_4h = fetch_klines(symbol, "4h", 300)
        if df_4h is None or len(df_4h) < 80:
            await status.edit_text("❌ خطا در دریافت داده ۴H.")
            return
        df_4h = add_indicators(df_4h)
        multi = get_multi_exchange_prices(symbol)
        price = multi["consensus"] or float(df_4h['close'].iloc[-1])
        atr = float(calc_atr(df_4h).iloc[-1])
        rsi = float(df_4h['rsi'].iloc[-1])
        nds_4h = analyze_nds(df_4h)
        fibs = calc_fib(df_4h)
        last_high = nds_4h.get('last_high')
        last_low = nds_4h.get('last_low')
        higher = nds_4h['bias']

        # تحلیل تمام تایم‌فریم‌ها
        multi_nds = analyze_nds_multi(symbol)

        # تعیین بایاس غالب از تایم‌فریم‌های بالاتر
        higher_list = [r for r in multi_nds if r["interval"] in ["1d", "3d", "1w", "1M", "12h"] and r["ok"]]
        if higher_list:
            biases = [r["bias"] for r in higher_list if r["bias"] != "خنثی"]
            if biases:
                higher = max(set(biases), key=biases.count)

        buf = create_pro_chart(df_4h, f"𓄂 ηοsτrαμεhrδαδ𓆃 | {symbol} | 4H | {higher}",
                               fibs=fibs, nds_info=nds_4h, bias=higher, mark_hugs=True)

        # پیام ۱: نمودار + خلاصه
        caption = (
            f"🧬 **NDS | {symbol}**\n\n"
            f"**بایاس غالب:** {higher}\n\n"
            f"**حمایت و مقاومت (۴H)**\n"
        )
        if last_high: caption += f"• مقاومت: `{last_high:.4f}`\n"
        if last_low: caption += f"• حمایت: `{last_low:.4f}`\n"
        caption += f"\nRSI: `{rsi:.1f}` | ATR: `{atr:.4f}` | قیمت: `{price:.4f}`\n"
        caption += f"منابع: {format_price_sources(multi)}"
        await update.message.reply_photo(photo=buf, caption=caption, parse_mode="Markdown")

        # پیام ۲ و ۳: تمام تایم‌فریم‌ها
        lines = ["📊 **وضعیت NDS در تایم‌فریم‌ها**\n"]
        for r in multi_nds:
            if not r["ok"]:
                lines.append(f"• {r['label']}: داده ناکافی")
                continue
            emoji = "🟢" if r["bias"] == "صعودی" else "🔴" if r["bias"] == "نزولی" else "⚪️"
            lines.append(f"{emoji} {r['label']}: {r['cycle']} → **{r['bias']}**")
        # تقسیم به دو پیام اگر طولانی شد
        mid = len(lines) // 2 + 1
        await update.message.reply_text("\n".join(lines[:mid]), parse_mode="Markdown")
        if len(lines) > mid:
            await update.message.reply_text("\n".join(lines[mid:]), parse_mode="Markdown")

        # پیام پایانی: جمع‌بندی
        if higher == "صعودی":
            cont = f"ساختار غالب صعودی است. ادامه حرکت بالای حمایت‌های کلیدی محتمل‌تر است."
        elif higher == "نزولی":
            cont = f"ساختار غالب نزولی است. ادامه حرکت زیر مقاومت‌های کلیدی محتمل‌تر است."
        else:
            cont = "ساختار غالب خنثی/رنج است. صبر منطقی‌تر است."
        await update.message.reply_text(f"📌 **جمع‌بندی**\n{cont}", parse_mode="Markdown")
        await status.delete()

    except Exception as e:
        await status.edit_text(f"❌ خطا: {str(e)[:100]}")

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

    open_pos = get_open_position(uid)
    status = await update.message.reply_text(f"⏳ تحلیل کامل `{symbol}` ...")

    try:
        df_4h = fetch_klines(symbol, "4h", 250)
        if df_4h is None or len(df_4h) < 80:
            await status.edit_text("❌ خطا در دریافت داده.")
            return
        df_4h = add_indicators(df_4h)
        multi = get_multi_exchange_prices(symbol)
        price = multi["consensus"] or float(df_4h['close'].iloc[-1])
        rsi = float(df_4h['rsi'].iloc[-1])
        atr = float(calc_atr(df_4h).iloc[-1])
        vol_ratio = float(df_4h['volume'].iloc[-1] / df_4h['volume'].tail(20).mean()) if df_4h['volume'].tail(20).mean() > 0 else 1.0
        funding_info = get_funding_info(symbol)
        funding = funding_info["rate"]
        next_fund = funding_info["next_time"]

        # NDS چندتایم‌فریم
        multi_nds = analyze_nds_multi(symbol)
        higher_list = [r for r in multi_nds if r["interval"] in ["1d", "3d", "1w", "12h"] and r["ok"]]
        higher = "خنثی"
        if higher_list:
            biases = [r["bias"] for r in higher_list if r["bias"] != "خنثی"]
            if biases:
                higher = max(set(biases), key=biases.count)

        rets = np.diff(np.log(df_4h['close'].values[-80:]))
        mu, sigma = np.mean(rets), np.std(rets)
        horizon = 2
        sims = np.array([price * np.exp(np.cumsum(np.random.normal(mu, sigma, horizon))) for _ in range(50)])
        mean_path = np.mean(sims, axis=0)
        short_bias = mean_path[-1] > price

        # امتیاز چندعاملی
        score, reasons, risks = calculate_signal_score(multi_nds, rsi, funding, vol_ratio, higher, short_bias)

        if higher == "صعودی" and short_bias and score >= 58:
            direction, signal, is_strong = "LONG", "🟢 LONG", True
        elif higher == "نزولی" and not short_bias and score >= 58:
            direction, signal, is_strong = "SHORT", "🔴 SHORT", True
        else:
            direction, signal, is_strong = None, "⚪️ صبر", False

        if direction:
            sl, tp = calculate_dollar_sl_tp(price, direction, atr, margin, lev, RISK_PER_TRADE)
            entry = price
        else:
            entry, sl, tp = price, price - atr*1.25, price + atr*1.25

        position_size = margin * lev
        risk_usd = abs(entry - sl) / entry * position_size
        reward_usd = abs(tp - entry) / entry * position_size
        rr = reward_usd / risk_usd if risk_usd > 0 else 0

        fibs = calc_fib(df_4h)
        nds_4h = analyze_nds(df_4h)
        buf = create_pro_chart(df_4h, f"𓄂 ηοsτrαμεhrδαδ𓆃 | {symbol} | 4H | {higher}",
                               fibs=fibs, entry=entry, sl=sl, tp=tp, sims=sims, mean_path=mean_path,
                               nds_info=nds_4h, bias=higher)

        base = symbol.replace("USDT", "")
        glass = f"https://www.coinglass.com/currencies/{base}/funding-rate"
        if funding is not None:
            fund_line = f"`{funding:.4f}%`"
            if next_fund: fund_line += f" | بعدی: `{next_fund}`"
        else:
            fund_line = f"[Coinglass]({glass})"

        plan = (
            f"📋 **پلن | {symbol}**\n\n"
            f"سیگنال: **{signal}**\n"
            f"امتیاز قدرت: `{score}/100`\n"
            f"ورود: `{entry:.4f}`\nSL: `{sl:.4f}`\nTP: `{tp:.4f}`\n\n"
            f"💵 ریسک: `${risk_usd:.2f}` | سود: `${reward_usd:.2f}` | R:R `1:{rr:.2f}`\n\n"
            f"💰 فاندینگ: {fund_line}\n"
            f"📡 منابع: {format_price_sources(multi)}"
        )
        if open_pos:
            plan += f"\n\n📌 پوزیشن باز دارید (`{open_pos['symbol']}`)."

        await update.message.reply_photo(photo=buf, caption=plan, parse_mode="Markdown")

        # دلیل و ریسک‌ها
        reason_text = "📌 **دلیل سیگنال:**\n"
        if reasons:
            for r in reasons:
                reason_text += f"• {r}\n"
        else:
            reason_text += "• تأیید کافی وجود ندارد\n"
        reason_text += "\n⚠️ **ریسک‌ها:**\n"
        if risks:
            for r in risks:
                reason_text += f"• {r}\n"
        else:
            reason_text += "• ریسک خاصی شناسایی نشد\n"
        reason_text += f"\nبایاس غالب NDS: **{higher}**"
        await update.message.reply_text(reason_text, parse_mode="Markdown")

        if is_strong and direction and not open_pos:
            set_pending_signal(uid, {"symbol": symbol, "direction": direction, "entry": entry, "sl": sl, "tp": tp})
            await update.message.reply_text("❓ سیگنال قوی. برای ثبت: `/confirm`", parse_mode="Markdown")
        elif open_pos:
            await update.message.reply_text("ℹ️ پوزیشن باز دارید. فقط تحلیل نمایش داده شد.")

        await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ خطا: {str(e)[:90]}")

def main():
    if not BOT_TOKEN:
        print("BOT_TOKEN missing")
        return
    t_web = threading.Thread(target=run_web, daemon=True)
    t_web.start()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_for_monitor = Bot(token=BOT_TOKEN)
    t_monitor = threading.Thread(target=monitor_positions, args=(bot_for_monitor,), daemon=True)
    t_monitor.start()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("tools", tools))
    application.add_handler(CommandHandler("news", news_command))
    application.add_handler(CommandHandler("predict", predict_command))
    application.add_handler(CommandHandler("nds", nds_command))
    application.add_handler(CommandHandler("market", market_command))
    application.add_handler(CommandHandler("log", log_trade))
    application.add_handler(CommandHandler("performance", performance))
    application.add_handler(CommandHandler("position", position_cmd))
    application.add_handler(CommandHandler("close", close_cmd))
    application.add_handler(CommandHandler("confirm", confirm_cmd))
    application.add_handler(CommandHandler("watch", watch_cmd))
    application.add_handler(CommandHandler("unwatch", unwatch_cmd))
    application.add_handler(CommandHandler("watchlist", watchlist_cmd))
    application.add_handler(CommandHandler("adduser", adduser))
    application.add_handler(CommandHandler("removeuser", removeuser))
    application.add_handler(CommandHandler("listusers", listusers))

    print("𓄂 ηοsτrαμεhrδαδ𓆃 آماده | NDS چندتایم‌فریم + امتیاز چندعاملی")
    application.run_polling()

if __name__ == '__main__':
    main()