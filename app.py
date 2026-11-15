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
PRICE_ALERTS_FILE = "price_alerts.json"

DEFAULT_MARGIN = 5.0
DEFAULT_LEVERAGE = 8
RISK_PER_TRADE = 0.5
RATE_LIMIT_SECONDS = 40
MONITOR_INTERVAL = 120
DANGER_PERCENT = 0.35
PULLBACK_ALERT_PCT = 0.012
# سخت‌گیری سیگنال
MIN_SIGNAL_SCORE = 68
MIN_HIGHER_TF_ALIGN = 3

NDS_INTERVALS = [
    ("5m", "۵ دقیقه"), ("15m", "۱۵ دقیقه"), ("30m", "۳۰ دقیقه"),
    ("1h", "۱ ساعت"), ("2h", "۲ ساعت"), ("4h", "۴ ساعت"),
    ("6h", "۶ ساعت"), ("8h", "۸ ساعت"), ("12h", "۱۲ ساعت"),
    ("1d", "۱ روز"), ("3d", "۳ روز"), ("1w", "۱ هفته"), ("1M", "۱ ماه"),
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

# ===================== هشدار قیمت سفارشی =====================

def get_user_price_alerts(uid):
    data = load_json(PRICE_ALERTS_FILE)
    return data.get(str(uid), [])

def add_price_alert(uid, symbol, target, condition):
    """condition: above | below"""
    data = load_json(PRICE_ALERTS_FILE)
    alerts = data.get(str(uid), [])
    alerts.append({
        "id": int(time.time() * 1000) % 100000000,
        "symbol": symbol.upper(),
        "target": float(target),
        "condition": condition,
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    data[str(uid)] = alerts
    save_json(PRICE_ALERTS_FILE, data)
    return alerts[-1]

def remove_price_alert(uid, alert_id):
    data = load_json(PRICE_ALERTS_FILE)
    alerts = data.get(str(uid), [])
    new_alerts = [a for a in alerts if int(a.get("id", -1)) != int(alert_id)]
    data[str(uid)] = new_alerts
    save_json(PRICE_ALERTS_FILE, data)
    return len(alerts) - len(new_alerts)

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
            # --- پوزیشن‌ها ---
            positions = load_json(POSITION_FILE)
            alerts = load_json(ALERT_STATE_FILE)
            for uid_str, pos in list(positions.items()):
                try:
                    uid = int(uid_str)
                    symbol = pos["symbol"]
                    direction = pos["direction"]
                    entry = float(pos["entry"])
                    sl = float(pos["sl"])
                    tp = float(pos["tp"])
                    price = get_current_price(symbol)
                    if price is None:
                        continue
                    user_alerts = alerts.get(uid_str, {})

                    hit_sl = (direction == "LONG" and price <= sl) or (direction == "SHORT" and price >= sl)
                    hit_tp = (direction == "LONG" and price >= tp) or (direction == "SHORT" and price <= tp)

                    if hit_sl:
                        close_position(uid)
                        try:
                            bot.send_message(chat_id=uid, text=(
                                f"🛑 **پوزیشن خودکار بسته شد (حد ضرر)**\n\n`{symbol}` | {direction}\n"
                                f"قیمت: `{price:.4f}` | SL: `{sl:.4f}` | ورود: `{entry:.4f}`"
                            ), parse_mode="Markdown")
                        except: pass
                        continue
                    if hit_tp:
                        close_position(uid)
                        try:
                            bot.send_message(chat_id=uid, text=(
                                f"🎯 **پوزیشن خودکار بسته شد (حد سود)**\n\n`{symbol}` | {direction}\n"
                                f"قیمت: `{price:.4f}` | TP: `{tp:.4f}` | ورود: `{entry:.4f}`"
                            ), parse_mode="Markdown")
                        except: pass
                        continue

                    if direction == "LONG":
                        danger = price <= entry - (entry - sl) * (1 - DANGER_PERCENT)
                        near_tp = price >= entry + (tp - entry) * 0.7
                        pullback = entry > 0 and (entry - price) / entry >= PULLBACK_ALERT_PCT and price > sl
                    else:
                        danger = price >= entry + (sl - entry) * (1 - DANGER_PERCENT)
                        near_tp = price <= entry - (entry - tp) * 0.7
                        pullback = entry > 0 and (price - entry) / entry >= PULLBACK_ALERT_PCT and price < sl

                    if danger and not user_alerts.get("sl_warned"):
                        try:
                            bot.send_message(chat_id=uid, text=f"⚠️ **نزدیک به محدوده خطر**\n\n`{symbol}` | {direction}\nقیمت: `{price:.4f}`\nSL: `{sl:.4f}`", parse_mode="Markdown")
                            user_alerts["sl_warned"] = True
                        except: pass
                    if near_tp and not user_alerts.get("tp_warned"):
                        try:
                            bot.send_message(chat_id=uid, text=f"🎯 **نزدیک به حد سود**\n\n`{symbol}` | {direction}\nقیمت: `{price:.4f}`\nTP: `{tp:.4f}`", parse_mode="Markdown")
                            user_alerts["tp_warned"] = True
                        except: pass
                    if pullback and not user_alerts.get("pullback_warned"):
                        try:
                            bot.send_message(chat_id=uid, text=f"🔄 **هشدار پولبک**\n\n`{symbol}` | {direction}\nقیمت: `{price:.4f}` | ورود: `{entry:.4f}`", parse_mode="Markdown")
                            user_alerts["pullback_warned"] = True
                        except: pass
                    alerts[uid_str] = user_alerts
                except Exception as e:
                    print("Monitor pos:", e)
            save_json(ALERT_STATE_FILE, alerts)

            # --- هشدارهای قیمت سفارشی ---
            all_alerts = load_json(PRICE_ALERTS_FILE)
            changed = False
            for uid_str, user_alerts_list in list(all_alerts.items()):
                remaining = []
                for a in user_alerts_list:
                    try:
                        price = get_current_price(a["symbol"])
                        if price is None:
                            remaining.append(a)
                            continue
                        target = float(a["target"])
                        cond = a.get("condition", "above")
                        triggered = (cond == "above" and price >= target) or (cond == "below" and price <= target)
                        if triggered:
                            try:
                                bot.send_message(
                                    chat_id=int(uid_str),
                                    text=(
                                        f"🚨 **هشدار قیمت**\n\n"
                                        f"`{a['symbol']}` به `{price:.4f}` رسید\n"
                                        f"شرط: {('بالای' if cond=='above' else 'زیر')} `{target:.4f}`"
                                    ),
                                    parse_mode="Markdown"
                                )
                            except: pass
                            changed = True
                        else:
                            remaining.append(a)
                    except Exception as e:
                        print("Price alert:", e)
                        remaining.append(a)
                all_alerts[uid_str] = remaining
            if changed:
                save_json(PRICE_ALERTS_FILE, all_alerts)

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

def add_macd(df, fast=12, slow=26, signal=9):
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    df['macd'] = ema_fast - ema_slow
    df['macd_signal'] = df['macd'].ewm(span=signal, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    return df

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
    df = add_macd(df)
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

def detect_order_blocks(df, lookback=40):
    if df is None or len(df) < lookback + 5:
        return []
    sub = df.tail(lookback).copy()
    atr = float(calc_atr(df).iloc[-1] or 0)
    blocks = []
    for i in range(2, len(sub)-2):
        body = abs(sub['close'].iloc[i] - sub['open'].iloc[i])
        rng = sub['high'].iloc[i] - sub['low'].iloc[i]
        if rng <= 0 or atr <= 0:
            continue
        if body > atr * 0.8 and body / rng > 0.55:
            direction = "bullish" if sub['close'].iloc[i] > sub['open'].iloc[i] else "bearish"
            if direction == "bullish" and sub['low'].iloc[i+1:i+3].min() >= sub['low'].iloc[i] * 0.998:
                blocks.append({"type": "bullish_ob", "low": float(sub['low'].iloc[i]), "high": float(sub['high'].iloc[i])})
            if direction == "bearish" and sub['high'].iloc[i+1:i+3].max() <= sub['high'].iloc[i] * 1.002:
                blocks.append({"type": "bearish_ob", "low": float(sub['low'].iloc[i]), "high": float(sub['high'].iloc[i])})
    return blocks[-3:]

def analyze_nds(df):
    if df is None or len(df) < 60:
        return {'bias': 'خنثی', 'cycle': '—', 'last_high': None, 'last_low': None, 'nodes': [], 'tf_low': None, 'tf_high': None}
    nodes = find_nodes(df)
    price = float(df['close'].iloc[-1])
    tf_low = float(df['low'].tail(80).min())
    tf_high = float(df['high'].tail(80).max())
    if len(nodes) < 4:
        return {'bias': 'خنثی', 'cycle': 'ناقص', 'last_high': None, 'last_low': None, 'nodes': nodes, 'tf_low': tf_low, 'tf_high': tf_high}
    highs = [n for n in nodes if n['type'] == 'high']
    lows = [n for n in nodes if n['type'] == 'low']
    last_high = highs[-1]['price'] if highs else price
    prev_high = highs[-2]['price'] if len(highs) > 1 else last_high
    last_low = lows[-1]['price'] if lows else price
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
        'nodes': nodes, 'tf_low': tf_low, 'tf_high': tf_high
    }

def analyze_nds_multi(symbol, current_price=None):
    results = []
    for interval, label in NDS_INTERVALS:
        limit = 200 if interval in ["1d", "3d", "1w", "1M"] else 150
        df = fetch_klines(symbol, interval, limit)
        if df is None or len(df) < 50:
            results.append({"interval": interval, "label": label, "bias": "—", "cycle": "داده ناکافی", "ok": False})
            continue
        info = analyze_nds(df)
        tf_low = info.get("tf_low")
        tf_high = info.get("tf_high")
        dist_pct = None
        range_val = None
        if tf_low is not None and current_price:
            dist_pct = (current_price - tf_low) / current_price * 100
        if tf_low is not None and tf_high is not None:
            range_val = tf_high - tf_low
        results.append({
            "interval": interval, "label": label,
            "bias": info["bias"], "cycle": info["cycle"],
            "last_high": info.get("last_high"), "last_low": info.get("last_low"),
            "tf_low": tf_low, "tf_high": tf_high,
            "dist_pct": dist_pct, "range_val": range_val,
            "ok": True
        })
    return results

def calculate_signal_score(nds_results, rsi, funding, volume_ratio, higher_bias, short_bias, macd_hist=None):
    """امتیاز سخت‌گیرانه‌تر برای سیگنال پایدار"""
    score = 50.0
    reasons, risks = [], []

    higher_tfs = [r for r in nds_results if r["interval"] in ["1d", "3d", "1w", "1M", "12h", "8h"] and r["ok"]]
    mid_tfs = [r for r in nds_results if r["interval"] in ["4h", "6h", "2h", "1h"] and r["ok"]]
    lower_tfs = [r for r in nds_results if r["interval"] in ["30m", "15m", "5m"] and r["ok"]]

    bullish_h = sum(1 for r in higher_tfs if r["bias"] == "صعودی")
    bearish_h = sum(1 for r in higher_tfs if r["bias"] == "نزولی")
    bullish_m = sum(1 for r in mid_tfs if r["bias"] == "صعودی")
    bearish_m = sum(1 for r in mid_tfs if r["bias"] == "نزولی")

    if bullish_h >= MIN_HIGHER_TF_ALIGN:
        score += 20
        reasons.append(f"هم‌راستایی تایم‌فریم بالاتر صعودی ({bullish_h} مورد)")
        higher_bias = "صعودی"
    elif bearish_h >= MIN_HIGHER_TF_ALIGN:
        score -= 20
        reasons.append(f"هم‌راستایی تایم‌فریم بالاتر نزولی ({bearish_h} مورد)")
        higher_bias = "نزولی"
    else:
        risks.append("عدم هم‌راستایی کافی تایم‌فریم‌های بالاتر — سیگنال ضعیف")
        score -= 10

    if higher_bias == "صعودی" and bullish_m >= 2:
        score += 12
        reasons.append("تأیید میان‌مدت صعودی")
    elif higher_bias == "نزولی" and bearish_m >= 2:
        score += 12
        reasons.append("تأیید میان‌مدت نزولی")
    elif higher_bias != "خنثی":
        score -= 8
        risks.append("واگرایی میان‌مدت با تایم‌فریم بالاتر")

    # تایم‌فریم پایین نباید خلاف جهت قوی باشد
    if higher_bias == "صعودی":
        against = sum(1 for r in lower_tfs if r["bias"] == "نزولی")
        if against >= 2:
            score -= 7
            risks.append("فشار نزولی در تایم‌فریم پایین")
    elif higher_bias == "نزولی":
        against = sum(1 for r in lower_tfs if r["bias"] == "صعودی")
        if against >= 2:
            score -= 7
            risks.append("فشار صعودی در تایم‌فریم پایین")

    if 40 <= rsi <= 60:
        score += 6
        reasons.append(f"RSI متعادل ({rsi:.1f})")
    elif rsi > 72:
        score -= 10
        risks.append(f"RSI اشباع خرید ({rsi:.1f})")
    elif rsi < 28:
        score -= 10
        risks.append(f"RSI اشباع فروش ({rsi:.1f})")

    if funding is not None:
        if higher_bias == "صعودی" and funding < 0:
            score += 4
            reasons.append(f"فاندینگ منفی هم‌راستا ({funding:.4f}%)")
        elif higher_bias == "نزولی" and funding > 0.02:
            score += 4
            reasons.append(f"فاندینگ مثبت هم‌راستا با شورت ({funding:.4f}%)")
        elif abs(funding) > 0.08:
            score -= 5
            risks.append(f"فاندینگ افراطی ({funding:.4f}%)")

    if volume_ratio is not None:
        if volume_ratio >= 1.15:
            score += 4
            reasons.append(f"حجم نسبی قابل قبول ({volume_ratio:.2f}x)")
        elif volume_ratio < 0.55:
            score -= 5
            risks.append(f"حجم ضعیف ({volume_ratio:.2f}x)")

    if macd_hist is not None:
        if higher_bias == "صعودی" and macd_hist > 0:
            score += 5
            reasons.append("MACD هیستوگرام مثبت")
        elif higher_bias == "نزولی" and macd_hist < 0:
            score += 5
            reasons.append("MACD هیستوگرام منفی")
        elif higher_bias != "خنثی":
            score -= 4
            risks.append("MACD خلاف بایاس غالب")

    # مونت‌کارلو فقط به‌عنوان تأیید خفیف
    if higher_bias == "صعودی" and short_bias:
        score += 4
        reasons.append("تأیید خفیف افق کوتاه‌مدت")
    elif higher_bias == "نزولی" and not short_bias:
        score += 4
        reasons.append("تأیید خفیف افق کوتاه‌مدت")
    elif higher_bias != "خنثی":
        score -= 6
        risks.append("افق کوتاه‌مدت با بایاس غالب هم‌خوان نیست")

    return max(0, min(100, round(score, 1))), reasons, risks, higher_bias

def calculate_dollar_sl_tp(entry, direction, atr, margin=5.0, leverage=8, risk_dollar=0.5, structure_low=None, structure_high=None):
    """تارگت واقع‌بینانه‌تر بر اساس ATR + ساختار"""
    position_size = margin * leverage
    price_risk = (risk_dollar / position_size) * entry
    atr_distance = atr * 1.6  # کمی بازتر برای پایداری بیشتر
    final_distance = max(price_risk, atr_distance * 0.85)

    if direction == "LONG":
        sl = entry - final_distance
        if structure_low and structure_low < entry:
            # SL کمی زیر ساختار
            sl = min(sl, structure_low - atr * 0.15)
        tp = entry + final_distance * 2.0
        if structure_high and structure_high > entry:
            # TP را تا نزدیک مقاومت بکش، نه فراتر از منطق
            tp = min(tp, structure_high - atr * 0.1) if structure_high - entry > final_distance else tp
    else:
        sl = entry + final_distance
        if structure_high and structure_high > entry:
            sl = max(sl, structure_high + atr * 0.15)
        tp = entry - final_distance * 2.0
        if structure_low and structure_low < entry:
            tp = max(tp, structure_low + atr * 0.1) if entry - structure_low > final_distance else tp
    return round(sl, 4), round(tp, 4)

def draw_candles(ax, df, width=0.55):
    opens, highs, lows, closes = df['open'].values, df['high'].values, df['low'].values, df['close'].values
    for i in range(len(df)):
        color = '#2ed573' if closes[i] >= opens[i] else '#ff4757'
        ax.plot([i, i], [lows[i], highs[i]], color=color, lw=0.9, zorder=3)
        bottom = min(opens[i], closes[i])
        height = abs(closes[i] - opens[i]) or (highs[i]-lows[i])*0.02 or 1e-8
        ax.add_patch(Rectangle((i - width/2, bottom), width, height, facecolor=color, edgecolor=color, lw=0.4, zorder=4))

def create_pro_chart(df, title, fibs=None, entry=None, sl=None, tp=None,
                     sims=None, mean_path=None, nds_info=None, bias=None, mark_hugs=False):
    plot_df = df.tail(100).copy().reset_index(drop=True)
    n = len(plot_df)
    fig = plt.figure(figsize=(14, 9.4), facecolor='#0d0d0d')
    gs = GridSpec(4, 1, height_ratios=[3.3, 0.95, 0.95, 0.04], hspace=0.07)
    ax = fig.add_subplot(gs[0])
    ax_macd = fig.add_subplot(gs[1], sharex=ax)
    ax_rsi = fig.add_subplot(gs[2], sharex=ax)
    for a in [ax, ax_macd, ax_rsi]:
        a.set_facecolor('#0d0d0d')
        a.tick_params(colors='#cccccc', labelsize=7.2)
        for spine in a.spines.values():
            spine.set_color('#333333')

    draw_candles(ax, plot_df)
    ax.plot(plot_df['sma20'].values, color='#da70d6', lw=1.0, label='MA20', alpha=0.9)
    ax.plot(plot_df['sma50'].values, color='#ffa500', lw=1.1, label='MA50')
    if 'sma200' in plot_df.columns:
        ax.plot(plot_df['sma200'].values, color='#1e90ff', lw=1.15, label='MA200', alpha=0.85)
    ax.plot(plot_df['ema20'].values, color='#00fa9a', lw=0.95, label='EMA20', alpha=0.85)
    ax.plot(plot_df['ema50'].values, color='#ff6347', lw=1.0, label='EMA50', alpha=0.85)
    ax.plot(plot_df['bb_upper'].values, color='#888888', lw=0.8, ls='--', alpha=0.65)
    ax.plot(plot_df['bb_lower'].values, color='#888888', lw=0.8, ls='--', alpha=0.65)
    if 'senkou_a' in plot_df.columns:
        ax.fill_between(range(n), plot_df['senkou_a'].values, plot_df['senkou_b'].values,
                        where=plot_df['senkou_a'] >= plot_df['senkou_b'], color='#00ff7f', alpha=0.11)
        ax.fill_between(range(n), plot_df['senkou_a'].values, plot_df['senkou_b'].values,
                        where=plot_df['senkou_a'] < plot_df['senkou_b'], color='#ff69b4', alpha=0.11)
    if fibs:
        colors = {'0.0':'#ffffff', '0.236':'#ff6b6b', '0.382':'#ff9f43', '0.5':'#feca57',
                  '0.618':'#1dd1a1', '0.786':'#54a0ff', '1.0':'#a55eea'}
        for k, v in fibs.items():
            c = colors.get(k, '#aaaaaa')
            ax.axhline(v, color=c, ls=':', lw=0.85, alpha=0.8)
            ax.text(n + 1.0, v, f'{k} {v:.2f}', fontsize=6.6, va='center', color=c)
    if entry: ax.axhline(entry, color='#1e90ff', lw=1.25, label='Entry')
    if sl: ax.axhline(sl, color='#ff4757', lw=1.25, label='SL')
    if tp: ax.axhline(tp, color='#2ed573', lw=1.25, label='TP')
    if sims is not None and mean_path is not None:
        last = n - 1
        horizon = len(mean_path)
        for s in sims[::3]:
            ax.plot(range(last, last+horizon), s[:horizon], color='#ffffff', alpha=0.15, lw=0.7)
        ax.plot(range(last, last+horizon), mean_path, color='#ffffff', lw=1.8, label='Pred 4-6h', zorder=6)

    if nds_info and mark_hugs:
        nodes = nds_info.get('nodes', [])
        offset = len(df) - n
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
            size = max(1.2, n * 0.007)
            ax.add_patch(Rectangle((x - size/2, y - y*0.004), size, y*0.008, linewidth=1.2, edgecolor=color, facecolor=color, alpha=0.85, zorder=11))
            ax.text(x, y + y*0.012, label, fontsize=6.6, ha='center', color=color, fontweight='bold', zorder=12)

    ax.set_title(title, color='white', fontsize=11.5, pad=8)
    ax.legend(loc='upper left', fontsize=6.5, facecolor='#1a1a1a', edgecolor='#333', labelcolor='white', ncol=3)
    ax.grid(True, alpha=0.16, color='#444')
    ax.yaxis.tick_right()
    ax.yaxis.set_major_formatter(FormatStrFormatter('%.2f'))
    ax.set_xlim(-1, n+12)

    if 'macd' in plot_df.columns:
        hist = plot_df['macd_hist'].values
        colors_h = ['#2ed573' if v >= 0 else '#ff4757' for v in hist]
        ax_macd.bar(range(n), hist, color=colors_h, alpha=0.75, width=0.7)
        ax_macd.plot(plot_df['macd'].values, color='#00bfff', lw=1.1, label='MACD')
        ax_macd.plot(plot_df['macd_signal'].values, color='#ffa500', lw=1.0, label='Signal')
        ax_macd.axhline(0, color='#666', lw=0.7, alpha=0.6)
        ax_macd.legend(loc='upper left', fontsize=6.2, facecolor='#1a1a1a', labelcolor='white')
    ax_macd.set_ylabel('MACD', color='#aaaaaa', fontsize=7.2)
    ax_macd.grid(True, alpha=0.14, color='#444')

    ax_rsi.plot(plot_df['rsi'].values, color='#e056fd', lw=1.1)
    ax_rsi.axhline(70, color='#ff4757', ls='--', lw=0.7, alpha=0.6)
    ax_rsi.axhline(30, color='#2ed573', ls='--', lw=0.7, alpha=0.6)
    ax_rsi.axhline(50, color='#888888', ls=':', lw=0.6, alpha=0.5)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel('RSI', color='#aaaaaa', fontsize=7.2)
    ax_rsi.grid(True, alpha=0.14, color='#444')
    plt.setp(ax.get_xticklabels(), visible=False)
    plt.setp(ax_macd.get_xticklabels(), visible=False)

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
        "`/predict` `/nds` `/market`\n"
        "`/alert` `/alerts` `/removealert`\n"
        "`/position` `/close` `/confirm`\n"
        "`/watch` `/watchlist` `/unwatch`\n"
        "`/log` `/performance`\n"
        "`/buy` `/status` `/tools` `/news`",
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
        f"• [CoinMarketCal](https://coinmarketcal.com)"
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)

async def alert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /alert BTCUSDT 95000 above  یا  /alert BTCUSDT 90000 below """
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if len(context.args) < 3:
        await update.message.reply_text(
            "فرمت:\n`/alert نماد قیمت above|below`\n\n"
            "مثال:\n`/alert BTCUSDT 95000 above`\n`/alert ETHUSDT 2800 below`",
            parse_mode="Markdown"
        )
        return
    try:
        symbol = context.args[0].upper()
        target = float(context.args[1])
        condition = context.args[2].lower()
        if condition not in ("above", "below"):
            await update.message.reply_text("شرط باید `above` یا `below` باشد.")
            return
        a = add_price_alert(uid, symbol, target, condition)
        await update.message.reply_text(
            f"✅ هشدار ثبت شد\n\nID: `{a['id']}`\n`{symbol}` {condition} `{target}`\n\nلیست: `/alerts`",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"خطا: {str(e)[:80]}")

async def alerts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    alerts = get_user_price_alerts(uid)
    if not alerts:
        await update.message.reply_text("هشداری ثبت نشده.\n`/alert BTCUSDT 95000 above`")
        return
    lines = ["🚨 **هشدارهای قیمت**\n"]
    for a in alerts:
        lines.append(f"• ID `{a['id']}` | `{a['symbol']}` {a['condition']} `{a['target']}`")
    lines.append("\nحذف: `/removealert ID`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def removealert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update): return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if not context.args:
        await update.message.reply_text("فرمت: `/removealert ID`", parse_mode="Markdown")
        return
    try:
        n = remove_price_alert(uid, context.args[0])
        if n:
            await update.message.reply_text("✅ هشدار حذف شد.")
        else:
            await update.message.reply_text("ID پیدا نشد.")
    except Exception as e:
        await update.message.reply_text(str(e)[:80])

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
        f"ورود: `{pending['entry']}` | SL: `{pending['sl']}` | TP: `{pending['tp']}`\n\n"
        f"با رسیدن به SL/TP خودکار بسته می‌شود.\n`/position` | `/close`",
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
        text += f"قیمت فعلی: `{price:.4f}`\n"
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
    """فقط نمادهای واچ‌لیست کاربر"""
    if not is_private(update): return
    uid = update.effective_user.id
    if not check_rate(uid):
        await update.message.reply_text("⏳ کمی صبر کنید.")
        return
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    wl = get_watchlist(uid)
    if not wl:
        await update.message.reply_text(
            "واچ‌لیست خالی است.\nابتدا با `/watch BTCUSDT ETHUSDT` اضافه کنید.\nسپس `/market` بزنید.",
            parse_mode="Markdown"
        )
        return
    status = await update.message.reply_text("⏳ وضعیت واچ‌لیست ...")
    try:
        lines = ["🌐 **وضعیت بازار (فقط واچ‌لیست)**\n"]
        for symbol in wl:
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
    status = await update.message.reply_text(f"⏳ تحلیل NDS `{symbol}` ...")

    try:
        df_4h = fetch_klines(symbol, "4h", 300)
        if df_4h is None or len(df_4h) < 80:
            await status.edit_text("❌ خطا در دریافت داده.")
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
        obs = detect_order_blocks(df_4h)
        multi_nds = analyze_nds_multi(symbol, current_price=price)

        higher_list = [r for r in multi_nds if r["interval"] in ["1d", "3d", "1w", "1M", "12h"] and r["ok"]]
        higher = nds_4h['bias']
        if higher_list:
            biases = [r["bias"] for r in higher_list if r["bias"] != "خنثی"]
            if biases:
                higher = max(set(biases), key=biases.count)

        buf = create_pro_chart(df_4h, f"𓄂 ηοsτrαμεhrδαδ𓆃 | {symbol} | 4H | {higher}",
                               fibs=fibs, nds_info=nds_4h, bias=higher, mark_hugs=True)

        caption = (
            f"🧬 **NDS | {symbol}**\n\n"
            f"**بایاس غالب:** {higher}\n\n"
            f"**حمایت و مقاومت پیش‌رو (۴H)**\n"
        )
        if last_high: caption += f"• مقاومت: `{last_high:.4f}`\n"
        if last_low: caption += f"• حمایت: `{last_low:.4f}`\n"
        if obs:
            caption += "\n**اوردر بلاک تقریبی**\n"
            for ob in obs:
                tag = "صعودی" if ob["type"] == "bullish_ob" else "نزولی"
                caption += f"• OB {tag}: `{ob['low']:.4f}` – `{ob['high']:.4f}`\n"
        caption += f"\nRSI: `{rsi:.1f}` | ATR: `{atr:.4f}` | قیمت: `{price:.4f}`\n"
        caption += f"منابع: {format_price_sources(multi)}"
        await update.message.reply_photo(photo=buf, caption=caption, parse_mode="Markdown")

        lines = ["📊 **NDS تایم‌فریم‌ها**\nکف | فاصله% از قیمت آنی | (سقف−کف)\n"]
        for r in multi_nds:
            if not r["ok"]:
                lines.append(f"• {r['label']}: داده ناکافی")
                continue
            emoji = "🟢" if r["bias"] == "صعودی" else "🔴" if r["bias"] == "نزولی" else "⚪️"
            low_txt = f"{r['tf_low']:.4f}" if r.get("tf_low") is not None else "—"
            dist = f"{r['dist_pct']:+.2f}%" if r.get("dist_pct") is not None else "—"
            rng = f"{r['range_val']:.4f}" if r.get("range_val") is not None else "—"
            lines.append(f"{emoji} {r['label']}: {r['cycle']}→**{r['bias']}** | کف `{low_txt}` | {dist} | ({rng})")
        mid = len(lines) // 2 + 1
        await update.message.reply_text("\n".join(lines[:mid]), parse_mode="Markdown")
        if len(lines) > mid:
            await update.message.reply_text("\n".join(lines[mid:]), parse_mode="Markdown")

        if higher == "صعودی":
            cont = "ساختار غالب صعودی است. حمایت‌های پیش‌رو و احتمال پولبک را رصد کنید."
        elif higher == "نزولی":
            cont = "ساختار غالب نزولی است. مقاومت‌های پیش‌رو و احتمال پولبک را رصد کنید."
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
    status = await update.message.reply_text(f"⏳ تحلیل سخت‌گیرانه `{symbol}` ...")

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
        macd_hist = float(df_4h['macd_hist'].iloc[-1]) if 'macd_hist' in df_4h.columns else None
        funding_info = get_funding_info(symbol)
        funding = funding_info["rate"]
        next_fund = funding_info["next_time"]

        multi_nds = analyze_nds_multi(symbol, current_price=price)
        rets = np.diff(np.log(df_4h['close'].values[-80:]))
        mu, sigma = np.mean(rets), np.std(rets)
        horizon = 2
        sims = np.array([price * np.exp(np.cumsum(np.random.normal(mu, sigma, horizon))) for _ in range(40)])
        mean_path = np.mean(sims, axis=0)
        short_bias = mean_path[-1] > price

        score, reasons, risks, higher = calculate_signal_score(
            multi_nds, rsi, funding, vol_ratio, "خنثی", short_bias, macd_hist
        )
        nds_4h = analyze_nds(df_4h)
        obs = detect_order_blocks(df_4h)
        last_high, last_low = nds_4h.get('last_high'), nds_4h.get('last_low')

        # فقط با امتیاز بالا و هم‌راستایی واقعی سیگنال بده
        if higher == "صعودی" and score >= MIN_SIGNAL_SCORE and short_bias:
            direction, signal, is_strong = "LONG", "🟢 LONG", True
        elif higher == "نزولی" and score >= MIN_SIGNAL_SCORE and not short_bias:
            direction, signal, is_strong = "SHORT", "🔴 SHORT", True
        else:
            direction, signal, is_strong = None, "⚪️ صبر", False
            if score < MIN_SIGNAL_SCORE:
                risks.append(f"امتیاز زیر آستانه سخت‌گیرانه ({score} < {MIN_SIGNAL_SCORE})")

        if direction:
            sl, tp = calculate_dollar_sl_tp(
                price, direction, atr, margin, lev, RISK_PER_TRADE,
                structure_low=last_low, structure_high=last_high
            )
            entry = price
        else:
            entry, sl, tp = price, price - atr*1.6, price + atr*1.6

        position_size = margin * lev
        risk_usd = abs(entry - sl) / entry * position_size if entry else 0
        reward_usd = abs(tp - entry) / entry * position_size if entry else 0
        rr = reward_usd / risk_usd if risk_usd > 0 else 0

        fibs = calc_fib(df_4h)
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
            f"📋 **پلن سخت‌گیرانه | {symbol}**\n\n"
            f"سیگنال: **{signal}**\n"
            f"امتیاز قدرت: `{score}/100` (حداقل `{MIN_SIGNAL_SCORE}`)\n"
            f"ورود: `{entry:.4f}`\nSL: `{sl:.4f}`\nTP: `{tp:.4f}`\n\n"
            f"💵 ریسک: `${risk_usd:.2f}` | سود: `${reward_usd:.2f}` | R:R `1:{rr:.2f}`\n\n"
            f"**حمایت/مقاومت پیش‌رو**\n"
        )
        if last_high: plan += f"• مقاومت: `{last_high:.4f}`\n"
        if last_low: plan += f"• حمایت: `{last_low:.4f}`\n"
        if obs:
            plan += "\n**اوردر بلاک تقریبی**\n"
            for ob in obs:
                tag = "صعودی" if ob["type"] == "bullish_ob" else "نزولی"
                plan += f"• OB {tag}: `{ob['low']:.4f}`–`{ob['high']:.4f}`\n"
        plan += f"\n💰 فاندینگ: {fund_line}\n📡 منابع: {format_price_sources(multi)}"
        if open_pos:
            plan += f"\n\n📌 پوزیشن باز دارید (`{open_pos['symbol']}`)."

        await update.message.reply_photo(photo=buf, caption=plan, parse_mode="Markdown")

        reason_text = "📌 **دلیل سیگنال:**\n"
        reason_text += "\n".join([f"• {r}" for r in reasons]) if reasons else "• تأیید کافی نیست"
        reason_text += "\n\n⚠️ **ریسک‌ها:**\n"
        reason_text += "\n".join([f"• {r}" for r in risks]) if risks else "• ریسک خاصی شناسایی نشد"
        reason_text += f"\n\nبایاس غالب: **{higher}**\n"
        reason_text += "سیگنال فقط با هم‌راستایی چندتایم‌فریم و امتیاز کافی صادر می‌شود."
        await update.message.reply_text(reason_text, parse_mode="Markdown")

        if is_strong and direction and not open_pos:
            set_pending_signal(uid, {"symbol": symbol, "direction": direction, "entry": entry, "sl": sl, "tp": tp})
            await update.message.reply_text(
                "❓ سیگنال با فیلتر سخت‌گیرانه تأیید شد.\nبرای ثبت: `/confirm`",
                parse_mode="Markdown"
            )
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
    application.add_handler(CommandHandler("alert", alert_cmd))
    application.add_handler(CommandHandler("alerts", alerts_cmd))
    application.add_handler(CommandHandler("removealert", removealert_cmd))
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

    print("𓄂 ηοsτrαμεhrδαδ𓆃 آماده | مسیر A کامل")
    application.run_polling()

if __name__ == '__main__':
    main()