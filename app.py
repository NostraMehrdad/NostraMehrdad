import os
import json
import threading
import io
import time
import html
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
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

@app_web.route("/")
def home():
    return "𓄂 ηοsτrαμεhrδαδ𓆃 | Kronos Professional is active"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
COINGLASS_API_KEY = os.environ.get("COINGLASS_API_KEY", "").strip()

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
ONCHAIN_CACHE_FILE = "onchain_btc_cache.json"

DEFAULT_MARGIN = 5.0
DEFAULT_LEVERAGE = 8
RISK_PER_TRADE = 0.5
RATE_LIMIT_SECONDS = 40
MONITOR_INTERVAL = 120
DANGER_PERCENT = 0.35
PULLBACK_ALERT_PCT = 0.012
MIN_SIGNAL_SCORE = 62
MIN_RANGE_MOVE_PCT = 3.0
NDS_86 = 0.864
ONCHAIN_CACHE_HOURS = 18

NDS_INTERVALS = [
    ("15m", "۱۵ دقیقه"),
    ("2h", "۲ ساعت"),
    ("4h", "۴ ساعت"),
    ("8h", "۸ ساعت"),
    ("12h", "۱۲ ساعت"),
    ("1d", "روزانه"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
user_last_request = defaultdict(float)

def esc(x):
    return html.escape(str(x) if x is not None else "")

def p1(x):
    try:
        return f"{float(x):.1f}"
    except Exception:
        return str(x)

def load_json(path):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print("Save error:", e)

def is_admin(uid):
    return uid in ADMIN_IDS

def is_subscribed(uid):
    users = load_json(USERS_FILE)
    u = users.get(str(uid))
    if not u:
        return False
    return datetime.now(timezone.utc) < datetime.fromisoformat(u["expiry"])

def remaining_days(uid):
    users = load_json(USERS_FILE)
    u = users.get(str(uid))
    if not u:
        return 0
    return max(0, (datetime.fromisoformat(u["expiry"]) - datetime.now(timezone.utc)).days)

def check_rate(uid):
    now = time.time()
    if now - user_last_request[uid] < RATE_LIMIT_SECONDS:
        return False
    user_last_request[uid] = now
    return True

def is_private(update):
    return update.effective_chat.type == "private"

def get_open_position(uid):
    return load_json(POSITION_FILE).get(str(uid))

def set_open_position(uid, symbol, direction, entry, sl, tp):
    data = load_json(POSITION_FILE)
    data[str(uid)] = {
        "symbol": symbol, "direction": direction,
        "entry": entry, "sl": sl, "tp": tp,
        "opened_at": datetime.now(timezone.utc).isoformat(),
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

def get_user_price_alerts(uid):
    return load_json(PRICE_ALERTS_FILE).get(str(uid), [])

def add_price_alert(uid, symbol, target, condition):
    data = load_json(PRICE_ALERTS_FILE)
    alerts = data.get(str(uid), [])
    alerts.append({
        "id": int(time.time() * 1000) % 100000000,
        "symbol": symbol.upper(),
        "target": float(target),
        "condition": condition,
        "created_at": datetime.now(timezone.utc).isoformat(),
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

# -------------------- داده بازار --------------------

def get_binance_price(symbol):
    try:
        url = f"https://data-api.binance.vision/api/v3/ticker/price?symbol={symbol}"
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            return float(r.json()["price"])
    except Exception:
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
    if multi["binance"]:
        parts.append(f"Binance: <code>{p1(multi['binance'])}</code>")
    if multi["coinbase"]:
        parts.append(f"Coinbase: <code>{p1(multi['coinbase'])}</code>")
    if multi["kraken"]:
        parts.append(f"Kraken: <code>{p1(multi['kraken'])}</code>")
    if multi["consensus"]:
        parts.append(f"اجماع: <code>{p1(multi['consensus'])}</code>")
    if multi.get("spread_pct") and multi["spread_pct"] > 0.15:
        parts.append(f"⚠️ اختلاف: <code>{multi['spread_pct']:.2f}%</code>")
    return " | ".join(parts) if parts else "قیمت در دسترس نیست"

def get_funding_info(symbol):
    result = {"rate": None, "next_time": None, "mark": None, "source": None, "detail": {}}
    pair = f"{symbol.replace('USDT','')}/USDT:USDT"
    try:
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            d = r.json()
            result["rate"] = float(d.get("lastFundingRate", 0)) * 100
            result["mark"] = float(d.get("markPrice", 0) or 0) or None
            next_ts = d.get("nextFundingTime")
            if next_ts:
                result["next_time"] = datetime.fromtimestamp(int(next_ts) / 1000, tz=timezone.utc).strftime("%H:%M UTC")
            result["source"] = "Binance"
            result["detail"]["binance"] = result["rate"]
            return result
    except Exception as e:
        print("Binance funding:", e)
    if CCXT_AVAILABLE:
        for name in ("bybit", "okx", "binance"):
            try:
                ex = getattr(ccxt, name)({"enableRateLimit": True, "timeout": 12000})
                r = ex.fetch_funding_rate(pair)
                fr = r.get("fundingRate")
                if fr is not None:
                    result["detail"][name] = float(fr) * 100
                    if result["rate"] is None:
                        result["rate"] = float(fr) * 100
                        result["source"] = name.capitalize()
                        ts = r.get("fundingTimestamp") or r.get("nextFundingTimestamp")
                        if ts:
                            result["next_time"] = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%H:%M UTC")
            except Exception as e:
                print(f"Funding {name}:", e)
    return result

# -------------------- آن‌چین BTC: STH / LTH Realized Price --------------------

def _load_onchain_cache():
    data = load_json(ONCHAIN_CACHE_FILE)
    if not data or "ts" not in data:
        return None
    try:
        if (time.time() - float(data["ts"])) / 3600.0 <= ONCHAIN_CACHE_HOURS:
            return data.get("payload")
    except Exception:
        pass
    return None

def _save_onchain_cache(payload):
    save_json(ONCHAIN_CACHE_FILE, {"ts": time.time(), "payload": payload})

def fetch_explore21_realized():
    try:
        url = "https://data.explore21.com/v1/metrics/bitcoin-realized-price/latest"
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200:
            return None
        j = r.json()
        rows = j.get("data") or []
        if not rows:
            return None
        row = rows[-1]
        return {
            "date": row.get("date"),
            "realized_price": float(row["realized_price_usd"]) if row.get("realized_price_usd") is not None else None,
            "sth_realized_price": float(row["sth_realized_price_usd"]) if row.get("sth_realized_price_usd") is not None else None,
            "btc_close": float(row["btc_close_usd"]) if row.get("btc_close_usd") is not None else None,
            "source": "Explore21",
            "chart": "https://charts.explore21.com/bitcoin-realized-price",
            "block": j.get("as_of_block"),
        }
    except Exception as e:
        print("Explore21:", e)
        return None

def fetch_coinglass_lth():
    if not COINGLASS_API_KEY:
        return None
    try:
        url = "https://open-api-v4.coinglass.com/api/index/bitcoin-lth-realized-price"
        r = requests.get(url, headers={**HEADERS, "CG-API-KEY": COINGLASS_API_KEY}, timeout=12)
        if r.status_code != 200:
            return None
        j = r.json()
        arr = j.get("data") or []
        if not arr:
            return None
        last = arr[-1]
        lth = last.get("lth_realized_price") or last.get("lthRealizedPrice")
        return {
            "lth_realized_price": float(lth) if lth is not None else None,
            "price": float(last.get("price") or 0) or None,
            "source": "CoinGlass",
        }
    except Exception as e:
        print("CoinGlass LTH:", e)
        return None

def get_btc_onchain_realized(live_price=None):
    cached = _load_onchain_cache()
    if cached and live_price is None:
        return cached

    base = fetch_explore21_realized() or {}
    lth_data = fetch_coinglass_lth() or {}

    rp = base.get("realized_price")
    sth = base.get("sth_realized_price")
    lth = lth_data.get("lth_realized_price")
    px = live_price or base.get("btc_close") or get_binance_price("BTCUSDT")

    sth_mvrv = (px / sth) if (px and sth and sth > 0) else None
    lth_mvrv = (px / lth) if (px and lth and lth > 0) else None
    overall_mvrv = (px / rp) if (px and rp and rp > 0) else None
    dist_sth = ((px - sth) / sth * 100) if (px and sth and sth > 0) else None
    dist_lth = ((px - lth) / lth * 100) if (px and lth and lth > 0) else None

    notes = []
    bias_hint = "خنثی"

    if sth_mvrv is not None:
        if sth_mvrv < 0.95:
            notes.append(f"STH زیر هزینه خرید (MVRV {sth_mvrv:.2f}) → فشار فروش کوتاه‌مدت / تسلیم نسبی")
            bias_hint = "احتیاط_نزولی_کوتاه"
        elif sth_mvrv > 1.15:
            notes.append(f"STH در سود (MVRV {sth_mvrv:.2f}) → احتمال حمایت نزدیک STH RP در پولبک")
        else:
            notes.append(f"STH نزدیک سربه‌سر (MVRV {sth_mvrv:.2f})")

    if lth_mvrv is not None:
        if lth_mvrv < 1.05:
            notes.append(f"LTH نزدیک/زیر سربه‌سر (MVRV {lth_mvrv:.2f}) → منطقه حساس چرخه‌ای")
            if lth_mvrv < 1.0:
                bias_hint = "حمایت_چرخه_ای"
        elif lth_mvrv > 2.5:
            notes.append(f"LTH MVRV بالا ({lth_mvrv:.2f}) → سودآوری سنگین؛ ریسک توزیع")
            bias_hint = "احتیاط_توزیع"
        else:
            notes.append(f"LTH در سود معقول (MVRV {lth_mvrv:.2f})")
    else:
        notes.append("LTH RP: برای داده کامل، COINGLASS_API_KEY را در Render تنظیم کنید")

    if dist_sth is not None and abs(dist_sth) < 3:
        notes.append(f"قیمت بسیار نزدیک STH RP ({dist_sth:+.1f}%)")
    if dist_lth is not None and abs(dist_lth) < 5:
        notes.append(f"قیمت نزدیک LTH RP ({dist_lth:+.1f}%) → حمایت/مقاومت چرخه‌ای")

    payload = {
        "ok": bool(rp or sth or lth),
        "date": base.get("date"),
        "price": px,
        "realized_price": rp,
        "sth_realized_price": sth,
        "lth_realized_price": lth,
        "sth_mvrv": round(sth_mvrv, 3) if sth_mvrv is not None else None,
        "lth_mvrv": round(lth_mvrv, 3) if lth_mvrv is not None else None,
        "overall_mvrv": round(overall_mvrv, 3) if overall_mvrv is not None else None,
        "dist_sth_pct": round(dist_sth, 2) if dist_sth is not None else None,
        "dist_lth_pct": round(dist_lth, 2) if dist_lth is not None else None,
        "bias_hint": bias_hint,
        "notes": notes,
        "sources": {
            "explore21": base.get("source"),
            "coinglass": lth_data.get("source"),
            "chart": base.get("chart") or "https://charts.explore21.com/bitcoin-realized-price",
        },
        "block": base.get("block"),
    }
    if payload["ok"]:
        _save_onchain_cache(payload)
    return payload

def format_onchain_html(oc):
    if not oc or not oc.get("ok"):
        return (
            "⛓ <b>آن‌چین BTC</b>\nداده در دسترس نیست.\n"
            '<a href="https://charts.explore21.com/bitcoin-realized-price">Explore21</a>'
        )
    lines = ["⛓ <b>آن‌چین BTC | Realized Price</b>"]
    if oc.get("date"):
        lines.append(f"تاریخ: <code>{esc(oc['date'])}</code>")
    if oc.get("price"):
        lines.append(f"قیمت: <code>{p1(oc['price'])}</code>")
    if oc.get("sth_realized_price"):
        m = f" | MVRV <code>{oc['sth_mvrv']}</code>" if oc.get("sth_mvrv") is not None else ""
        d = f" | فاصله <code>{oc['dist_sth_pct']:+.1f}%</code>" if oc.get("dist_sth_pct") is not None else ""
        lines.append(f"STH RP: <code>{p1(oc['sth_realized_price'])}</code>{m}{d}")
    if oc.get("lth_realized_price"):
        m = f" | MVRV <code>{oc['lth_mvrv']}</code>" if oc.get("lth_mvrv") is not None else ""
        d = f" | فاصله <code>{oc['dist_lth_pct']:+.1f}%</code>" if oc.get("dist_lth_pct") is not None else ""
        lines.append(f"LTH RP: <code>{p1(oc['lth_realized_price'])}</code>{m}{d}")
    else:
        lines.append("LTH RP: — (اختیاری: COINGLASS_API_KEY)")
    if oc.get("realized_price"):
        m = f" | MVRV <code>{oc['overall_mvrv']}</code>" if oc.get("overall_mvrv") is not None else ""
        lines.append(f"Realized کلی: <code>{p1(oc['realized_price'])}</code>{m}")
    if oc.get("notes"):
        lines.append("\n<b>تفسیر:</b>")
        for n in oc["notes"][:5]:
            lines.append(f"• {esc(n)}")
    chart = (oc.get("sources") or {}).get("chart")
    if chart:
        lines.append(
            f'\n<a href="{chart}">Explore21</a> | '
            '<a href="https://nodewitness.com/en/charts/bitcoin-sth-lth-realized-price-chart/">NodeWitness</a>'
        )
    return "\n".join(lines)

def monitor_positions(bot: Bot):
    while True:
        try:
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
                            bot.send_message(
                                chat_id=uid,
                                text=(
                                    f"🛑 <b>پوزیشن بسته شد (حد ضرر)</b>\n\n"
                                    f"<code>{esc(symbol)}</code> | {esc(direction)}\n"
                                    f"قیمت: <code>{p1(price)}</code> | SL: <code>{p1(sl)}</code>"
                                ),
                                parse_mode="HTML",
                            )
                        except Exception:
                            pass
                        continue
                    if hit_tp:
                        close_position(uid)
                        try:
                            bot.send_message(
                                chat_id=uid,
                                text=(
                                    f"🎯 <b>پوزیشن بسته شد (حد سود)</b>\n\n"
                                    f"<code>{esc(symbol)}</code> | {esc(direction)}\n"
                                    f"قیمت: <code>{p1(price)}</code> | TP: <code>{p1(tp)}</code>"
                                ),
                                parse_mode="HTML",
                            )
                        except Exception:
                            pass
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
                            bot.send_message(
                                chat_id=uid,
                                text=f"⚠️ <b>نزدیک حد ضرر</b>\n<code>{esc(symbol)}</code> | {p1(price)} / SL {p1(sl)}",
                                parse_mode="HTML",
                            )
                            user_alerts["sl_warned"] = True
                        except Exception:
                            pass
                    if near_tp and not user_alerts.get("tp_warned"):
                        try:
                            bot.send_message(
                                chat_id=uid,
                                text=f"🎯 <b>نزدیک حد سود</b>\n<code>{esc(symbol)}</code> | {p1(price)} / TP {p1(tp)}",
                                parse_mode="HTML",
                            )
                            user_alerts["tp_warned"] = True
                        except Exception:
                            pass
                    if pullback and not user_alerts.get("pullback_warned"):
                        try:
                            bot.send_message(
                                chat_id=uid,
                                text=f"🔄 <b>پولبک</b>\n<code>{esc(symbol)}</code> | {p1(price)} vs ورود {p1(entry)}",
                                parse_mode="HTML",
                            )
                            user_alerts["pullback_warned"] = True
                        except Exception:
                            pass
                    alerts[uid_str] = user_alerts
                except Exception as e:
                    print("Monitor pos:", e)
            save_json(ALERT_STATE_FILE, alerts)

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
                                        f"🚨 <b>هشدار قیمت</b>\n"
                                        f"<code>{esc(a['symbol'])}</code> → <code>{p1(price)}</code>\n"
                                        f"شرط: {('بالای' if cond == 'above' else 'زیر')} <code>{p1(target)}</code>"
                                    ),
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass
                            changed = True
                        else:
                            remaining.append(a)
                    except Exception:
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
            df = pd.DataFrame(
                r.json(),
                columns=[
                    "open_time", "open", "high", "low", "close", "volume",
                    "close_time", "qav", "num_trades", "taker_base", "taker_quote", "ignore",
                ],
            )
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = df[c].astype(float)
            return df
    except Exception as e:
        print("Fetch:", e)
    return None

def safe_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)

def calc_atr(df, period=14):
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()

def add_macd(df, fast=12, slow=26, signal=9):
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    return df

def add_indicators(df):
    df = df.copy()
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()
    df["sma200"] = df["close"].rolling(200).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["bb_mid"] = df["close"].rolling(20).mean()
    df["bb_std"] = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2 * df["bb_std"]
    df["tenkan"] = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    df["kijun"] = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    df["senkou_a"] = ((df["tenkan"] + df["kijun"]) / 2).shift(26)
    df["senkou_b"] = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)
    df["rsi"] = safe_rsi(df["close"])
    df = add_macd(df)
    atr = calc_atr(df)
    df["atr"] = atr
    df["atr_upper"] = df["close"] + atr
    df["atr_lower"] = df["close"] - atr
    return df

def calc_fib(df, lookback=100):
    h = df["high"].tail(lookback).max()
    l = df["low"].tail(lookback).min()
    d = h - l
    return {
        "0.0": h, "0.236": h - d * 0.236, "0.382": h - d * 0.382,
        "0.5": h - d * 0.5, "0.618": h - d * 0.618, "0.786": h - d * 0.786, "1.0": l,
    }

# ===================== NDS رسمی =====================

def detect_swing_nodes(df, lb=3, atr_mult=0.75):
    if df is None or len(df) < lb * 4 + 10:
        return []
    atr = calc_atr(df).bfill()
    hs, ls = df["high"].values, df["low"].values
    nodes = []
    for i in range(lb, len(df) - lb):
        if hs[i] == max(hs[i - lb : i + lb + 1]) and (hs[i] - min(ls[i - lb : i + 1])) > float(atr.iloc[i]) * atr_mult:
            nodes.append({"type": "high", "price": float(hs[i]), "index": i})
        if ls[i] == min(ls[i - lb : i + lb + 1]) and (max(hs[i - lb : i + 1]) - ls[i]) > float(atr.iloc[i]) * atr_mult:
            nodes.append({"type": "low", "price": float(ls[i]), "index": i})
    nodes.sort(key=lambda x: x["index"])
    return nodes

def level_864(start_price, end_price):
    return end_price + NDS_86 * (start_price - end_price)

def count_hh_ll(nodes):
    highs = [n for n in nodes if n["type"] == "high"]
    lows = [n for n in nodes if n["type"] == "low"]
    hh = hl = lh = ll = 0
    for i in range(1, len(highs)):
        if highs[i]["price"] > highs[i - 1]["price"]:
            hh += 1
        else:
            lh += 1
    for i in range(1, len(lows)):
        if lows[i]["price"] > lows[i - 1]["price"]:
            hl += 1
        else:
            ll += 1
    return hh, hl, lh, ll, highs, lows

def detect_open_nodes_direction(nodes):
    if len(nodes) < 4:
        return "خنثی", None, None
    highs = [n for n in nodes if n["type"] == "high"]
    lows = [n for n in nodes if n["type"] == "low"]
    if len(highs) >= 2 and len(lows) >= 2:
        n1h, n2h = highs[-2], highs[-1]
        n1l, n2l = lows[-2], lows[-1]
        if n2h["price"] > n1h["price"] and n2l["price"] > n1l["price"]:
            return "صعودی", n1h, n2h
        if n2h["price"] < n1h["price"] and n2l["price"] < n1l["price"]:
            return "نزولی", n1l, n2l
    return "خنثی", None, None

def detect_hook_structure(nodes):
    if len(nodes) < 4:
        return []
    hooks = []
    recent = nodes[-12:]
    h_i = l_i = 0
    for n in recent:
        if n["type"] == "high":
            h_i += 1
            if h_i <= 3:
                hooks.append({**n, "label": f"H{h_i}", "kind": "hook"})
        else:
            l_i += 1
            if l_i <= 3:
                hooks.append({**n, "label": f"F{l_i}", "kind": "flag"})
    return hooks

def detect_flag_lvs(df, nodes, lookback=30):
    if df is None or len(df) < lookback + 5 or not nodes:
        return None
    sub = df.tail(lookback)
    vol_mean = float(sub["volume"].mean()) or 1.0
    last = sub.iloc[-1]
    prev = sub.iloc[-5]
    move = abs(float(last["close"]) - float(prev["close"]))
    atr = float(calc_atr(df).iloc[-1] or 0)
    vol_last = float(sub["volume"].iloc[-3:].mean())
    if atr > 0 and move > atr * 1.2 and vol_last < vol_mean * 0.85:
        return {"ok": True, "price": float(last["high"] if last["close"] >= last["open"] else last["low"]), "type": "flag_lvs"}
    return None

def find_123_pattern(df, direction_bias, lookback=50):
    if df is None or len(df) < lookback:
        return None
    sub = df.tail(lookback).reset_index(drop=True)
    nodes = detect_swing_nodes(sub, lb=2, atr_mult=0.55)
    if len(nodes) < 3:
        return None
    n1, n2, n3 = nodes[-3], nodes[-2], nodes[-1]
    if direction_bias == "صعودی":
        if n1["type"] == "low" and n2["type"] == "high" and n3["type"] == "low":
            if n3["price"] > n1["price"] and n2["price"] > n3["price"]:
                return {"ok": True, "kind": "123_bull", "p1": n1["price"], "p2": n2["price"], "p3": n3["price"]}
    if direction_bias == "نزولی":
        if n1["type"] == "high" and n2["type"] == "low" and n3["type"] == "high":
            if n3["price"] < n1["price"] and n2["price"] < n3["price"]:
                return {"ok": True, "kind": "123_bear", "p1": n1["price"], "p2": n2["price"], "p3": n3["price"]}
    return None

def analyze_nds_official(df_htf, df_ltf=None, price=None):
    result = {
        "bias": "خنثی", "cycle": "—", "trade_allowed": False,
        "reason": "جهت تایم بالاتر مشخص نیست (قانون ۱ و ۲)",
        "last_high": None, "last_low": None, "nodes": [], "hooks": [],
        "level_864": None, "pattern_123": None, "flag": None,
        "n1": None, "n2": None, "entry_zone": None,
        "tf_low": None, "tf_high": None, "sl_hint": None, "tp_hint": None,
        "hh": 0, "ll": 0, "expected_move_pct": 0.0,
    }
    if df_htf is None or len(df_htf) < 40:
        result["reason"] = "داده تایم بالاتر ناکافی"
        return result

    price = float(price if price is not None else df_htf["close"].iloc[-1])
    result["tf_low"] = float(df_htf["low"].tail(80).min())
    result["tf_high"] = float(df_htf["high"].tail(80).max())

    nodes = detect_swing_nodes(df_htf, lb=3, atr_mult=0.85)
    result["nodes"] = nodes
    result["hooks"] = detect_hook_structure(nodes)
    hh, hl, lh, ll, highs, lows = count_hh_ll(nodes)
    result["hh"], result["ll"] = hh, ll

    bias, n1, n2 = detect_open_nodes_direction(nodes)
    result["bias"] = bias
    result["n1"] = n1["price"] if n1 else None
    result["n2"] = n2["price"] if n2 else None
    if highs:
        result["last_high"] = highs[-1]["price"]
    if lows:
        result["last_low"] = lows[-1]["price"]

    if bias == "صعودی":
        result["cycle"] = "HH+HL | دو Node باز صعودی"
    elif bias == "نزولی":
        result["cycle"] = "LH+LL | دو Node باز نزولی"
    else:
        result["cycle"] = "رنج / بدون دو Node باز"

    level = None
    if len(nodes) >= 2:
        a, b = nodes[-2], nodes[-1]
        if a["type"] != b["type"]:
            level = level_864(a["price"], b["price"])
    result["level_864"] = level
    result["flag"] = detect_flag_lvs(df_htf, nodes)

    target_ref = level or (result["last_high"] if bias == "صعودی" else result["last_low"])
    if target_ref and price > 0:
        result["expected_move_pct"] = abs(target_ref - price) / price * 100

    pattern = None
    if df_ltf is not None and len(df_ltf) >= 40 and bias in ("صعودی", "نزولی"):
        pattern = find_123_pattern(df_ltf, bias, lookback=50)
        result["pattern_123"] = pattern

    near_864 = False
    if level is not None and price > 0:
        near_864 = abs(price - level) / price <= 0.018

    end_down = ll >= 3
    end_up = hh >= 3

    if bias not in ("صعودی", "نزولی"):
        result["trade_allowed"] = False
        if result["expected_move_pct"] >= MIN_RANGE_MOVE_PCT and (near_864 or pattern):
            result["reason"] = f"رنج؛ حرکت مورد انتظار {result['expected_move_pct']:.1f}% ≥ ۳٪ — نیاز به جهت واضح‌تر"
        else:
            result["reason"] = "قانون ۲: دو Node باز واضح نیست → معامله نکن"
        return result

    if bias == "صعودی" and (near_864 or (pattern and pattern.get("kind") == "123_bull")):
        if result["expected_move_pct"] < MIN_RANGE_MOVE_PCT and not (pattern and near_864):
            result["trade_allowed"] = False
            result["reason"] = f"صعودی اما حرکت ضعیف ({result['expected_move_pct']:.1f}% < ۳٪) → صبر"
        else:
            result["trade_allowed"] = True
            result["entry_zone"] = pattern["p3"] if pattern else (level or price)
            result["reason"] = "قانون ۱+۲: جهت بالاتر صعودی + تأیید ۱۲۳/۸۶٫۴٪"
            if pattern:
                result["sl_hint"] = pattern["p3"] * 0.997
                result["tp_hint"] = result["n2"] or result["last_high"]
    elif bias == "نزولی" and (near_864 or (pattern and pattern.get("kind") == "123_bear")):
        if result["expected_move_pct"] < MIN_RANGE_MOVE_PCT and not (pattern and near_864):
            result["trade_allowed"] = False
            result["reason"] = f"نزولی اما حرکت ضعیف ({result['expected_move_pct']:.1f}% < ۳٪) → صبر"
        else:
            result["trade_allowed"] = True
            result["entry_zone"] = pattern["p3"] if pattern else (level or price)
            result["reason"] = "قانون ۱+۲: جهت بالاتر نزولی + تأیید ۱۲۳/۸۶٫۴٪"
            if pattern:
                result["sl_hint"] = pattern["p3"] * 1.003
                result["tp_hint"] = result["n2"] or result["last_low"]
    else:
        result["trade_allowed"] = False
        extra = ""
        if end_down:
            extra = " | نشانه ۳LL"
        if end_up:
            extra = " | نشانه ۳HH"
        result["reason"] = f"جهت {bias}؛ هنوز ۱۲۳ یا نزدیکی ۸۶٫۴٪ کامل نیست{extra} → صبر"

    return result

def analyze_nds(df):
    geo = analyze_nds_official(df, None, float(df["close"].iloc[-1]) if df is not None and len(df) else None)
    return {
        "bias": geo["bias"], "cycle": geo["cycle"],
        "last_high": geo["last_high"], "last_low": geo["last_low"],
        "nodes": geo["nodes"], "hooks": geo["hooks"],
        "tf_low": geo["tf_low"], "tf_high": geo["tf_high"],
        "level_864": geo["level_864"], "trade_allowed": geo["trade_allowed"],
        "reason": geo["reason"], "pattern_123": geo.get("pattern_123"),
        "n1": geo.get("n1"), "n2": geo.get("n2"),
    }

def analyze_nds_multi(symbol, current_price=None):
    results = []
    for interval, label in NDS_INTERVALS:
        limit = 200 if interval == "1d" else 150
        df = fetch_klines(symbol, interval, limit)
        if df is None or len(df) < 50:
            results.append({"interval": interval, "label": label, "bias": "—", "cycle": "داده ناکافی", "ok": False})
            continue
        info = analyze_nds(df)
        tf_low, tf_high = info.get("tf_low"), info.get("tf_high")
        dist_pct = range_val = None
        if tf_low is not None and current_price:
            dist_pct = (current_price - tf_low) / current_price * 100
        if tf_low is not None and tf_high is not None:
            range_val = tf_high - tf_low
        results.append({
            "interval": interval, "label": label, "bias": info["bias"], "cycle": info["cycle"],
            "last_high": info.get("last_high"), "last_low": info.get("last_low"),
            "tf_low": tf_low, "tf_high": tf_high, "dist_pct": dist_pct, "range_val": range_val, "ok": True,
        })
    return results

def detect_order_blocks(df, lookback=40):
    if df is None or len(df) < lookback + 5:
        return []
    sub = df.tail(lookback).copy()
    atr = float(calc_atr(df).iloc[-1] or 0)
    blocks = []
    for i in range(2, len(sub) - 2):
        body = abs(sub["close"].iloc[i] - sub["open"].iloc[i])
        rng = sub["high"].iloc[i] - sub["low"].iloc[i]
        if rng <= 0 or atr <= 0:
            continue
        if body > atr * 0.8 and body / rng > 0.55:
            direction = "bullish" if sub["close"].iloc[i] > sub["open"].iloc[i] else "bearish"
            if direction == "bullish" and sub["low"].iloc[i + 1 : i + 3].min() >= sub["low"].iloc[i] * 0.998:
                blocks.append({"type": "bullish_ob", "low": float(sub["low"].iloc[i]), "high": float(sub["high"].iloc[i])})
            if direction == "bearish" and sub["high"].iloc[i + 1 : i + 3].max() <= sub["high"].iloc[i] * 1.002:
                blocks.append({"type": "bearish_ob", "low": float(sub["low"].iloc[i]), "high": float(sub["high"].iloc[i])})
    return blocks[-3:]

def calculate_signal_score(nds_results, rsi, funding, volume_ratio, higher_bias, short_bias, macd_hist=None, geo=None):
    score = 50.0
    reasons, risks = [], []
    if geo and geo.get("trade_allowed"):
        score += 20
        reasons.append(f"NDS رسمی: {geo.get('reason')}")
        higher_bias = geo.get("bias") or higher_bias
    elif geo:
        score -= 6
        risks.append(geo.get("reason") or "تأیید NDS ناکافی")

    higher_tfs = [r for r in nds_results if r["interval"] in ["1d", "12h", "8h"] and r["ok"]]
    bullish_h = sum(1 for r in higher_tfs if r["bias"] == "صعودی")
    bearish_h = sum(1 for r in higher_tfs if r["bias"] == "نزولی")
    if bullish_h >= 2:
        score += 10
        reasons.append(f"هم‌راستایی تایم بالاتر صعودی ({bullish_h})")
        if higher_bias == "خنثی":
            higher_bias = "صعودی"
    elif bearish_h >= 2:
        score += 10
        reasons.append(f"هم‌راستایی تایم بالاتر نزولی ({bearish_h})")
        if higher_bias == "خنثی":
            higher_bias = "نزولی"

    if geo and geo.get("level_864"):
        reasons.append(f"سطح ۸۶٫۴٪: {p1(geo['level_864'])}")
    if geo and geo.get("pattern_123"):
        reasons.append(f"۱۲۳: {geo['pattern_123'].get('kind')}")
    if geo and geo.get("n1") and geo.get("n2"):
        reasons.append(f"Node باز N1/N2: {p1(geo['n1'])} / {p1(geo['n2'])}")
    if geo and geo.get("expected_move_pct"):
        reasons.append(f"حرکت مورد انتظار: {geo['expected_move_pct']:.1f}%")

    if 40 <= rsi <= 60:
        score += 4
        reasons.append(f"RSI متعادل ({rsi:.1f})")
    elif rsi > 72:
        score -= 8
        risks.append(f"RSI اشباع خرید ({rsi:.1f})")
    elif rsi < 28:
        score -= 8
        risks.append(f"RSI اشباع فروش ({rsi:.1f})")

    if funding is not None:
        if abs(funding) > 0.08:
            score -= 3
            risks.append(f"فاندینگ افراطی ({funding:.4f}%)")
        else:
            reasons.append(f"فاندینگ ({funding:.4f}%)")

    if volume_ratio is not None:
        if volume_ratio >= 1.15:
            score += 3
        elif volume_ratio < 0.55:
            score -= 3
            risks.append(f"حجم ضعیف ({volume_ratio:.2f}x)")

    if macd_hist is not None and higher_bias in ("صعودی", "نزولی"):
        if (higher_bias == "صعودی" and macd_hist > 0) or (higher_bias == "نزولی" and macd_hist < 0):
            score += 4
            reasons.append("MACD هم‌راستا")
        else:
            score -= 2
            risks.append("MACD خلاف بایاس")

    if higher_bias == "صعودی" and short_bias:
        score += 3
    elif higher_bias == "نزولی" and not short_bias:
        score += 3
    elif higher_bias != "خنثی":
        score -= 4
        risks.append("افق کوتاه با بایاس غالب هم‌خوان نیست")

    return max(0, min(100, round(score, 1))), reasons, risks, higher_bias

def calculate_dollar_sl_tp(
    entry, direction, atr, margin=5.0, leverage=8, risk_dollar=0.5,
    structure_low=None, structure_high=None, sl_hint=None, tp_hint=None,
):
    position_size = margin * leverage
    price_risk = (risk_dollar / position_size) * entry
    atr_distance = atr * 1.5
    final_distance = max(price_risk, atr_distance * 0.85)
    if direction == "LONG":
        sl = entry - final_distance
        if sl_hint and sl_hint < entry:
            sl = min(sl, sl_hint)
        elif structure_low and structure_low < entry:
            sl = min(sl, structure_low - atr * 0.1)
        tp = entry + final_distance * 2.0
        if tp_hint and tp_hint > entry:
            tp = max(tp, tp_hint)
        elif structure_high and structure_high > entry:
            tp = min(tp, structure_high - atr * 0.1) if structure_high - entry > final_distance else tp
    else:
        sl = entry + final_distance
        if sl_hint and sl_hint > entry:
            sl = max(sl, sl_hint)
        elif structure_high and structure_high > entry:
            sl = max(sl, structure_high + atr * 0.1)
        tp = entry - final_distance * 2.0
        if tp_hint and tp_hint < entry:
            tp = min(tp, tp_hint)
        elif structure_low and structure_low < entry:
            tp = max(tp, structure_low + atr * 0.1) if entry - structure_low > final_distance else tp
    return round(sl, 1), round(tp, 1)

# -------------------- نمودار --------------------

def draw_candles(ax, df, width=0.55):
    opens, highs, lows, closes = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    for i in range(len(df)):
        color = "#2ed573" if closes[i] >= opens[i] else "#ff4757"
        ax.plot([i, i], [lows[i], highs[i]], color=color, lw=0.85, zorder=3)
        bottom = min(opens[i], closes[i])
        height = abs(closes[i] - opens[i]) or (highs[i] - lows[i]) * 0.02 or 1e-8
        ax.add_patch(Rectangle((i - width / 2, bottom), width, height, facecolor=color, edgecolor=color, lw=0.35, zorder=4))

def draw_hook_marker(ax, x, y, label, is_high=True):
    color = "#ff4757" if is_high else "#1dd1a1"
    size = max(abs(y) * 0.008, 0.8)
    ax.scatter([x], [y], s=55, c=color, marker="s", zorder=15, edgecolors="white", linewidths=0.6)
    ty = y + size * 3.5 if is_high else y - size * 3.5
    ax.annotate(
        label, xy=(x, y), xytext=(x, ty), fontsize=7.5, ha="center", va="center",
        color=color, fontweight="bold", zorder=16,
        arrowprops=dict(arrowstyle="-", color=color, lw=0.8, alpha=0.7),
    )

def create_pro_chart(
    df, title, fibs=None, entry=None, sl=None, tp=None,
    sims=None, mean_path=None, level_864=None, show_nds_hooks=False, nds_info=None, max_bars=100,
):
    plot_df = df.tail(max_bars).copy().reset_index(drop=True)
    n = len(plot_df)
    fig = plt.figure(figsize=(14, 8.6), facecolor="#0d0d0d")
    gs = GridSpec(4, 1, height_ratios=[3.7, 0.68, 0.68, 0.02], hspace=0.05)
    ax = fig.add_subplot(gs[0])
    ax_macd = fig.add_subplot(gs[1], sharex=ax)
    ax_rsi = fig.add_subplot(gs[2], sharex=ax)
    for a in [ax, ax_macd, ax_rsi]:
        a.set_facecolor("#0d0d0d")
        a.tick_params(colors="#bbbbbb", labelsize=6.4)
        for spine in a.spines.values():
            spine.set_color("#333333")
        a.yaxis.tick_right()
        a.yaxis.set_label_position("right")

    draw_candles(ax, plot_df)
    ax.plot(plot_df["sma20"].values, color="#c39bd3", lw=0.9, label="MA20", alpha=0.85)
    ax.plot(plot_df["sma50"].values, color="#f39c12", lw=1.15, label="MA50")
    if "sma200" in plot_df.columns and plot_df["sma200"].notna().sum() > 10:
        ax.plot(plot_df["sma200"].values, color="#3498db", lw=1.25, label="MA200", alpha=0.9)
    ax.plot(plot_df["ema20"].values, color="#2ecc71", lw=0.85, label="EMA20", alpha=0.8)
    ax.plot(plot_df["ema50"].values, color="#e74c3c", lw=0.95, label="EMA50", alpha=0.8)

    if "atr_upper" in plot_df.columns:
        ax.plot(plot_df["atr_upper"].values, color="#95a5a6", lw=0.8, ls=":", alpha=0.7, label="ATR+")
        ax.plot(plot_df["atr_lower"].values, color="#95a5a6", lw=0.8, ls=":", alpha=0.7, label="ATR-")

    if "senkou_a" in plot_df.columns:
        ax.fill_between(range(n), plot_df["senkou_a"].values, plot_df["senkou_b"].values,
                        where=plot_df["senkou_a"] >= plot_df["senkou_b"], color="#00ff7f", alpha=0.08)
        ax.fill_between(range(n), plot_df["senkou_a"].values, plot_df["senkou_b"].values,
                        where=plot_df["senkou_a"] < plot_df["senkou_b"], color="#ff69b4", alpha=0.08)

    if fibs:
        colors = {"0.0": "#ecf0f1", "0.236": "#e74c3c", "0.382": "#e67e22", "0.5": "#f1c40f",
                  "0.618": "#2ecc71", "0.786": "#3498db", "1.0": "#9b59b6"}
        for k, v in fibs.items():
            c = colors.get(k, "#aaaaaa")
            ax.axhline(v, color=c, ls=":", lw=0.75, alpha=0.7)
            ax.text(n + 0.6, v, f"{k} {p1(v)}", fontsize=5.8, va="center", color=c)

    if level_864 is not None:
        ax.axhline(level_864, color="#f1c40f", lw=1.4, ls="-.", alpha=0.95, label="86.4%")
        ax.text(n + 0.6, level_864, f"86.4% {p1(level_864)}", fontsize=6.5, va="center", color="#f1c40f", fontweight="bold")

    if entry is not None:
        ax.axhline(entry, color="#2980b9", lw=1.2, label="Entry")
    if sl is not None:
        ax.axhline(sl, color="#c0392b", lw=1.2, label="SL")
    if tp is not None:
        ax.axhline(tp, color="#27ae60", lw=1.2, label="TP")

    if sims is not None and mean_path is not None:
        last = n - 1
        horizon = len(mean_path)
        for s in sims[::4]:
            ax.plot(range(last, last + horizon), s[:horizon], color="#ffffff", alpha=0.10, lw=0.6)
        ax.plot(range(last, last + horizon), mean_path, color="#ffffff", lw=1.5, label="Pred 4-6h", zorder=6)

    if show_nds_hooks and nds_info:
        hooks = nds_info.get("hooks") or []
        offset = len(df) - n
        for node in hooks:
            x = node["index"] - offset
            if 0 <= x < n:
                draw_hook_marker(ax, x, node["price"], node.get("label", "?"), is_high=(node.get("type") == "high"))

    ax.set_title(title, color="white", fontsize=10.5, pad=6)
    ax.legend(loc="upper left", fontsize=5.5, facecolor="#1a1a1a", edgecolor="#333", labelcolor="white", ncol=4)
    ax.grid(True, alpha=0.12, color="#444")
    ax.yaxis.set_major_formatter(FormatStrFormatter("%.1f"))
    ax.set_xlim(-1, n + 10)

    if "macd" in plot_df.columns:
        hist = plot_df["macd_hist"].values
        colors_h = ["#2ed573" if v >= 0 else "#ff4757" for v in hist]
        ax_macd.bar(range(n), hist, color=colors_h, alpha=0.65, width=0.6)
        ax_macd.plot(plot_df["macd"].values, color="#00bfff", lw=0.9)
        ax_macd.plot(plot_df["macd_signal"].values, color="#f39c12", lw=0.85)
        ax_macd.axhline(0, color="#555", lw=0.5, alpha=0.5)
    ax_macd.set_ylabel("MACD", color="#888888", fontsize=6.2)
    ax_macd.grid(True, alpha=0.10, color="#444")
    ax_macd.tick_params(labelbottom=False)

    ax_rsi.plot(plot_df["rsi"].values, color="#e056fd", lw=0.9)
    ax_rsi.axhline(70, color="#e74c3c", ls="--", lw=0.55, alpha=0.5)
    ax_rsi.axhline(30, color="#2ecc71", ls="--", lw=0.55, alpha=0.5)
    ax_rsi.axhline(50, color="#666666", ls=":", lw=0.45, alpha=0.4)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel("RSI", color="#888888", fontsize=6.2)
    ax_rsi.grid(True, alpha=0.10, color="#444")
    plt.setp(ax.get_xticklabels(), visible=False)
    plt.setp(ax_macd.get_xticklabels(), visible=False)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", dpi=150, facecolor="#0d0d0d")
    buf.seek(0)
    plt.close()
    return buf

def create_nds_weekly_chart(df, title, geo):
    return create_pro_chart(
        df, title, fibs=calc_fib(df, lookback=min(80, len(df))),
        level_864=geo.get("level_864"), show_nds_hooks=True, nds_info=geo, max_bars=min(150, len(df)),
    )

# ===================== دستورات =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    await update.message.reply_text(
        "🤖 <b>𓄂 ηοsτrαμεhrδαδ𓆃 | Kronos</b>\n\n"
        "<code>/predict</code> <code>/nds</code> <code>/onchain</code> <code>/market</code>\n"
        "<code>/alert</code> <code>/alerts</code> <code>/removealert</code>\n"
        "<code>/position</code> <code>/close</code> <code>/confirm</code>\n"
        "<code>/watch</code> <code>/watchlist</code> <code>/unwatch</code>\n"
        "<code>/log</code> <code>/performance</code>\n"
        "<code>/buy</code> <code>/status</code> <code>/tools</code> <code>/news</code>\n\n"
        "📌 ورود فقط با <code>/confirm</code>\n"
        "🧬 NDS رسمی: Node • ۸۶٫۴٪ • Hook/Flag\n"
        "⛓ <code>/onchain</code> → STH/LTH Realized Price\n"
        "در رنج فقط حرکت ≥ ۳٪ سیگنال می‌شود",
        parse_mode="HTML",
    )

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    await update.message.reply_text(
        f"💳 <b>اشتراک ۳۰ روزه</b>\n\nمبلغ: <b>{SUBSCRIPTION_PRICE} USDT</b> (TRC20)\n\nآدرس:\n<code>{USDT_WALLET}</code>",
        parse_mode="HTML",
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if is_subscribed(uid):
        await update.message.reply_text(f"✅ فعال — <b>{remaining_days(uid)} روز</b>", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ اشتراک فعال نیست.")

async def tools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    text = (
        "🛠 <b>منابع</b>\n\n"
        '• <a href="https://www.coinglass.com">Coinglass</a>\n'
        '• <a href="https://charts.explore21.com/bitcoin-realized-price">Explore21 Realized</a>\n'
        '• <a href="https://nodewitness.com/en/charts/bitcoin-sth-lth-realized-price-chart/">NodeWitness STH/LTH</a>\n'
        '• <a href="https://www.arkham.com">Arkham</a>\n'
        '• <a href="https://www.lookonchain.com">Lookonchain</a>\n'
        '• <a href="https://dexscreener.com">DexScreener</a>\n'
        '• <a href="https://cryptoquant.com">CryptoQuant</a>'
    )
    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)

async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    base = (context.args[0].upper() if context.args else "BTC").replace("USDT", "")
    text = (
        f"📰 <b>اخبار | {esc(base)}</b>\n\n"
        '• <a href="https://www.coinglass.com">Coinglass</a>\n'
        '• <a href="https://www.lookonchain.com">Lookonchain</a>\n'
        '• <a href="https://www.arkham.com">Arkham</a>\n'
        '• <a href="https://cryptopanic.com">CryptoPanic</a>\n'
        '• <a href="https://coinmarketcal.com">CoinMarketCal</a>'
    )
    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)

async def onchain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not check_rate(uid):
        await update.message.reply_text("⏳ کمی صبر کنید.")
        return
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    status = await update.message.reply_text("⏳ دریافت داده آن‌چین BTC ...")
    try:
        multi = get_multi_exchange_prices("BTCUSDT")
        price = multi["consensus"] or multi["binance"]
        oc = get_btc_onchain_realized(live_price=price)
        text = format_onchain_html(oc)
        text += f"\n\n📡 {format_price_sources(multi)}"
        text += "\n\nℹ️ داده چرخه‌ای بیت‌کوین است. ترید روی XT.com انجام می‌شود."
        await status.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        await status.edit_text(f"❌ {esc(str(e)[:120])}")

async def alert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if len(context.args) < 3:
        await update.message.reply_text("<code>/alert نماد قیمت above|below</code>", parse_mode="HTML")
        return
    try:
        symbol = context.args[0].upper()
        target = float(context.args[1])
        condition = context.args[2].lower()
        if condition not in ("above", "below"):
            await update.message.reply_text("شرط: above یا below")
            return
        a = add_price_alert(uid, symbol, target, condition)
        await update.message.reply_text(
            f"✅ هشدار <code>{a['id']}</code>\n<code>{esc(symbol)}</code> {condition} <code>{p1(target)}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"خطا: {esc(str(e)[:80])}")

async def alerts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    alerts = get_user_price_alerts(uid)
    if not alerts:
        await update.message.reply_text("هشداری نیست.")
        return
    lines = ["🚨 <b>هشدارها</b>\n"] + [
        f"• <code>{a['id']}</code> | <code>{esc(a['symbol'])}</code> {a['condition']} <code>{p1(a['target'])}</code>"
        for a in alerts
    ]
    lines.append("\nحذف: <code>/removealert ID</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def removealert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if not context.args:
        await update.message.reply_text("<code>/removealert ID</code>", parse_mode="HTML")
        return
    n = remove_price_alert(uid, context.args[0])
    await update.message.reply_text("✅ حذف شد." if n else "ID پیدا نشد.")

async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if not context.args:
        await update.message.reply_text("<code>/watch BTCUSDT ETHUSDT</code>", parse_mode="HTML")
        return
    result = add_to_watchlist(uid, [s.upper() for s in context.args])
    await update.message.reply_text("✅ <code>" + "</code>, <code>".join(result) + "</code>", parse_mode="HTML")

async def unwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if not context.args:
        await update.message.reply_text("<code>/unwatch BTCUSDT</code>", parse_mode="HTML")
        return
    result = remove_from_watchlist(uid, [s.upper() for s in context.args])
    await update.message.reply_text(
        "✅ <code>" + "</code>, <code>".join(result) + "</code>" if result else "خالی شد.", parse_mode="HTML"
    )

async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    wl = get_watchlist(uid)
    if not wl:
        await update.message.reply_text("واچ‌لیست خالی است.")
        return
    status = await update.message.reply_text("⏳ ...")
    lines = ["👀 <b>واچ‌لیست</b>\n"]
    for symbol in wl:
        try:
            df_4h = fetch_klines(symbol, "4h", 100)
            df_1d = fetch_klines(symbol, "1d", 80)
            if df_4h is None:
                lines.append(f"• <code>{esc(symbol)}</code>: داده نیست")
                continue
            nds_d = analyze_nds(df_1d) if df_1d is not None else {"bias": "—"}
            nds_4h = analyze_nds(df_4h)
            higher = nds_d["bias"] if nds_d.get("bias") != "خنثی" else nds_4h["bias"]
            price = float(df_4h["close"].iloc[-1])
            emoji = "🟢" if higher == "صعودی" else "🔴" if higher == "نزولی" else "⚪️"
            lines.append(f"{emoji} <code>{esc(symbol)}</code> → <b>{esc(higher)}</b> | <code>{p1(price)}</code>")
        except Exception:
            lines.append(f"• <code>{esc(symbol)}</code>: خطا")
    await status.edit_text("\n".join(lines), parse_mode="HTML")

async def confirm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    pending = get_pending_signal(uid)
    if not pending:
        await update.message.reply_text(
            "سیگنال معلقی نیست.\nاول <code>/predict نماد</code> سپس <code>/confirm</code>",
            parse_mode="HTML",
        )
        return
    if get_open_position(uid):
        await update.message.reply_text("⚠️ پوزیشن باز دارید. اول <code>/close</code>", parse_mode="HTML")
        return
    set_open_position(uid, pending["symbol"], pending["direction"], pending["entry"], pending["sl"], pending["tp"])
    clear_pending_signal(uid)
    await update.message.reply_text(
        f"✅ <b>ورود ثبت شد</b>\n\n"
        f"نماد: <code>{esc(pending['symbol'])}</code>\n"
        f"جهت: <b>{esc(pending['direction'])}</b>\n"
        f"ورود: <code>{p1(pending['entry'])}</code>\n"
        f"SL: <code>{p1(pending['sl'])}</code>\n"
        f"TP: <code>{p1(pending['tp'])}</code>\n\n"
        f"<code>/position</code>",
        parse_mode="HTML",
    )

async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or not is_admin(update.effective_user.id):
        return
    if not context.args:
        return
    try:
        uid = str(int(context.args[0]))
        days = int(context.args[1]) if len(context.args) > 1 else SUBSCRIPTION_DAYS
        users = load_json(USERS_FILE)
        users[uid] = {"expiry": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()}
        save_json(USERS_FILE, users)
        await update.message.reply_text(f"✅ {uid} برای {days} روز فعال شد.")
    except Exception as e:
        await update.message.reply_text(str(e))

async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or not is_admin(update.effective_user.id):
        return
    if not context.args:
        return
    uid = str(context.args[0])
    users = load_json(USERS_FILE)
    if uid in users:
        del users[uid]
        save_json(USERS_FILE, users)
        await update.message.reply_text("حذف شد.")

async def listusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or not is_admin(update.effective_user.id):
        return
    users = load_json(USERS_FILE)
    if not users:
        await update.message.reply_text("خالی")
        return
    lines = [f"<code>{u}</code> → {remaining_days(int(u))} روز" for u in users]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def log_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if len(context.args) < 4:
        await update.message.reply_text("<code>/log نماد جهت ورود خروج [pnl] [یادداشت]</code>", parse_mode="HTML")
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
            except Exception:
                note = " ".join(context.args[4:])
        journal = load_json(JOURNAL_FILE)
        trades = journal.get(str(uid), [])
        trades.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol, "direction": direction,
            "entry": entry, "exit": exit_p, "pnl": pnl, "note": note,
        })
        journal[str(uid)] = trades
        save_json(JOURNAL_FILE, journal)
        close_position(uid)
        clear_pending_signal(uid)
        msg = f"✅ ثبت شد.\n<code>{esc(symbol)}</code> | {esc(direction)} | PnL: <code>{pnl}</code>"
        if note:
            msg += f"\n{esc(note)}"
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"خطا: {esc(str(e)[:80])}")

async def performance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
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
    winrate = (len(wins) / total * 100) if total else 0
    text = (
        f"📊 <b>عملکرد</b>\n\nکل: <code>{total}</code> | برد: <code>{len(wins)}</code> | باخت: <code>{len(losses)}</code>\n"
        f"وین‌ریت: <code>{winrate:.1f}%</code>\nمجموع PnL: <code>{sum(pnls):.2f}</code>"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def position_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    pos = get_open_position(uid)
    if not pos:
        await update.message.reply_text("📭 پوزیشن بازی نیست.")
        return
    multi = get_multi_exchange_prices(pos["symbol"])
    price = multi["consensus"] or multi["binance"]
    text = (
        f"📌 <b>پوزیشن باز</b>\n\nنماد: <code>{esc(pos['symbol'])}</code>\nجهت: <b>{esc(pos['direction'])}</b>\n"
        f"ورود: <code>{p1(pos['entry'])}</code>\nSL: <code>{p1(pos['sl'])}</code>\nTP: <code>{p1(pos['tp'])}</code>\n"
    )
    if price:
        if pos["direction"] == "LONG":
            pnl_pct = (price - pos["entry"]) / pos["entry"] * 100
        else:
            pnl_pct = (pos["entry"] - price) / pos["entry"] * 100
        text += f"قیمت: <code>{p1(price)}</code> | PnL: <code>{pnl_pct:.2f}%</code>\n"
        text += f"منابع: {format_price_sources(multi)}\n"
    await update.message.reply_text(text, parse_mode="HTML")

async def close_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if close_position(uid):
        clear_pending_signal(uid)
        await update.message.reply_text("✅ پوزیشن بسته شد.")
    else:
        await update.message.reply_text("📭 پوزیشن بازی نیست.")

async def market_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not check_rate(uid):
        await update.message.reply_text("⏳ کمی صبر کنید.")
        return
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    wl = get_watchlist(uid)
    if not wl:
        await update.message.reply_text("واچ‌لیست خالی.\n<code>/watch BTCUSDT</code>", parse_mode="HTML")
        return
    status = await update.message.reply_text("⏳ ...")
    try:
        lines = ["🌐 <b>واچ‌لیست</b>\n"]
        for symbol in wl:
            try:
                df_1d = fetch_klines(symbol, "1d", 120)
                df_4h = fetch_klines(symbol, "4h", 150)
                if df_1d is None or df_4h is None:
                    lines.append(f"• <code>{esc(symbol)}</code>: داده نیست")
                    continue
                nds_d = analyze_nds(df_1d)
                nds_4h = analyze_nds(df_4h)
                higher = nds_d["bias"] if nds_d["bias"] != "خنثی" else nds_4h["bias"]
                multi = get_multi_exchange_prices(symbol)
                price = multi["consensus"] or float(df_4h["close"].iloc[-1])
                emoji = "🟢" if higher == "صعودی" else "🔴" if higher == "نزولی" else "⚪️"
                lines.append(f"{emoji} <code>{esc(symbol)}</code> → <b>{esc(higher)}</b> | <code>{p1(price)}</code>")
            except Exception:
                lines.append(f"• <code>{esc(symbol)}</code>: خطا")
        await status.edit_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await status.edit_text(f"❌ {esc(str(e)[:80])}")

async def nds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not check_rate(uid):
        await update.message.reply_text("⏳ کمی صبر کنید.")
        return
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    symbol = context.args[0].upper() if context.args else "BTCUSDT"
    status = await update.message.reply_text(f"⏳ NDS هفتگی <code>{esc(symbol)}</code> ...", parse_mode="HTML")
    try:
        df_1w = fetch_klines(symbol, "1w", 150)
        df_15m = fetch_klines(symbol, "15m", 200)
        if df_1w is None or len(df_1w) < 30:
            await status.edit_text("❌ داده هفتگی ناکافی.")
            return
        df_1w = add_indicators(df_1w)
        multi = get_multi_exchange_prices(symbol)
        price = multi["consensus"] or float(df_1w["close"].iloc[-1])
        geo = analyze_nds_official(df_1w, df_ltf=df_15m, price=price)
        higher = geo["bias"]

        buf = create_nds_weekly_chart(df_1w, f"𓄂 ηοsτrαμεhrδαδ𓆃 | NDS | {symbol} | 1W | {higher}", geo)
        caption = (
            f"🧬 <b>NDS رسمی | {esc(symbol)} | هفتگی</b>\n\n"
            f"بایاس: <b>{esc(higher)}</b> | {esc(geo['cycle'])}\n"
            f"مجاز: {'✅ بله' if geo['trade_allowed'] else '❌ خیر'}\n"
            f"{esc(geo['reason'])}\n\n"
            f"مقاومت: <code>{p1(geo['last_high']) if geo.get('last_high') else '—'}</code>\n"
            f"حمایت: <code>{p1(geo['last_low']) if geo.get('last_low') else '—'}</code>\n"
            f"سطح ۸۶٫۴٪: <code>{p1(geo['level_864']) if geo.get('level_864') else '—'}</code>\n"
        )
        if geo.get("n1") and geo.get("n2"):
            caption += f"Node باز N1/N2: <code>{p1(geo['n1'])}</code> / <code>{p1(geo['n2'])}</code>\n"
        caption += f"قیمت: <code>{p1(price)}</code>\nمنابع: {format_price_sources(multi)}"
        if geo.get("hooks"):
            labels = ", ".join([h.get("label", "?") for h in geo["hooks"][-6:]])
            caption += f"\nهوک/فلگ: <code>{esc(labels)}</code>"
        await update.message.reply_photo(photo=buf, caption=caption, parse_mode="HTML")

        multi_nds = analyze_nds_multi(symbol, current_price=price)
        lines = ["📊 <b>NDS تایم‌فریم‌ها</b>\n(۱۵م / ۲س / ۴س / ۸س / ۱۲س / روزانه)\n"]
        for r in multi_nds:
            if not r["ok"]:
                lines.append(f"• {esc(r['label'])}: داده ناکافی")
                continue
            emoji = "🟢" if r["bias"] == "صعودی" else "🔴" if r["bias"] == "نزولی" else "⚪️"
            low_txt = p1(r["tf_low"]) if r.get("tf_low") is not None else "—"
            dist = f"{r['dist_pct']:+.1f}%" if r.get("dist_pct") is not None else "—"
            rng = p1(r["range_val"]) if r.get("range_val") is not None else "—"
            lines.append(
                f"{emoji} {esc(r['label'])}: {esc(r['cycle'])}→<b>{esc(r['bias'])}</b> | کف <code>{low_txt}</code> | {dist} | ({rng})"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ {esc(str(e)[:120])}")

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
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
        margin = float(args[1]) if len(args) > 1 else DEFAULT_MARGIN
        lev = float(args[2]) if len(args) > 2 else DEFAULT_LEVERAGE
    except Exception:
        margin, lev = DEFAULT_MARGIN, DEFAULT_LEVERAGE
    open_pos = get_open_position(uid)
    status = await update.message.reply_text(f"⏳ تحلیل <code>{esc(symbol)}</code> ...", parse_mode="HTML")
    try:
        df_4h = fetch_klines(symbol, "4h", 250)
        df_15m = fetch_klines(symbol, "15m", 200)
        if df_4h is None or len(df_4h) < 80:
            await status.edit_text("❌ خطا در دریافت داده.")
            return
        df_4h = add_indicators(df_4h)
        multi = get_multi_exchange_prices(symbol)
        price = multi["consensus"] or float(df_4h["close"].iloc[-1])
        rsi = float(df_4h["rsi"].iloc[-1])
        atr = float(calc_atr(df_4h).iloc[-1])
        vol_ratio = (
            float(df_4h["volume"].iloc[-1] / df_4h["volume"].tail(20).mean())
            if df_4h["volume"].tail(20).mean() > 0 else 1.0
        )
        macd_hist = float(df_4h["macd_hist"].iloc[-1]) if "macd_hist" in df_4h.columns else None
        funding_info = get_funding_info(symbol)
        funding = funding_info["rate"]
        next_fund = funding_info["next_time"]
        fund_src = funding_info.get("source")

        geo = analyze_nds_official(df_4h, df_ltf=df_15m, price=price)
        multi_nds = analyze_nds_multi(symbol, current_price=price)
        rets = np.diff(np.log(df_4h["close"].values[-80:]))
        mu, sigma = np.mean(rets), np.std(rets)
        sims = np.array([price * np.exp(np.cumsum(np.random.normal(mu, sigma, 2))) for _ in range(40)])
        mean_path = np.mean(sims, axis=0)
        short_bias = mean_path[-1] > price

        score, reasons, risks, higher = calculate_signal_score(
            multi_nds, rsi, funding, vol_ratio, geo["bias"], short_bias, macd_hist, geo
        )

        # آن‌چین فقط برای BTC
        onchain = None
        if symbol in ("BTCUSDT", "BTCUSD", "BTC"):
            onchain = get_btc_onchain_realized(live_price=price)
            if onchain and onchain.get("ok"):
                if onchain.get("sth_mvrv") is not None and onchain["sth_mvrv"] < 0.92:
                    risks.append(f"STH زیر هزینه خرید (MVRV {onchain['sth_mvrv']})")
                    score = max(0, score - 3)
                if onchain.get("lth_mvrv") is not None and onchain["lth_mvrv"] < 1.02:
                    reasons.append(f"نزدیک LTH RP / MVRV {onchain['lth_mvrv']} — منطقه حساس چرخه‌ای")
                    score = min(100, score + 4)
                if onchain.get("lth_mvrv") is not None and onchain["lth_mvrv"] > 2.8:
                    risks.append(f"LTH MVRV بسیار بالا ({onchain['lth_mvrv']}) — ریسک توزیع")
                    score = max(0, score - 5)

        if geo.get("trade_allowed") and higher in ("صعودی", "نزولی") and score >= MIN_SIGNAL_SCORE:
            if higher == "صعودی":
                direction, signal, is_strong = "LONG", "🟢 LONG", True
            else:
                direction, signal, is_strong = "SHORT", "🔴 SHORT", True
        else:
            direction, signal, is_strong = None, "⚪️ صبر", False
            if not geo.get("trade_allowed"):
                risks.append(geo.get("reason") or "ساختار NDS هنوز مجوز ورود نداده")

        if direction:
            sl, tp = calculate_dollar_sl_tp(
                price, direction, atr, margin, lev, RISK_PER_TRADE,
                structure_low=geo.get("last_low"), structure_high=geo.get("last_high"),
                sl_hint=geo.get("sl_hint"), tp_hint=geo.get("tp_hint"),
            )
            entry = round(price, 1)
        else:
            entry, sl, tp = round(price, 1), round(price - atr * 1.6, 1), round(price + atr * 1.6, 1)

        position_size = margin * lev
        risk_usd = abs(entry - sl) / entry * position_size if entry else 0
        reward_usd = abs(tp - entry) / entry * position_size if entry else 0
        rr = reward_usd / risk_usd if risk_usd > 0 else 0
        fibs = calc_fib(df_4h)
        obs = detect_order_blocks(df_4h)

        buf = create_pro_chart(
            df_4h, f"𓄂 ηοsτrαμεhrδαδ𓆃 | {symbol} | 4H | {higher}",
            fibs=fibs, entry=entry, sl=sl, tp=tp, sims=sims, mean_path=mean_path,
            level_864=geo.get("level_864"), show_nds_hooks=False, max_bars=100,
        )

        base = symbol.replace("USDT", "")
        glass = f"https://www.coinglass.com/currencies/{base}/funding-rate"
        if funding is not None:
            fund_line = f"<code>{funding:.4f}%</code>"
            if fund_src:
                fund_line += f" ({esc(fund_src)})"
            if next_fund:
                fund_line += f" | بعدی: <code>{esc(next_fund)}</code>"
        else:
            fund_line = f'<a href="{glass}">Coinglass</a>'

        plan = (
            f"📋 <b>پلن | {esc(symbol)}</b>\n\n"
            f"سیگنال: <b>{signal}</b>\n"
            f"امتیاز: <code>{score}/100</code> (حداقل <code>{MIN_SIGNAL_SCORE}</code>)\n"
            f"ورود: <code>{p1(entry)}</code>\n"
            f"SL: <code>{p1(sl)}</code>\n"
            f"TP: <code>{p1(tp)}</code>\n\n"
            f"💵 ریسک: <code>${risk_usd:.2f}</code> | سود: <code>${reward_usd:.2f}</code> | R:R <code>1:{rr:.2f}</code>\n\n"
            f"<b>ساختار NDS رسمی</b>\n"
            f"• بایاس: <b>{esc(higher)}</b> | {esc(geo.get('cycle'))}\n"
            f"• مجوز: {'✅' if geo.get('trade_allowed') else '❌'}\n"
            f"• {esc(geo.get('reason'))}\n"
        )
        if geo.get("last_high"):
            plan += f"• مقاومت: <code>{p1(geo['last_high'])}</code>\n"
        if geo.get("last_low"):
            plan += f"• حمایت: <code>{p1(geo['last_low'])}</code>\n"
        if geo.get("level_864"):
            plan += f"• سطح ۸۶٫۴٪: <code>{p1(geo['level_864'])}</code>\n"
        if geo.get("n1") and geo.get("n2"):
            plan += f"• Node باز N1/N2: <code>{p1(geo['n1'])}</code> / <code>{p1(geo['n2'])}</code>\n"

        if onchain and onchain.get("ok"):
            plan += "\n<b>آن‌چین BTC</b>\n"
            if onchain.get("sth_realized_price"):
                plan += f"• STH RP: <code>{p1(onchain['sth_realized_price'])}</code>"
                if onchain.get("sth_mvrv") is not None:
                    plan += f" | MVRV <code>{onchain['sth_mvrv']}</code>"
                plan += "\n"
            if onchain.get("lth_realized_price"):
                plan += f"• LTH RP: <code>{p1(onchain['lth_realized_price'])}</code>"
                if onchain.get("lth_mvrv") is not None:
                    plan += f" | MVRV <code>{onchain['lth_mvrv']}</code>"
                plan += "\n"
            else:
                plan += "• LTH RP: — (اختیاری: COINGLASS_API_KEY)\n"
            if onchain.get("realized_price"):
                plan += f"• Realized کلی: <code>{p1(onchain['realized_price'])}</code>\n"

        if obs:
            plan += "\n<b>اوردر بلاک</b>\n"
            for ob in obs:
                tag = "صعودی" if ob["type"] == "bullish_ob" else "نزولی"
                plan += f"• OB {tag}: <code>{p1(ob['low'])}</code>–<code>{p1(ob['high'])}</code>\n"
        plan += f"\n💰 فاندینگ: {fund_line}\n📡 {format_price_sources(multi)}"
        if open_pos:
            plan += f"\n\n📌 پوزیشن باز: <code>{esc(open_pos['symbol'])}</code>"

        await update.message.reply_photo(photo=buf, caption=plan, parse_mode="HTML")

        reason_text = "📌 <b>دلیل:</b>\n"
        reason_text += "\n".join([f"• {esc(r)}" for r in reasons]) if reasons else "• تأیید کافی نیست"
        reason_text += "\n\n⚠️ <b>ریسک:</b>\n"
        reason_text += "\n".join([f"• {esc(r)}" for r in risks]) if risks else "• مورد خاصی نیست"
        await update.message.reply_text(reason_text, parse_mode="HTML")

        if is_strong and direction and not open_pos:
            set_pending_signal(uid, {"symbol": symbol, "direction": direction, "entry": entry, "sl": sl, "tp": tp})
            await update.message.reply_text(
                f"✅ <b>سیگنال معتبر</b>\n\n"
                f"جهت: <b>{esc(direction)}</b>\n"
                f"ورود: <code>{p1(entry)}</code> | SL: <code>{p1(sl)}</code> | TP: <code>{p1(tp)}</code>\n\n"
                f"👉 <code>/confirm</code>",
                parse_mode="HTML",
            )
        elif open_pos:
            await update.message.reply_text("ℹ️ پوزیشن باز دارید. فقط تحلیل نمایش داده شد.")
        else:
            await update.message.reply_text("⏸ ورود مجاز نیست. صبر تا NDS + امتیاز تأیید شوند.")

        await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ {esc(str(e)[:120])}")

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
    application.add_handler(CommandHandler("onchain", onchain_command))
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

    print("𓄂 ηοsτrαμεhrδαδ𓆃 آماده | NDS + OnChain STH/LTH + HTML")
    application.run_polling()

if __name__ == "__main__":
    main()