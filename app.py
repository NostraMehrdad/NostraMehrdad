# 𓄂 ηοsτrαμεhrδαδ𓆃 | Kronos Stable
# Pre-Alert + Pullback + Entry Chart | NO auto-entry | 4H closed | Notify
# Defaults: BTCUSDT / QNTUSDT | Price up to 4 decimals
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
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
)

try:
    import ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False

app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "𓄂 ηοsτrαμεhrδαδ𓆃 | Kronos Stable | Entry Chart + Pre-Alert + Pullback | NO auto-entry"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
COINGLASS_API_KEY = os.environ.get("COINGLASS_API_KEY", "").strip()

USDT_WALLET = "TTrC39T9eHNwpGmUu5FKzMWyEHELAmFV5S"
SUBSCRIPTION_PRICE = 10
SUBSCRIPTION_DAYS = 30

# جفت‌های پیش‌فرض
DEFAULT_SYMBOLS = ["BTCUSDT", "QNTUSDT"]

USERS_FILE = "users.json"
JOURNAL_FILE = "journal.json"
POSITION_FILE = "position.json"
ALERT_STATE_FILE = "alert_state.json"
PENDING_FILE = "pending_signal.json"
WATCHLIST_FILE = "watchlist.json"
PRICE_ALERTS_FILE = "price_alerts.json"
ONCHAIN_CACHE_FILE = "onchain_btc_cache.json"
SIGNAL_LOCK_FILE = "signal_locks.json"
NOTIFIED_SIGNAL_FILE = "notified_signals.json"
PRE_ALERT_FILE = "pre_alerts.json"
PULLBACK_ZONE_FILE = "pullback_zones.json"

DEFAULT_MARGIN = 5.0
DEFAULT_LEVERAGE = 8
RISK_PER_TRADE = 0.5
RATE_LIMIT_SECONDS = 45
MONITOR_INTERVAL = 75
DANGER_PERCENT = 0.35
PULLBACK_ALERT_PCT = 0.012
MIN_SIGNAL_SCORE = 70
PRE_ALERT_SCORE_MIN = 60
MIN_RANGE_MOVE_PCT = 3.0
NDS_86 = 0.864
ONCHAIN_CACHE_HOURS = 18
MIN_TF_ALIGN = 3
ALT_BTC_BLOCK_SCORE = 80
SIGNAL_LOCK_HOURS = 3
CAPTION_MAX = 1000
MIN_RR = 1.5
PRE_ALERT_COOLDOWN_MIN = 45

NDS_INTERVALS = [
    ("15m", "۱۵ دقیقه"), ("2h", "۲ ساعت"), ("4h", "۴ ساعت"),
    ("8h", "۸ ساعت"), ("12h", "۱۲ ساعت"), ("1d", "روزانه"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
user_last_request = defaultdict(float)

DISCLAIMER = (
    "⚠️ تحلیل آماری است، نه توصیه مالی. ورود خودکار انجام نمی‌شود؛ "
    "فقط با دستور شما (<code>/confirm</code>). داده عمدتاً عمومی است و با XT.com ممکن است فرق داشته باشد."
)

def esc(x):
    return html.escape(str(x) if x is not None else "")

def pfmt(x):
    """
    نمایش قیمت:
    - اگر قیمت کمتر از ۱ واحد باشد → تا ۴ رقم اعشار
    - قیمت‌های بزرگ‌تر با اعشار کمتر برای خوانایی
    """
    try:
        v = float(x)
        av = abs(v)
        if av < 1:
            return f"{v:.4f}"   # هر ارزی زیر ۱ واحد → ۴ رقم اعشار
        if av < 10:
            return f"{v:.4f}"   # مثل بسیاری آلت‌ها / QNT
        if av < 100:
            return f"{v:.3f}"
        if av < 1000:
            return f"{v:.2f}"
        return f"{v:.1f}"       # BTC و مشابه
    except Exception:
        return str(x)

# سازگاری با نام قدیمی
p1 = pfmt

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

def clip_caption(text, limit=CAPTION_MAX):
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 12] + "\n…"

def unique_lines(items, max_n=12):
    seen = set()
    out = []
    for x in items or []:
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= max_n:
            break
    return out

# -------------------- سشن بازار (UTC) --------------------

def get_market_session(now=None):
    now = now or datetime.now(timezone.utc)
    h = now.hour
    if 13 <= h < 16:
        return {"name": "هم‌پوشانی اروپا–آمریکا", "quality": "high", "score_boost": 3,
                "min_score_extra": 0, "note": "بهترین نقدینگی روزانه.", "utc_hour": h}
    if 16 <= h < 21:
        return {"name": "آمریکا", "quality": "high", "score_boost": 2,
                "min_score_extra": 0, "note": "حجم بالا؛ روندها معمولاً واضح‌ترند.", "utc_hour": h}
    if 7 <= h < 13:
        return {"name": "اروپا", "quality": "medium", "score_boost": 1,
                "min_score_extra": 2, "note": "نقدینگی متوسط تا خوب.", "utc_hour": h}
    if 0 <= h < 7:
        return {"name": "آسیا", "quality": "medium", "score_boost": 0,
                "min_score_extra": 4, "note": "فقط سیگنال خیلی قوی.", "utc_hour": h}
    return {"name": "پس از آمریکا / کم‌نقدینگی", "quality": "low", "score_boost": -4,
            "min_score_extra": 8, "note": "فقط امتیاز خیلی بالا.", "utc_hour": h}

def effective_min_score(session):
    return MIN_SIGNAL_SCORE + int(session.get("min_score_extra", 0))

# -------------------- پوزیشن / کاربران --------------------

def get_open_position(uid):
    return load_json(POSITION_FILE).get(str(uid))

def set_open_position(uid, symbol, direction, entry, sl, tp):
    data = load_json(POSITION_FILE)
    data[str(uid)] = {
        "symbol": symbol, "direction": direction, "entry": entry, "sl": sl, "tp": tp,
        "opened_at": datetime.now(timezone.utc).isoformat(), "manual_confirm": True,
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
        "symbol": symbol.upper(), "target": float(target), "condition": condition,
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

# -------------------- قفل سیگنال + Pre-Alert + Pullback --------------------

def get_signal_lock(uid, symbol):
    data = load_json(SIGNAL_LOCK_FILE)
    key = f"{uid}:{symbol.upper()}"
    item = data.get(key)
    if not item:
        return None
    try:
        if datetime.now(timezone.utc) > datetime.fromisoformat(item["expiry"]):
            del data[key]
            save_json(SIGNAL_LOCK_FILE, data)
            return None
        return item
    except Exception:
        return None

def set_signal_lock(uid, symbol, direction, entry, sl, tp, score, reason, session_name, candle_close_ts=None):
    data = load_json(SIGNAL_LOCK_FILE)
    key = f"{uid}:{symbol.upper()}"
    data[key] = {
        "direction": direction, "entry": float(entry), "sl": float(sl), "tp": float(tp),
        "score": score, "reason": reason, "session": session_name,
        "candle_close_ts": candle_close_ts,
        "created": datetime.now(timezone.utc).isoformat(),
        "expiry": (datetime.now(timezone.utc) + timedelta(hours=SIGNAL_LOCK_HOURS)).isoformat(),
    }
    save_json(SIGNAL_LOCK_FILE, data)

def clear_signal_lock(uid, symbol=None):
    data = load_json(SIGNAL_LOCK_FILE)
    if symbol:
        key = f"{uid}:{symbol.upper()}"
        if key in data:
            del data[key]
    else:
        for k in [k for k in list(data.keys()) if k.startswith(f"{uid}:")]:
            del data[k]
    save_json(SIGNAL_LOCK_FILE, data)

def was_notified(uid, symbol, candle_close_ts):
    data = load_json(NOTIFIED_SIGNAL_FILE)
    return bool(data.get(f"{uid}:{symbol}:{candle_close_ts}"))

def mark_notified(uid, symbol, candle_close_ts):
    data = load_json(NOTIFIED_SIGNAL_FILE)
    data[f"{uid}:{symbol}:{candle_close_ts}"] = datetime.now(timezone.utc).isoformat()
    if len(data) > 500:
        items = sorted(data.items(), key=lambda x: x[1])
        data = dict(items[-300:])
    save_json(NOTIFIED_SIGNAL_FILE, data)

def can_send_pre_alert(uid, symbol):
    data = load_json(PRE_ALERT_FILE)
    key = f"{uid}:{symbol.upper()}"
    item = data.get(key)
    if not item:
        return True
    try:
        last = datetime.fromisoformat(item["ts"])
        return (datetime.now(timezone.utc) - last).total_seconds() >= PRE_ALERT_COOLDOWN_MIN * 60
    except Exception:
        return True

def mark_pre_alert(uid, symbol, score, note):
    data = load_json(PRE_ALERT_FILE)
    key = f"{uid}:{symbol.upper()}"
    data[key] = {
        "score": score, "note": note,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    save_json(PRE_ALERT_FILE, data)

def set_pullback_zone(uid, symbol, direction, zone_low, zone_high, entry, sl, tp, score):
    data = load_json(PULLBACK_ZONE_FILE)
    key = f"{uid}:{symbol.upper()}"
    data[key] = {
        "direction": direction,
        "zone_low": float(zone_low),
        "zone_high": float(zone_high),
        "entry": float(entry),
        "sl": float(sl),
        "tp": float(tp),
        "score": score,
        "created": datetime.now(timezone.utc).isoformat(),
        "expiry": (datetime.now(timezone.utc) + timedelta(hours=SIGNAL_LOCK_HOURS)).isoformat(),
        "hit_notified": False,
    }
    save_json(PULLBACK_ZONE_FILE, data)

def get_pullback_zone(uid, symbol=None):
    data = load_json(PULLBACK_ZONE_FILE)
    if symbol:
        return data.get(f"{uid}:{symbol.upper()}")
    return {k: v for k, v in data.items() if k.startswith(f"{uid}:")}

def clear_pullback_zone(uid, symbol=None):
    data = load_json(PULLBACK_ZONE_FILE)
    if symbol:
        key = f"{uid}:{symbol.upper()}"
        if key in data:
            del data[key]
    else:
        for k in [k for k in list(data.keys()) if k.startswith(f"{uid}:")]:
            del data[k]
    save_json(PULLBACK_ZONE_FILE, data)

def compute_pullback_zone(direction, entry, atr, geo, price):
    """منطقه ورود ترجیحی روی پولبک — نزدیک ۸۶٫۴٪ یا سوینگ اخیر."""
    level = geo.get("level_864")
    last_low = geo.get("last_low")
    last_high = geo.get("last_high")
    buf = max(atr * 0.35, entry * 0.002)

    if direction == "LONG":
        candidates = []
        if level and level < entry:
            candidates.append(level)
        if last_low and last_low < entry:
            candidates.append(last_low)
        if candidates:
            zone_mid = max(candidates)
        else:
            zone_mid = entry - atr * 0.6
        zone_low = zone_mid - buf
        zone_high = min(entry - buf * 0.3, zone_mid + buf)
        if zone_high <= zone_low:
            zone_high = zone_low + buf
    else:
        candidates = []
        if level and level > entry:
            candidates.append(level)
        if last_high and last_high > entry:
            candidates.append(last_high)
        if candidates:
            zone_mid = min(candidates)
        else:
            zone_mid = entry + atr * 0.6
        zone_high = zone_mid + buf
        zone_low = max(entry + buf * 0.3, zone_mid - buf)
        if zone_high <= zone_low:
            zone_low = zone_high - buf

    return float(zone_low), float(zone_high)

# -------------------- داده بازار --------------------

def get_binance_price(symbol):
    try:
        r = requests.get(
            f"https://data-api.binance.vision/api/v3/ticker/price?symbol={symbol}",
            headers=HEADERS, timeout=8,
        )
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
        t = ccxt.coinbase({"enableRateLimit": True, "timeout": 10000}).fetch_ticker(f"{base}/USD")
        if t and t.get("last"):
            result["coinbase"] = float(t["last"])
    except Exception as e:
        print("Coinbase:", e)
    try:
        t = ccxt.kraken({"enableRateLimit": True, "timeout": 10000}).fetch_ticker(f"{base}/USD")
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
        parts.append(f"Binance: <code>{pfmt(multi['binance'])}</code>")
    if multi["coinbase"]:
        parts.append(f"Coinbase: <code>{pfmt(multi['coinbase'])}</code>")
    if multi["kraken"]:
        parts.append(f"Kraken: <code>{pfmt(multi['kraken'])}</code>")
    if multi["consensus"]:
        parts.append(f"اجماع: <code>{pfmt(multi['consensus'])}</code>")
    if multi.get("spread_pct") and multi["spread_pct"] > 0.15:
        parts.append(f"⚠️ اختلاف: <code>{multi['spread_pct']:.2f}%</code>")
    return " | ".join(parts) if parts else "قیمت در دسترس نیست"

def get_funding_info(symbol):
    result = {"rate": None, "next_time": None, "mark": None, "source": None, "detail": {}}
    pair = f"{symbol.replace('USDT', '')}/USDT:USDT"
    try:
        r = requests.get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}", headers=HEADERS, timeout=8)
        if r.status_code == 200:
            d = r.json()
            result["rate"] = float(d.get("lastFundingRate", 0)) * 100
            result["mark"] = float(d.get("markPrice", 0) or 0) or None
            next_ts = d.get("nextFundingTime")
            if next_ts:
                result["next_time"] = datetime.fromtimestamp(int(next_ts) / 1000, tz=timezone.utc).strftime("%H:%M UTC")
            result["source"] = "Binance"
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

# -------------------- آن‌چین BTC --------------------

def _load_onchain_cache():
    data = load_json(ONCHAIN_CACHE_FILE)
    if not data or "ts" not in data:
        return None
    try:
        age_h = (time.time() - float(data["ts"])) / 3600.0
        if age_h <= ONCHAIN_CACHE_HOURS:
            payload = data.get("payload") or {}
            payload["_cache_age_hours"] = round(age_h, 1)
            return payload
    except Exception:
        pass
    return None

def _save_onchain_cache(payload):
    save_json(ONCHAIN_CACHE_FILE, {"ts": time.time(), "payload": payload})

def fetch_explore21_realized():
    try:
        r = requests.get("https://data.explore21.com/v1/metrics/bitcoin-realized-price/latest", headers=HEADERS, timeout=12)
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
        }
    except Exception as e:
        print("Explore21:", e)
        return None

def fetch_coinglass_lth():
    if not COINGLASS_API_KEY:
        return None
    try:
        r = requests.get(
            "https://open-api-v4.coinglass.com/api/index/bitcoin-lth-realized-price",
            headers={**HEADERS, "CG-API-KEY": COINGLASS_API_KEY}, timeout=12,
        )
        if r.status_code != 200:
            return None
        arr = (r.json().get("data") or [])
        if not arr:
            return None
        last = arr[-1]
        lth = last.get("lth_realized_price") or last.get("lthRealizedPrice")
        return {"lth_realized_price": float(lth) if lth is not None else None, "source": "CoinGlass"}
    except Exception as e:
        print("CoinGlass LTH:", e)
        return None

def get_btc_onchain_realized(live_price=None):
    cached = _load_onchain_cache()
    if cached and live_price is None:
        return cached
    base = fetch_explore21_realized() or {}
    lth_data = fetch_coinglass_lth() or {}
    rp, sth, lth = base.get("realized_price"), base.get("sth_realized_price"), lth_data.get("lth_realized_price")
    px = live_price or base.get("btc_close") or get_binance_price("BTCUSDT")
    sth_mvrv = (px / sth) if (px and sth and sth > 0) else None
    lth_mvrv = (px / lth) if (px and lth and lth > 0) else None
    overall_mvrv = (px / rp) if (px and rp and rp > 0) else None
    notes = []
    if sth_mvrv is not None:
        if sth_mvrv < 0.95:
            notes.append(f"STH زیر هزینه خرید (MVRV {sth_mvrv:.2f})")
        elif sth_mvrv > 1.15:
            notes.append(f"STH در سود (MVRV {sth_mvrv:.2f})")
        else:
            notes.append(f"STH نزدیک سربه‌سر (MVRV {sth_mvrv:.2f})")
    if lth_mvrv is not None:
        notes.append(f"LTH MVRV {lth_mvrv:.2f}")
    else:
        notes.append("LTH RP: COINGLASS_API_KEY اختیاری")
    payload = {
        "ok": bool(rp or sth or lth), "date": base.get("date"), "price": px,
        "realized_price": rp, "sth_realized_price": sth, "lth_realized_price": lth,
        "sth_mvrv": round(sth_mvrv, 3) if sth_mvrv is not None else None,
        "lth_mvrv": round(lth_mvrv, 3) if lth_mvrv is not None else None,
        "overall_mvrv": round(overall_mvrv, 3) if overall_mvrv is not None else None,
        "notes": notes,
        "sources": {"explore21": base.get("source"), "coinglass": lth_data.get("source"),
                    "chart": base.get("chart") or "https://charts.explore21.com/bitcoin-realized-price"},
        "_cache_age_hours": 0.0,
    }
    if payload["ok"]:
        _save_onchain_cache(payload)
    return payload

def format_onchain_html(oc):
    if not oc or not oc.get("ok"):
        return "⛓ <b>آن‌چین BTC</b>\nداده در دسترس نیست."
    lines = ["⛓ <b>آن‌چین BTC | Realized Price</b>"]
    if oc.get("date"):
        lines.append(f"تاریخ داده: <code>{esc(oc['date'])}</code>")
    age = oc.get("_cache_age_hours")
    if age is not None:
        lines.append(f"سن کش: <code>{age}</code> ساعت")
    if oc.get("price"):
        lines.append(f"قیمت: <code>{pfmt(oc['price'])}</code>")
    if oc.get("sth_realized_price"):
        m = f" | MVRV <code>{oc['sth_mvrv']}</code>" if oc.get("sth_mvrv") is not None else ""
        lines.append(f"STH RP: <code>{pfmt(oc['sth_realized_price'])}</code>{m}")
    if oc.get("lth_realized_price"):
        m = f" | MVRV <code>{oc['lth_mvrv']}</code>" if oc.get("lth_mvrv") is not None else ""
        lines.append(f"LTH RP: <code>{pfmt(oc['lth_realized_price'])}</code>{m}")
    else:
        lines.append("LTH RP: — (اختیاری: COINGLASS_API_KEY)")
    if oc.get("realized_price"):
        m = f" | MVRV <code>{oc['overall_mvrv']}</code>" if oc.get("overall_mvrv") is not None else ""
        lines.append(f"Realized کلی: <code>{pfmt(oc['realized_price'])}</code>{m}")
    if oc.get("notes"):
        lines.append("\n<b>تفسیر:</b>")
        for n in oc["notes"][:5]:
            lines.append(f"• {esc(n)}")
    chart = (oc.get("sources") or {}).get("chart")
    if chart:
        lines.append(f'\n<a href="{chart}">Explore21</a>')
    return "\n".join(lines)

# -------------------- مانیتور: SL/TP + نوتیف + پولبک زون --------------------

def monitor_positions(bot: Bot):
    while True:
        try:
            positions = load_json(POSITION_FILE)
            alerts = load_json(ALERT_STATE_FILE)
            for uid_str, pos in list(positions.items()):
                try:
                    uid = int(uid_str)
                    symbol, direction = pos["symbol"], pos["direction"]
                    entry, sl, tp = float(pos["entry"]), float(pos["sl"]), float(pos["tp"])
                    price = get_current_price(symbol)
                    if price is None:
                        continue
                    user_alerts = alerts.get(uid_str, {})
                    hit_sl = (direction == "LONG" and price <= sl) or (direction == "SHORT" and price >= sl)
                    hit_tp = (direction == "LONG" and price >= tp) or (direction == "SHORT" and price <= tp)
                    if hit_sl:
                        close_position(uid)
                        clear_signal_lock(uid, symbol)
                        clear_pullback_zone(uid, symbol)
                        try:
                            bot.send_message(chat_id=uid, text=f"🛑 حد ضرر | <code>{esc(symbol)}</code> {pfmt(price)}", parse_mode="HTML")
                        except Exception:
                            pass
                        continue
                    if hit_tp:
                        close_position(uid)
                        clear_signal_lock(uid, symbol)
                        clear_pullback_zone(uid, symbol)
                        try:
                            bot.send_message(chat_id=uid, text=f"🎯 حد سود | <code>{esc(symbol)}</code> {pfmt(price)}", parse_mode="HTML")
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
                            bot.send_message(chat_id=uid, text=f"⚠️ نزدیک SL | <code>{esc(symbol)}</code> {pfmt(price)}", parse_mode="HTML")
                            user_alerts["sl_warned"] = True
                        except Exception:
                            pass
                    if near_tp and not user_alerts.get("tp_warned"):
                        try:
                            bot.send_message(chat_id=uid, text=f"🎯 نزدیک TP | <code>{esc(symbol)}</code> {pfmt(price)}", parse_mode="HTML")
                            user_alerts["tp_warned"] = True
                        except Exception:
                            pass
                    if pullback and not user_alerts.get("pullback_warned"):
                        try:
                            bot.send_message(chat_id=uid, text=f"🔄 پولبک | <code>{esc(symbol)}</code> {pfmt(price)}", parse_mode="HTML")
                            user_alerts["pullback_warned"] = True
                        except Exception:
                            pass
                    alerts[uid_str] = user_alerts
                except Exception as e:
                    print("Monitor pos:", e)
            save_json(ALERT_STATE_FILE, alerts)

            # نوتیف سیگنال معلق (۴H بسته)
            for uid_str, pend in list(load_json(PENDING_FILE).items()):
                try:
                    uid = int(uid_str)
                    if get_open_position(uid):
                        continue
                    c_ts = pend.get("candle_close_ts")
                    if not c_ts or was_notified(uid, pend["symbol"], c_ts):
                        continue
                    mark_notified(uid, pend["symbol"], c_ts)
                    try:
                        bot.send_message(
                            chat_id=uid,
                            text=(
                                f"🔔 <b>نوتیف فوری — سیگنال پس از بسته شدن کندل ۴H</b>\n\n"
                                f"<code>{esc(pend['symbol'])}</code> | <b>{esc(pend['direction'])}</b>\n"
                                f"ورود پیشنهادی <code>{pfmt(pend['entry'])}</code>\n"
                                f"SL <code>{pfmt(pend['sl'])}</code> | TP <code>{pfmt(pend['tp'])}</code>\n\n"
                                f"📍 ترجیح: صبر برای پولبک به منطقه ورود (اگر تعریف شده).\n"
                                f"⏱ فرصت واکنش داری — سفارش را خودت روی XT.com بزن.\n"
                                f"⛔ ربات ورود خودکار نمی‌کند.\n"
                                f"<code>/confirm</code> فقط ثبت ژورنال\n\n{DISCLAIMER}"
                            ),
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        print("Notify send:", e)
                except Exception as e:
                    print("Pending notify:", e)

            # نوتیف رسیدن قیمت به منطقه پولبک
            pb_data = load_json(PULLBACK_ZONE_FILE)
            pb_changed = False
            for key, zone in list(pb_data.items()):
                try:
                    if zone.get("hit_notified"):
                        continue
                    try:
                        if datetime.now(timezone.utc) > datetime.fromisoformat(zone["expiry"]):
                            del pb_data[key]
                            pb_changed = True
                            continue
                    except Exception:
                        pass
                    parts = key.split(":", 1)
                    if len(parts) != 2:
                        continue
                    uid = int(parts[0])
                    symbol = parts[1]
                    if get_open_position(uid):
                        continue
                    price = get_current_price(symbol)
                    if price is None:
                        continue
                    zlo, zhi = float(zone["zone_low"]), float(zone["zone_high"])
                    if zlo <= price <= zhi:
                        zone["hit_notified"] = True
                        pb_data[key] = zone
                        pb_changed = True
                        try:
                            bot.send_message(
                                chat_id=uid,
                                text=(
                                    f"📍 <b>قیمت وارد منطقه پولبک شد</b>\n\n"
                                    f"<code>{esc(symbol)}</code> | <b>{esc(zone['direction'])}</b>\n"
                                    f"قیمت الان: <code>{pfmt(price)}</code>\n"
                                    f"منطقه ورود: <code>{pfmt(zlo)}</code> – <code>{pfmt(zhi)}</code>\n"
                                    f"Entry پیشنهادی: <code>{pfmt(zone['entry'])}</code>\n"
                                    f"SL <code>{pfmt(zone['sl'])}</code> | TP <code>{pfmt(zone['tp'])}</code>\n\n"
                                    f"⏱ فرصت ورود روی پولبک.\n"
                                    f"⛔ ربات سفارش ثبت نمی‌کند — خودت روی XT.com اقدام کن.\n"
                                    f"<code>/confirm</code> برای ثبت ژورنال"
                                ),
                                parse_mode="HTML",
                            )
                        except Exception as e:
                            print("Pullback notify:", e)
                except Exception as e:
                    print("PB zone check:", e)
            if pb_changed:
                save_json(PULLBACK_ZONE_FILE, pb_data)

            # هشدار قیمت سفارشی
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
                        target, cond = float(a["target"]), a.get("condition", "above")
                        triggered = (cond == "above" and price >= target) or (cond == "below" and price <= target)
                        if triggered:
                            try:
                                bot.send_message(
                                    chat_id=int(uid_str),
                                    text=f"🚨 هشدار قیمت | <code>{esc(a['symbol'])}</code> → <code>{pfmt(price)}</code>",
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
    try:
        r = requests.get(
            f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}",
            headers=HEADERS, timeout=14,
        )
        if r.status_code == 200:
            df = pd.DataFrame(
                r.json(),
                columns=["open_time", "open", "high", "low", "close", "volume",
                         "close_time", "qav", "num_trades", "taker_base", "taker_quote", "ignore"],
            )
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = df[c].astype(float)
            return df
    except Exception as e:
        print("Fetch:", e)
    return None

def closed_klines(df):
    if df is None or len(df) < 3:
        return df
    return df.iloc[:-1].copy()

def last_closed_candle_ts(df_full):
    if df_full is None or len(df_full) < 2:
        return None
    try:
        return int(df_full["open_time"].iloc[-2])
    except Exception:
        return None

def safe_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)

def calc_atr(df, period=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
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

def nearby_fibs(fibs, price, max_n=4):
    """فیبوهای نزدیک به قیمت فعلی برای نمودار سیگنال."""
    if not fibs:
        return {}
    items = sorted(fibs.items(), key=lambda x: abs(float(x[1]) - price))
    return dict(items[:max_n])

def format_levels_block(price, df, fibs, geo):
    lines = ["📐 <b>سطوح پیش‌رو (۴H بسته‌شده)</b>"]
    last = df.iloc[-1]
    for name, key in [("SMA20", "sma20"), ("SMA50", "sma50"), ("SMA200", "sma200"),
                      ("EMA20", "ema20"), ("EMA50", "ema50")]:
        if key in df.columns and pd.notna(last.get(key)):
            v = float(last[key])
            dist = (price - v) / price * 100
            side = "بالای" if price > v else "زیر"
            lines.append(f"• {name}: <code>{pfmt(v)}</code> ({side} | {dist:+.2f}%)")
    if "bb_upper" in df.columns and pd.notna(last.get("bb_upper")):
        up, mid, lo = float(last["bb_upper"]), float(last["bb_mid"]), float(last["bb_lower"])
        lines.append(f"• BB U <code>{pfmt(up)}</code> | M <code>{pfmt(mid)}</code> | L <code>{pfmt(lo)}</code>")
        if up > lo:
            lines.append(f"• داخل باند: <code>{(price - lo) / (up - lo) * 100:.0f}%</code>")
    if fibs:
        below = sorted([(k, v) for k, v in fibs.items() if v < price], key=lambda x: -x[1])[:2]
        above = sorted([(k, v) for k, v in fibs.items() if v > price], key=lambda x: x[1])[:2]
        for k, v in above:
            lines.append(f"• فیبو↑ {k}: <code>{pfmt(v)}</code> ({(v - price) / price * 100:+.2f}%)")
        for k, v in below:
            lines.append(f"• فیبو↓ {k}: <code>{pfmt(v)}</code> ({(v - price) / price * 100:+.2f}%)")
    if geo.get("level_864"):
        lv = float(geo["level_864"])
        lines.append(f"• ۸۶٫۴٪: <code>{pfmt(lv)}</code> ({(lv - price) / price * 100:+.2f}%)")
    return "\n".join(lines)

def classify_market_regime(df):
    if df is None or len(df) < 30:
        return {"regime": "نامشخص", "atr_pct": None, "vol_ratio": 1.0}
    close = float(df["close"].iloc[-1])
    atr = float(calc_atr(df).iloc[-1] or 0)
    atr_pct = (atr / close * 100) if close else 0
    mid = float(df["close"].rolling(20).mean().iloc[-1] or close)
    std = float(df["close"].rolling(20).std().iloc[-1] or 0)
    bb_width = (4 * std / mid * 100) if mid else 0
    vol_ma = float(df["volume"].tail(20).mean() or 1)
    vol_ratio = float(df["volume"].iloc[-1] / vol_ma) if vol_ma > 0 else 1.0
    sma50 = df["close"].rolling(50).mean()
    slope = 0.0
    if len(sma50.dropna()) >= 10:
        s = sma50.dropna().tail(10).values
        slope = (s[-1] - s[0]) / s[0] * 100 if s[0] else 0
    if atr_pct >= 2.2 or bb_width >= 8:
        regime = "پرنوسان"
    elif abs(slope) >= 1.2 and atr_pct >= 0.6:
        regime = "رونددار"
    else:
        regime = "رنج"
    return {"regime": regime, "atr_pct": round(atr_pct, 2), "vol_ratio": round(vol_ratio, 2)}

def count_tf_alignment(nds_results):
    ok = [r for r in nds_results if r.get("ok")]
    bull = sum(1 for r in ok if r.get("bias") == "صعودی")
    bear = sum(1 for r in ok if r.get("bias") == "نزولی")
    higher = [r for r in ok if r.get("interval") in ("1d", "12h")]
    higher_bull = sum(1 for r in higher if r.get("bias") == "صعودی")
    higher_bear = sum(1 for r in higher if r.get("bias") == "نزولی")
    higher_bias = "صعودی" if higher_bull > higher_bear else "نزولی" if higher_bear > higher_bull else "خنثی"
    return {
        "bull": bull, "bear": bear, "higher_bias": higher_bias,
        "align_long_ok": bull >= MIN_TF_ALIGN and higher_bias != "نزولی",
        "align_short_ok": bear >= MIN_TF_ALIGN and higher_bias != "صعودی",
    }

def get_btc_structure_bias():
    try:
        df_4h = fetch_klines("BTCUSDT", "4h", 120)
        df_1d = fetch_klines("BTCUSDT", "1d", 80)
        if df_4h is None or len(df_4h) < 50:
            return {"bias": "خنثی", "detail": "—"}
        g4 = analyze_nds(closed_klines(df_4h))
        gd = analyze_nds(closed_klines(df_1d)) if df_1d is not None and len(df_1d) >= 40 else {"bias": "خنثی"}
        bias = gd["bias"] if gd.get("bias") in ("صعودی", "نزولی") else g4.get("bias", "خنثی")
        return {"bias": bias, "detail": f"1D:{gd.get('bias')} 4H:{g4.get('bias')}"}
    except Exception as e:
        print("BTC structure:", e)
        return {"bias": "خنثی", "detail": "خطا"}

# ===================== NDS =====================

def detect_swing_nodes(df, lb=3, atr_mult=0.75):
    if df is None or len(df) < lb * 4 + 10:
        return []
    atr = calc_atr(df).bfill()
    hs, ls = df["high"].values, df["low"].values
    nodes = []
    for i in range(lb, len(df) - lb):
        if hs[i] == max(hs[i - lb: i + lb + 1]) and (hs[i] - min(ls[i - lb: i + 1])) > float(atr.iloc[i]) * atr_mult:
            nodes.append({"type": "high", "price": float(hs[i]), "index": i})
        if ls[i] == min(ls[i - lb: i + lb + 1]) and (max(hs[i - lb: i + 1]) - ls[i]) > float(atr.iloc[i]) * atr_mult:
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
        hh += 1 if highs[i]["price"] > highs[i - 1]["price"] else 0
        lh += 0 if highs[i]["price"] > highs[i - 1]["price"] else 1
    for i in range(1, len(lows)):
        hl += 1 if lows[i]["price"] > lows[i - 1]["price"] else 0
        ll += 0 if lows[i]["price"] > lows[i - 1]["price"] else 1
    return hh, hl, lh, ll, highs, lows

def detect_open_nodes_direction(nodes):
    if len(nodes) < 4:
        return "خنثی", None, None
    highs = [n for n in nodes if n["type"] == "high"]
    lows = [n for n in nodes if n["type"] == "low"]
    if len(highs) >= 2 and len(lows) >= 2:
        n1h, n2h, n1l, n2l = highs[-2], highs[-1], lows[-2], lows[-1]
        if n2h["price"] > n1h["price"] and n2l["price"] > n1l["price"]:
            return "صعودی", n1h, n2h
        if n2h["price"] < n1h["price"] and n2l["price"] < n1l["price"]:
            return "نزولی", n1l, n2l
    return "خنثی", None, None

def detect_hook_structure(nodes):
    if len(nodes) < 4:
        return []
    hooks, h_i, l_i = [], 0, 0
    for n in nodes[-12:]:
        if n["type"] == "high":
            h_i += 1
            if h_i <= 3:
                hooks.append({**n, "label": f"H{h_i}", "kind": "hook"})
        else:
            l_i += 1
            if l_i <= 3:
                hooks.append({**n, "label": f"F{l_i}", "kind": "flag"})
    return hooks

def find_123_pattern(df, direction_bias, lookback=50):
    if df is None or len(df) < lookback:
        return None
    sub = df.tail(lookback).reset_index(drop=True)
    nodes = detect_swing_nodes(sub, lb=2, atr_mult=0.55)
    if len(nodes) < 3:
        return None
    n1, n2, n3 = nodes[-3], nodes[-2], nodes[-1]
    if direction_bias == "صعودی" and n1["type"] == "low" and n2["type"] == "high" and n3["type"] == "low":
        if n3["price"] > n1["price"] and n2["price"] > n3["price"]:
            return {"ok": True, "kind": "123_bull", "p1": n1["price"], "p2": n2["price"], "p3": n3["price"]}
    if direction_bias == "نزولی" and n1["type"] == "high" and n2["type"] == "low" and n3["type"] == "high":
        if n3["price"] < n1["price"] and n2["price"] < n3["price"]:
            return {"ok": True, "kind": "123_bear", "p1": n1["price"], "p2": n2["price"], "p3": n3["price"]}
    return None

def analyze_nds_official(df_htf, df_ltf=None, price=None):
    result = {
        "bias": "خنثی", "cycle": "—", "trade_allowed": False, "reason": "جهت نامشخص",
        "last_high": None, "last_low": None, "nodes": [], "hooks": [], "level_864": None,
        "pattern_123": None, "n1": None, "n2": None, "entry_zone": None,
        "tf_low": None, "tf_high": None, "sl_hint": None, "tp_hint": None, "expected_move_pct": 0.0,
    }
    if df_htf is None or len(df_htf) < 40:
        result["reason"] = "داده ناکافی"
        return result
    price = float(price if price is not None else df_htf["close"].iloc[-1])
    result["tf_low"] = float(df_htf["low"].tail(80).min())
    result["tf_high"] = float(df_htf["high"].tail(80).max())
    nodes = detect_swing_nodes(df_htf, lb=3, atr_mult=0.85)
    result["nodes"], result["hooks"] = nodes, detect_hook_structure(nodes)
    hh, hl, lh, ll, highs, lows = count_hh_ll(nodes)
    bias, n1, n2 = detect_open_nodes_direction(nodes)
    result["bias"] = bias
    result["n1"] = n1["price"] if n1 else None
    result["n2"] = n2["price"] if n2 else None
    if highs:
        result["last_high"] = highs[-1]["price"]
    if lows:
        result["last_low"] = lows[-1]["price"]
    result["cycle"] = "HH+HL" if bias == "صعودی" else "LH+LL" if bias == "نزولی" else "رنج"
    level = None
    if len(nodes) >= 2:
        a, b = nodes[-2], nodes[-1]
        if a["type"] != b["type"]:
            level = level_864(a["price"], b["price"])
    result["level_864"] = level
    target_ref = level or (result["last_high"] if bias == "صعودی" else result["last_low"])
    if target_ref and price > 0:
        result["expected_move_pct"] = abs(target_ref - price) / price * 100
    pattern = None
    if df_ltf is not None and len(df_ltf) >= 40 and bias in ("صعودی", "نزولی"):
        pattern = find_123_pattern(df_ltf, bias, lookback=50)
        result["pattern_123"] = pattern
    near_864 = level is not None and price > 0 and abs(price - level) / price <= 0.018
    if bias not in ("صعودی", "نزولی"):
        result["reason"] = "دو Node باز واضح نیست"
        return result
    if bias == "صعودی" and (near_864 or (pattern and pattern.get("kind") == "123_bull")):
        if result["expected_move_pct"] < MIN_RANGE_MOVE_PCT and not (pattern and near_864):
            result["reason"] = f"صعودی اما حرکت ضعیف ({result['expected_move_pct']:.1f}%)"
        else:
            result["trade_allowed"] = True
            result["reason"] = "صعودی + ۱۲۳/۸۶٫۴٪"
            if pattern:
                result["sl_hint"] = pattern["p3"] * 0.997
                result["tp_hint"] = result["n2"] or result["last_high"]
    elif bias == "نزولی" and (near_864 or (pattern and pattern.get("kind") == "123_bear")):
        if result["expected_move_pct"] < MIN_RANGE_MOVE_PCT and not (pattern and near_864):
            result["reason"] = f"نزولی اما حرکت ضعیف ({result['expected_move_pct']:.1f}%)"
        else:
            result["trade_allowed"] = True
            result["reason"] = "نزولی + ۱۲۳/۸۶٫۴٪"
            if pattern:
                result["sl_hint"] = pattern["p3"] * 1.003
                result["tp_hint"] = result["n2"] or result["last_low"]
    else:
        result["reason"] = f"جهت {bias}؛ ۱۲۳/۸۶٫۴٪ کامل نیست"
    return result

def analyze_nds(df):
    geo = analyze_nds_official(df, None, float(df["close"].iloc[-1]) if df is not None and len(df) else None)
    return {
        "bias": geo["bias"], "cycle": geo["cycle"], "last_high": geo["last_high"], "last_low": geo["last_low"],
        "nodes": geo["nodes"], "hooks": geo["hooks"], "tf_low": geo["tf_low"], "tf_high": geo["tf_high"],
        "level_864": geo["level_864"], "trade_allowed": geo["trade_allowed"], "reason": geo["reason"],
        "pattern_123": geo.get("pattern_123"), "n1": geo.get("n1"), "n2": geo.get("n2"),
    }

def analyze_nds_multi(symbol, current_price=None):
    results = []
    for interval, label in NDS_INTERVALS:
        df = fetch_klines(symbol, interval, 200 if interval == "1d" else 150)
        if df is None or len(df) < 50:
            results.append({"interval": interval, "label": label, "bias": "—", "ok": False})
            continue
        df_c = closed_klines(df) if interval in ("4h", "1d", "12h", "8h") else df
        info = analyze_nds(df_c if df_c is not None and len(df_c) >= 40 else df)
        dist_pct = None
        if info.get("tf_low") is not None and current_price:
            dist_pct = (current_price - info["tf_low"]) / current_price * 100
        results.append({
            "interval": interval, "label": label, "bias": info["bias"], "cycle": info["cycle"],
            "tf_low": info.get("tf_low"), "dist_pct": dist_pct, "ok": True,
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
        if rng <= 0 or atr <= 0 or body <= atr * 0.8 or body / rng <= 0.55:
            continue
        direction = "bullish" if sub["close"].iloc[i] > sub["open"].iloc[i] else "bearish"
        if direction == "bullish" and sub["low"].iloc[i + 1: i + 3].min() >= sub["low"].iloc[i] * 0.998:
            blocks.append({"type": "bullish_ob", "low": float(sub["low"].iloc[i]), "high": float(sub["high"].iloc[i])})
        if direction == "bearish" and sub["high"].iloc[i + 1: i + 3].max() <= sub["high"].iloc[i] * 1.002:
            blocks.append({"type": "bearish_ob", "low": float(sub["low"].iloc[i]), "high": float(sub["high"].iloc[i])})
    return blocks[-3:]

def calculate_signal_score(
    nds_results, rsi, funding, volume_ratio, higher_bias,
    macd_hist=None, geo=None, regime_info=None, tf_align=None,
    btc_bias=None, is_alt=False, session=None,
):
    score, reasons, risks = 50.0, [], []
    if session:
        reasons.append(f"سشن: {session['name']} UTC{session['utc_hour']:02d} | {session['quality']}")
        score += float(session.get("score_boost", 0))
        if session["quality"] == "low":
            risks.append(session["note"])
    if geo and geo.get("trade_allowed"):
        score += 18
        reasons.append(f"NDS: {geo.get('reason')}")
        higher_bias = geo.get("bias") or higher_bias
    elif geo:
        score -= 6
        risks.append(geo.get("reason") or "NDS ناکافی")
    if tf_align:
        reasons.append(f"TF ↑{tf_align['bull']} ↓{tf_align['bear']} بالاتر:{tf_align['higher_bias']}")
        if higher_bias == "صعودی":
            if tf_align["align_long_ok"]:
                score += 12
            else:
                score -= 10
                risks.append("هم‌راستایی LONG ناکافی")
        elif higher_bias == "نزولی":
            if tf_align["align_short_ok"]:
                score += 12
            else:
                score -= 10
                risks.append("هم‌راستایی SHORT ناکافی")
    if regime_info:
        reg = regime_info.get("regime", "—")
        reasons.append(f"رژیم {reg} ATR {regime_info.get('atr_pct')}% Vol {regime_info.get('vol_ratio')}x")
        if reg == "رنج" and volume_ratio is not None and volume_ratio < 1.15:
            score -= 8
            risks.append("رنج+حجم ضعیف")
        elif reg == "رونددار":
            score += 4
        elif reg == "پرنوسان":
            score -= 4
            risks.append("پرنوسان")
    if is_alt and btc_bias:
        bb = btc_bias.get("bias", "خنثی")
        reasons.append(f"BTC: {bb}")
        if bb == "نزولی" and higher_bias == "صعودی":
            score -= 12
            risks.append("BTC نزولی")
    if 40 <= rsi <= 60:
        score += 4
        reasons.append(f"RSI {rsi:.1f}")
    elif rsi > 72:
        score -= 8
        risks.append(f"RSI اشباع {rsi:.1f}")
    elif rsi < 28:
        score -= 8
        risks.append(f"RSI اشباع {rsi:.1f}")
    if funding is not None:
        if abs(funding) > 0.08:
            score -= 3
            risks.append(f"فاندینگ {funding:.4f}%")
        else:
            reasons.append(f"فاندینگ {funding:.4f}%")
    if volume_ratio is not None:
        if volume_ratio >= 1.15:
            score += 3
        elif volume_ratio < 0.55:
            score -= 3
            risks.append(f"حجم {volume_ratio:.2f}x")
    if macd_hist is not None and higher_bias in ("صعودی", "نزولی"):
        if (higher_bias == "صعودی" and macd_hist > 0) or (higher_bias == "نزولی" and macd_hist < 0):
            score += 4
            reasons.append("MACD هم‌راستا")
        else:
            score -= 2
            risks.append("MACD خلاف")
    reasons.append("مبنای سیگنال: کندل ۴H بسته")
    return max(0, min(100, round(score, 1))), unique_lines(reasons), unique_lines(risks), higher_bias

def calculate_dollar_sl_tp(
    entry, direction, atr, margin=5.0, leverage=8, risk_dollar=0.5,
    structure_low=None, structure_high=None, sl_hint=None, tp_hint=None, min_rr=MIN_RR,
):
    position_size = margin * leverage
    price_risk = (risk_dollar / position_size) * entry
    final_distance = max(price_risk, atr * 1.5 * 0.85)
    if direction == "LONG":
        sl = entry - final_distance
        if sl_hint and sl_hint < entry:
            sl = min(sl, sl_hint)
        elif structure_low and structure_low < entry:
            sl = min(sl, structure_low - atr * 0.1)
        risk_dist = entry - sl
        tp = entry + risk_dist * min_rr
        if tp_hint and tp_hint > entry:
            tp = max(tp, tp_hint)
    else:
        sl = entry + final_distance
        if sl_hint and sl_hint > entry:
            sl = max(sl, sl_hint)
        elif structure_high and structure_high > entry:
            sl = max(sl, structure_high + atr * 0.1)
        risk_dist = sl - entry
        tp = entry - risk_dist * min_rr
        if tp_hint and tp_hint < entry:
            tp = min(tp, tp_hint)
    # دقت اعشار بر اساس اندازه entry
    if abs(entry) >= 100:
        return round(sl, 1), round(tp, 1)
    if abs(entry) >= 10:
        return round(sl, 2), round(tp, 2)
    return round(sl, 4), round(tp, 4)

# -------------------- نمودارها --------------------

def draw_candles(ax, df, width=0.55):
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    for i in range(len(df)):
        color = "#2ed573" if c[i] >= o[i] else "#ff4757"
        ax.plot([i, i], [l[i], h[i]], color=color, lw=0.85, zorder=3)
        bottom = min(o[i], c[i])
        height = abs(c[i] - o[i]) or (h[i] - l[i]) * 0.02 or 1e-8
        ax.add_patch(Rectangle((i - width / 2, bottom), width, height, facecolor=color, edgecolor=color, lw=0.35, zorder=4))

def draw_hook_marker(ax, x, y, label, is_high=True):
    color = "#ff4757" if is_high else "#1dd1a1"
    size = max(abs(y) * 0.008, 0.8)
    ax.scatter([x], [y], s=55, c=color, marker="s", zorder=15, edgecolors="white", linewidths=0.6)
    ty = y + size * 3.5 if is_high else y - size * 3.5
    ax.annotate(label, xy=(x, y), xytext=(x, ty), fontsize=7.5, ha="center", va="center",
                color=color, fontweight="bold", zorder=16,
                arrowprops=dict(arrowstyle="-", color=color, lw=0.8, alpha=0.7))

def _price_formatter(price_sample):
    """فرمت محور قیمت بر اساس اندازه عدد."""
    av = abs(float(price_sample or 1))
    if av >= 1000:
        return FormatStrFormatter("%.1f")
    if av >= 10:
        return FormatStrFormatter("%.2f")
    return FormatStrFormatter("%.4f")

def create_pro_chart(
    df, title, fibs=None, entry=None, sl=None, tp=None,
    sims=None, mean_path=None, level_864=None, show_nds_hooks=False, nds_info=None,
    pullback_zone=None, max_bars=100,
):
    plot_df = df.tail(max_bars).copy().reset_index(drop=True)
    n = len(plot_df)
    price_sample = float(plot_df["close"].iloc[-1])
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
            ax.text(n + 0.6, v, f"{k} {pfmt(v)}", fontsize=5.8, va="center", color=c)
    if level_864 is not None:
        ax.axhline(level_864, color="#f1c40f", lw=1.4, ls="-.", alpha=0.95, label="86.4%")
        ax.text(n + 0.6, level_864, f"86.4% {pfmt(level_864)}", fontsize=6.5, va="center", color="#f1c40f", fontweight="bold")
    if pullback_zone:
        zlo, zhi = pullback_zone
        ax.axhspan(zlo, zhi, color="#3498db", alpha=0.15, label="Pullback Zone")
    if entry is not None:
        ax.axhline(entry, color="#2980b9", lw=1.2, label="Entry")
    if sl is not None:
        ax.axhline(sl, color="#c0392b", lw=1.2, label="SL")
    if tp is not None:
        ax.axhline(tp, color="#27ae60", lw=1.2, label="TP")
    if sims is not None and mean_path is not None:
        last, horizon = n - 1, len(mean_path)
        for s in sims[::3]:
            ax.plot(range(last, last + horizon), s[:horizon], color="#ffffff", alpha=0.12, lw=0.6)
        ax.plot(range(last, last + horizon), mean_path, color="#ffffff", lw=1.5, label="Pred", zorder=6)
    if show_nds_hooks and nds_info:
        offset = len(df) - n
        for node in (nds_info.get("hooks") or []):
            x = node["index"] - offset
            if 0 <= x < n:
                draw_hook_marker(ax, x, node["price"], node.get("label", "?"), is_high=(node.get("type") == "high"))
    ax.set_title(title, color="white", fontsize=10.5, pad=6)
    ax.legend(loc="upper left", fontsize=5.5, facecolor="#1a1a1a", edgecolor="#333", labelcolor="white", ncol=4)
    ax.grid(True, alpha=0.12, color="#444")
    ax.yaxis.set_major_formatter(_price_formatter(price_sample))
    ax.set_xlim(-1, n + 10)
    if "macd" in plot_df.columns:
        hist = plot_df["macd_hist"].values
        ax_macd.bar(range(n), hist, color=["#2ed573" if v >= 0 else "#ff4757" for v in hist], alpha=0.65, width=0.6)
        ax_macd.plot(plot_df["macd"].values, color="#00bfff", lw=0.9)
        ax_macd.plot(plot_df["macd_signal"].values, color="#f39c12", lw=0.85)
        ax_macd.axhline(0, color="#555", lw=0.5, alpha=0.5)
    ax_macd.set_ylabel("MACD", color="#888", fontsize=6.2)
    ax_macd.grid(True, alpha=0.10, color="#444")
    ax_macd.tick_params(labelbottom=False)
    ax_rsi.plot(plot_df["rsi"].values, color="#e056fd", lw=0.9)
    ax_rsi.axhline(70, color="#e74c3c", ls="--", lw=0.55, alpha=0.5)
    ax_rsi.axhline(30, color="#2ecc71", ls="--", lw=0.55, alpha=0.5)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel("RSI", color="#888", fontsize=6.2)
    ax_rsi.grid(True, alpha=0.10, color="#444")
    plt.setp(ax.get_xticklabels(), visible=False)
    plt.setp(ax_macd.get_xticklabels(), visible=False)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", dpi=150, facecolor="#0d0d0d")
    buf.seek(0)
    plt.close()
    return buf

def create_entry_signal_chart(df, symbol, direction, entry, sl, tp, fibs=None, level_864=None, pullback_zone=None, max_bars=80):
    """
    نمودار جداگانه فقط برای سیگنال قطعی:
    Entry / SL / TP برچسب‌دار + EMA20/50 + ATR + فیبوهای نزدیک حرکت.
    """
    plot_df = df.tail(max_bars).copy().reset_index(drop=True)
    n = len(plot_df)
    price_sample = float(plot_df["close"].iloc[-1])
    fig = plt.figure(figsize=(13.5, 7.2), facecolor="#0d0d0d")
    ax = fig.add_subplot(111)
    ax.set_facecolor("#0d0d0d")
    ax.tick_params(colors="#bbbbbb", labelsize=7)
    for spine in ax.spines.values():
        spine.set_color("#333333")
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")

    draw_candles(ax, plot_df, width=0.6)

    # فقط EMA20 و EMA50
    if "ema20" in plot_df.columns:
        ax.plot(plot_df["ema20"].values, color="#2ecc71", lw=1.4, label="EMA20", zorder=5)
    if "ema50" in plot_df.columns:
        ax.plot(plot_df["ema50"].values, color="#e74c3c", lw=1.5, label="EMA50", zorder=5)

    # ATR به‌صورت نقطه‌چین
    if "atr_upper" in plot_df.columns:
        ax.plot(plot_df["atr_upper"].values, color="#95a5a6", lw=1.0, ls=":", alpha=0.85, label="ATR+")
        ax.plot(plot_df["atr_lower"].values, color="#95a5a6", lw=1.0, ls=":", alpha=0.85, label="ATR-")

    # فیبوهای نزدیک حرکت
    near = nearby_fibs(fibs, entry or price_sample, max_n=5) if fibs else {}
    fib_colors = {"0.0": "#ecf0f1", "0.236": "#e74c3c", "0.382": "#e67e22", "0.5": "#f1c40f",
                  "0.618": "#2ecc71", "0.786": "#3498db", "1.0": "#9b59b6"}
    for k, v in near.items():
        c = fib_colors.get(k, "#aaaaaa")
        ax.axhline(v, color=c, ls="--", lw=0.9, alpha=0.8)
        ax.text(n + 0.8, v, f"Fib {k}  {pfmt(v)}", fontsize=7, va="center", color=c, fontweight="bold")

    if level_864 is not None:
        ax.axhline(level_864, color="#f1c40f", lw=1.5, ls="-.", alpha=0.95)
        ax.text(n + 0.8, level_864, f"86.4%  {pfmt(level_864)}", fontsize=7.5, va="center",
                color="#f1c40f", fontweight="bold")

    if pullback_zone:
        zlo, zhi = pullback_zone
        ax.axhspan(zlo, zhi, color="#3498db", alpha=0.18, label="Pullback Zone")

    # Entry / SL / TP با برچسب واضح
    if entry is not None:
        ax.axhline(entry, color="#2980b9", lw=2.0, zorder=8)
        ax.annotate(
            f"ENTRY  {pfmt(entry)}",
            xy=(n - 1, entry), xytext=(n * 0.55, entry),
            fontsize=9, color="#5dade2", fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="#5dade2", lw=1.2),
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#1a1a2e", edgecolor="#2980b9", alpha=0.9),
            zorder=12,
        )
    if sl is not None:
        ax.axhline(sl, color="#c0392b", lw=1.8, zorder=8)
        ax.annotate(
            f"SL  {pfmt(sl)}",
            xy=(n - 1, sl), xytext=(n * 0.35, sl),
            fontsize=8.5, color="#e74c3c", fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="#e74c3c", lw=1.1),
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#1a1a2e", edgecolor="#c0392b", alpha=0.9),
            zorder=12,
        )
    if tp is not None:
        ax.axhline(tp, color="#27ae60", lw=1.8, zorder=8)
        ax.annotate(
            f"TP  {pfmt(tp)}",
            xy=(n - 1, tp), xytext=(n * 0.35, tp),
            fontsize=8.5, color="#2ecc71", fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="#2ecc71", lw=1.1),
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#1a1a2e", edgecolor="#27ae60", alpha=0.9),
            zorder=12,
        )

    dir_emoji = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
    ax.set_title(f"سیگنال قطعی | {symbol} | 4H | {dir_emoji}", color="white", fontsize=12, pad=8, fontweight="bold")
    ax.legend(loc="upper left", fontsize=6.5, facecolor="#1a1a1a", edgecolor="#333", labelcolor="white", ncol=3)
    ax.grid(True, alpha=0.14, color="#444")
    ax.yaxis.set_major_formatter(_price_formatter(price_sample))
    ax.set_xlim(-1, n + 14)

    # حاشیه عمودی برای خوانایی برچسب‌ها
    ys = [y for y in [entry, sl, tp, level_864] if y is not None]
    if near:
        ys.extend(near.values())
    if ys:
        ymin, ymax = min(ys), max(ys)
        pad = (ymax - ymin) * 0.12 or price_sample * 0.01
        ax.set_ylim(ymin - pad, ymax + pad)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", dpi=155, facecolor="#0d0d0d")
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
    sess = get_market_session()
    await update.message.reply_text(
        "🤖 <b>𓄂 ηοsτrαμεhrδαδ𓆃 | Kronos Stable</b>\n\n"
        f"🕒 سشن: <b>{esc(sess['name'])}</b> (UTC {sess['utc_hour']:02d})\n\n"
        "⛔ ورود خودکار هرگز انجام نمی‌شود.\n"
        "سیگنال فقط پس از بسته شدن کندل ۴H.\n"
        "🔔 Pre-Alert + نوتیف منطقه پولبک + نمودار جداگانه Entry.\n"
        f"جفت‌های پیش‌فرض: <code>{DEFAULT_SYMBOLS[0]}</code> و <code>{DEFAULT_SYMBOLS[1]}</code>\n"
        "ثبت فقط با <code>/confirm</code>.\n\n"
        "<code>/predict</code> <code>/nds</code> <code>/onchain</code> <code>/market</code>\n"
        "<code>/confirm</code> <code>/position</code> <code>/close</code>\n"
        "<code>/alert</code> <code>/watch</code> <code>/log</code> <code>/performance</code>\n"
        "<code>/buy</code> <code>/status</code> <code>/tools</code>",
        parse_mode="HTML",
    )

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    await update.message.reply_text(
        f"💳 اشتراک ۳۰ روزه\n{SUBSCRIPTION_PRICE} USDT TRC20\n<code>{USDT_WALLET}</code>",
        parse_mode="HTML",
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if is_subscribed(uid):
        await update.message.reply_text(f"✅ فعال — {remaining_days(uid)} روز", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ اشتراک فعال نیست.")

async def tools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    await update.message.reply_text(
        '🛠 <a href="https://www.coinglass.com">Coinglass</a> | '
        '<a href="https://charts.explore21.com/bitcoin-realized-price">Explore21</a> | '
        '<a href="https://www.lookonchain.com">Lookonchain</a>',
        parse_mode="HTML", disable_web_page_preview=True,
    )

async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    await update.message.reply_text(
        '📰 <a href="https://www.coinglass.com">Coinglass</a> | <a href="https://www.lookonchain.com">Lookonchain</a>',
        parse_mode="HTML", disable_web_page_preview=True,
    )

async def onchain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not check_rate(uid):
        await update.message.reply_text("⏳ صبر کنید.")
        return
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    status = await update.message.reply_text("⏳ ...")
    try:
        multi = get_multi_exchange_prices("BTCUSDT")
        oc = get_btc_onchain_realized(live_price=multi["consensus"] or multi["binance"])
        await status.edit_text(format_onchain_html(oc) + "\n" + format_price_sources(multi), parse_mode="HTML")
    except Exception as e:
        await status.edit_text(f"❌ {esc(str(e)[:100])}")

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
        symbol, target, condition = context.args[0].upper(), float(context.args[1]), context.args[2].lower()
        if condition not in ("above", "below"):
            await update.message.reply_text("above یا below")
            return
        a = add_price_alert(uid, symbol, target, condition)
        await update.message.reply_text(f"✅ <code>{a['id']}</code> | {esc(symbol)} {condition} {pfmt(target)}", parse_mode="HTML")
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
    lines = ["🚨 هشدارها"] + [f"• <code>{a['id']}</code> {esc(a['symbol'])} {a['condition']} {pfmt(a['target'])}" for a in alerts]
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
    await update.message.reply_text("✅" if n else "پیدا نشد")

async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if not context.args:
        await update.message.reply_text("<code>/watch BTCUSDT</code>", parse_mode="HTML")
        return
    result = add_to_watchlist(uid, [s.upper() for s in context.args])
    await update.message.reply_text("✅ " + ", ".join(result))

async def unwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        return
    if context.args:
        remove_from_watchlist(uid, [s.upper() for s in context.args])
    await update.message.reply_text("✅")

async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    wl = get_watchlist(uid)
    if not wl:
        await update.message.reply_text("واچ‌لیست خالی.")
        return
    status = await update.message.reply_text("⏳ ...")
    lines = ["👀 واچ‌لیست"]
    for symbol in wl:
        try:
            df_4h = fetch_klines(symbol, "4h", 100)
            df_1d = fetch_klines(symbol, "1d", 80)
            if df_4h is None:
                lines.append(f"• {symbol}: —")
                continue
            nds_d = analyze_nds(closed_klines(df_1d)) if df_1d is not None else {"bias": "—"}
            nds_4h = analyze_nds(closed_klines(df_4h))
            higher = nds_d["bias"] if nds_d.get("bias") != "خنثی" else nds_4h["bias"]
            price = float(df_4h["close"].iloc[-1])
            emoji = "🟢" if higher == "صعودی" else "🔴" if higher == "نزولی" else "⚪️"
            lines.append(f"{emoji} <code>{esc(symbol)}</code> {esc(higher)} <code>{pfmt(price)}</code>")
        except Exception:
            lines.append(f"• {symbol}: خطا")
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
        await update.message.reply_text("معلقی نیست. اول <code>/predict</code>", parse_mode="HTML")
        return
    if get_open_position(uid):
        await update.message.reply_text("پوزیشن باز دارید.")
        return
    set_open_position(uid, pending["symbol"], pending["direction"], pending["entry"], pending["sl"], pending["tp"])
    clear_pending_signal(uid)
    clear_pullback_zone(uid, pending["symbol"])
    await update.message.reply_text(
        f"✅ ثبت دستی\n<code>{esc(pending['symbol'])}</code> {esc(pending['direction'])}\n"
        f"ورود {pfmt(pending['entry'])} | SL {pfmt(pending['sl'])} | TP {pfmt(pending['tp'])}\n"
        f"سفارش صرافی با خودتان است.",
        parse_mode="HTML",
    )

async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or not is_admin(update.effective_user.id) or not context.args:
        return
    try:
        uid = str(int(context.args[0]))
        days = int(context.args[1]) if len(context.args) > 1 else SUBSCRIPTION_DAYS
        users = load_json(USERS_FILE)
        users[uid] = {"expiry": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()}
        save_json(USERS_FILE, users)
        await update.message.reply_text(f"✅ {uid} — {days}d")
    except Exception as e:
        await update.message.reply_text(str(e))

async def removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or not is_admin(update.effective_user.id) or not context.args:
        return
    users = load_json(USERS_FILE)
    uid = str(context.args[0])
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
    await update.message.reply_text("\n".join([f"<code>{u}</code> {remaining_days(int(u))}d" for u in users]), parse_mode="HTML")

async def log_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    if len(context.args) < 4:
        await update.message.reply_text("<code>/log نماد جهت ورود خروج [pnl]</code>", parse_mode="HTML")
        return
    try:
        symbol, direction = context.args[0].upper(), context.args[1].upper()
        entry, exit_p = float(context.args[2]), float(context.args[3])
        pnl = float(context.args[4]) if len(context.args) >= 5 else 0.0
        journal = load_json(JOURNAL_FILE)
        trades = journal.get(str(uid), [])
        trades.append({
            "time": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "direction": direction,
            "entry": entry, "exit": exit_p, "pnl": pnl, "note": " ".join(context.args[5:]) if len(context.args) > 5 else "",
        })
        journal[str(uid)] = trades
        save_json(JOURNAL_FILE, journal)
        close_position(uid)
        clear_pending_signal(uid)
        clear_signal_lock(uid, symbol)
        clear_pullback_zone(uid, symbol)
        await update.message.reply_text(f"✅ PnL {pnl}")
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
        await update.message.reply_text("معامله‌ای نیست.")
        return
    pnls = [float(t.get("pnl", 0)) for t in trades]
    wins = [p for p in pnls if p > 0]
    wr = len(wins) / len(trades) * 100 if trades else 0
    await update.message.reply_text(f"📊 {len(trades)} | برد {len(wins)} | {wr:.1f}% | PnL {sum(pnls):.2f}")

async def position_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    pos = get_open_position(uid)
    if not pos:
        await update.message.reply_text("📭 پوزیشن باز نیست.")
        return
    multi = get_multi_exchange_prices(pos["symbol"])
    price = multi["consensus"] or multi["binance"]
    text = f"📌 <code>{esc(pos['symbol'])}</code> {esc(pos['direction'])}\nورود {pfmt(pos['entry'])} SL {pfmt(pos['sl'])} TP {pfmt(pos['tp'])}\n"
    if price:
        pnl_pct = ((price - pos["entry"]) / pos["entry"] * 100) if pos["direction"] == "LONG" else ((pos["entry"] - price) / pos["entry"] * 100)
        text += f"قیمت {pfmt(price)} | PnL {pnl_pct:.2f}%\n{format_price_sources(multi)}"
    await update.message.reply_text(text, parse_mode="HTML")

async def close_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    pos = get_open_position(uid)
    if close_position(uid):
        clear_pending_signal(uid)
        if pos:
            clear_signal_lock(uid, pos.get("symbol"))
            clear_pullback_zone(uid, pos.get("symbol"))
        await update.message.reply_text("✅ بسته شد.")
    else:
        await update.message.reply_text("📭 نیست.")

async def market_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not check_rate(uid):
        await update.message.reply_text("⏳ صبر کنید.")
        return
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    wl = get_watchlist(uid)
    if not wl:
        await update.message.reply_text("واچ‌لیست خالی.")
        return
    status = await update.message.reply_text("⏳ ...")
    lines = [f"🌐 {esc(get_market_session()['name'])}"]
    for symbol in wl:
        try:
            df_4h = fetch_klines(symbol, "4h", 100)
            df_1d = fetch_klines(symbol, "1d", 80)
            if df_4h is None:
                lines.append(f"• {symbol}: —")
                continue
            nds_d = analyze_nds(closed_klines(df_1d)) if df_1d is not None else {"bias": "—"}
            nds_4h = analyze_nds(closed_klines(df_4h))
            higher = nds_d["bias"] if nds_d.get("bias") != "خنثی" else nds_4h["bias"]
            emoji = "🟢" if higher == "صعودی" else "🔴" if higher == "نزولی" else "⚪️"
            lines.append(f"{emoji} <code>{esc(symbol)}</code> {esc(higher)} <code>{pfmt(df_4h['close'].iloc[-1])}</code>")
        except Exception:
            lines.append(f"• {symbol}: خطا")
    await status.edit_text("\n".join(lines), parse_mode="HTML")

async def nds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not check_rate(uid):
        await update.message.reply_text("⏳ صبر کنید.")
        return
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    # بدون آرگومان → پرسش پیش‌فرض
    if not context.args:
        keyboard = [
            [
                InlineKeyboardButton("BTCUSDT", callback_data="nds:BTCUSDT"),
                InlineKeyboardButton("QNTUSDT", callback_data="nds:QNTUSDT"),
            ]
        ]
        await update.message.reply_text(
            "کدام جفت‌ارز پیش‌فرض را برای NDS تحلیل کنم؟",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return
    symbol = context.args[0].upper()
    await _run_nds(update, symbol)

async def _run_nds(update: Update, symbol: str):
    status = await update.message.reply_text(f"⏳ NDS {esc(symbol)}", parse_mode="HTML")
    try:
        df_1w = fetch_klines(symbol, "1w", 150)
        df_15m = fetch_klines(symbol, "15m", 200)
        if df_1w is None or len(df_1w) < 30:
            await status.edit_text("❌ داده ناکافی.")
            return
        df_1w = add_indicators(df_1w)
        multi = get_multi_exchange_prices(symbol)
        price = multi["consensus"] or float(df_1w["close"].iloc[-1])
        geo = analyze_nds_official(df_1w, df_ltf=df_15m, price=price)
        buf = create_nds_weekly_chart(df_1w, f"NDS | {symbol} | 1W", geo)
        caption = clip_caption(
            f"🧬 <b>{esc(symbol)}</b> | {esc(geo['bias'])} | {esc(geo['cycle'])}\n"
            f"مجاز: {'✅' if geo['trade_allowed'] else '❌'} | {esc(geo['reason'])}\n"
            f"مقاومت {pfmt(geo.get('last_high'))} | حمایت {pfmt(geo.get('last_low'))}\n"
            f"۸۶٫۴٪ {pfmt(geo.get('level_864'))} | قیمت {pfmt(price)}"
        )
        await update.message.reply_photo(photo=buf, caption=caption, parse_mode="HTML")
        multi_nds = analyze_nds_multi(symbol, current_price=price)
        lines = ["📊 NDS تایم‌فریم‌ها"]
        for r in multi_nds:
            if not r["ok"]:
                lines.append(f"• {r['label']}: —")
                continue
            emoji = "🟢" if r["bias"] == "صعودی" else "🔴" if r["bias"] == "نزولی" else "⚪️"
            lines.append(f"{emoji} {esc(r['label'])}: <b>{esc(r['bias'])}</b>")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ {esc(str(e)[:120])}")

async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update):
        return
    uid = update.effective_user.id
    if not check_rate(uid):
        await update.message.reply_text("⏳ صبر کنید.")
        return
    if not is_subscribed(uid) and not is_admin(uid):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return

    args = context.args
    # بدون نماد → پرسش پیش‌فرض BTC / QNT
    if not args:
        keyboard = [
            [
                InlineKeyboardButton("BTCUSDT", callback_data="predict:BTCUSDT"),
                InlineKeyboardButton("QNTUSDT", callback_data="predict:QNTUSDT"),
            ]
        ]
        await update.message.reply_text(
            "کدام جفت‌ارز پیش‌فرض را تحلیل کنم؟\n"
            f"(یا بنویس: <code>/predict BTCUSDT</code> / <code>/predict QNTUSDT</code>)",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        return

    symbol = args[0].upper()
    try:
        margin = float(args[1]) if len(args) > 1 else DEFAULT_MARGIN
        lev = float(args[2]) if len(args) > 2 else DEFAULT_LEVERAGE
    except Exception:
        margin, lev = DEFAULT_MARGIN, DEFAULT_LEVERAGE

    await _run_predict(update, uid, symbol, margin, lev)

async def symbol_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پاسخ به دکمه‌های پیش‌فرض BTC / QNT برای /predict و /nds."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = (query.data or "").strip()
    if ":" not in data:
        return
    action, symbol = data.split(":", 1)
    symbol = symbol.upper()
    uid = query.from_user.id
    if not is_subscribed(uid) and not is_admin(uid):
        await query.edit_message_text("❌ دسترسی ندارید.")
        return
    # برای پیام‌های callback، update.message ممکن است None باشد؛ از query.message استفاده می‌کنیم
    class _FakeUpdate:
        def __init__(self, message, effective_user, effective_chat):
            self.message = message
            self.effective_user = effective_user
            self.effective_chat = effective_chat
    fake = _FakeUpdate(query.message, query.from_user, query.message.chat if query.message else None)
    if action == "predict":
        await query.edit_message_text(f"⏳ در حال تحلیل <code>{esc(symbol)}</code> ...", parse_mode="HTML")
        await _run_predict(fake, uid, symbol, DEFAULT_MARGIN, DEFAULT_LEVERAGE)
    elif action == "nds":
        await query.edit_message_text(f"⏳ NDS <code>{esc(symbol)}</code> ...", parse_mode="HTML")
        await _run_nds(fake, symbol)

async def _run_predict(update: Update, uid: int, symbol: str, margin: float, lev: float):
    open_pos = get_open_position(uid)
    status = await update.message.reply_text(f"⏳ {esc(symbol)} (۴H بسته) ...", parse_mode="HTML")
    try:
        session = get_market_session()
        min_score_now = effective_min_score(session)
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        df_4h_full = fetch_klines(symbol, "4h", 250)
        df_15m = fetch_klines(symbol, "15m", 200)
        if df_4h_full is None or len(df_4h_full) < 80:
            await status.edit_text("❌ خطا در داده.")
            return
        df_4h = closed_klines(df_4h_full)
        if df_4h is None or len(df_4h) < 80:
            await status.edit_text("❌ کندل بسته ناکافی.")
            return
        candle_close_ts = last_closed_candle_ts(df_4h_full)
        df_4h = add_indicators(df_4h)
        df_15m_c = closed_klines(df_15m) if df_15m is not None else None

        multi = get_multi_exchange_prices(symbol)
        live_price = multi["consensus"] or float(df_4h_full["close"].iloc[-1])
        signal_price = float(df_4h["close"].iloc[-1])
        price = live_price

        rsi = float(df_4h["rsi"].iloc[-1])
        atr = float(calc_atr(df_4h).iloc[-1])
        vol_ratio = float(df_4h["volume"].iloc[-1] / df_4h["volume"].tail(20).mean()) if df_4h["volume"].tail(20).mean() > 0 else 1.0
        macd_hist = float(df_4h["macd_hist"].iloc[-1]) if "macd_hist" in df_4h.columns else None
        funding_info = get_funding_info(symbol)
        funding = funding_info["rate"]

        geo = analyze_nds_official(df_4h, df_ltf=df_15m_c, price=signal_price)
        multi_nds = analyze_nds_multi(symbol, current_price=signal_price)
        tf_align = count_tf_alignment(multi_nds)
        regime_info = classify_market_regime(df_4h)
        is_alt = symbol not in ("BTCUSDT", "BTCUSD", "BTC")
        btc_bias = get_btc_structure_bias() if is_alt else None

        rets = np.diff(np.log(df_4h["close"].values[-80:]))
        mu, sigma = float(np.mean(rets)), float(np.std(rets))
        rng = np.random.default_rng(int(abs(signal_price) * 100) % 10_000_007)
        sims = np.array([signal_price * np.exp(np.cumsum(rng.normal(mu, sigma, 2))) for _ in range(24)])
        mean_path = np.mean(sims, axis=0)

        score, reasons, risks, higher = calculate_signal_score(
            multi_nds, rsi, funding, vol_ratio, geo["bias"],
            macd_hist, geo, regime_info, tf_align, btc_bias, is_alt, session,
        )

        onchain = None
        if symbol in ("BTCUSDT", "BTCUSD", "BTC"):
            onchain = get_btc_onchain_realized(live_price=price)
            if onchain and onchain.get("ok"):
                if onchain.get("sth_mvrv") is not None and onchain["sth_mvrv"] < 0.92:
                    risks.append(f"STH MVRV {onchain['sth_mvrv']}")
                    score = max(0, score - 3)
                if onchain.get("lth_mvrv") is not None and onchain["lth_mvrv"] < 1.02:
                    score = min(100, score + 4)
                if onchain.get("lth_mvrv") is not None and onchain["lth_mvrv"] > 2.8:
                    score = max(0, score - 5)

        direction, signal, is_strong, locked = None, "⚪️ صبر", False, False
        entry = sl = tp = None
        lock = get_signal_lock(uid, symbol)

        if lock and lock.get("direction") in ("LONG", "SHORT"):
            locked_dir = lock["direction"]
            invalidated = (locked_dir == "LONG" and price <= float(lock["sl"])) or (locked_dir == "SHORT" and price >= float(lock["sl"]))
            if not invalidated:
                direction, signal, is_strong, locked = locked_dir, f"{'🟢 LONG' if locked_dir == 'LONG' else '🔴 SHORT'} (قفل)", True, True
                entry, sl, tp = float(lock["entry"]), float(lock["sl"]), float(lock["tp"])
                reasons.insert(0, "قفل ۳س")
            else:
                clear_signal_lock(uid, symbol)
                clear_pullback_zone(uid, symbol)

        if not locked:
            allow = geo.get("trade_allowed") and higher in ("صعودی", "نزولی") and score >= min_score_now
            if session["quality"] == "low" and score < min_score_now + 2:
                allow = False
                risks.append("سشن کم‌نقدینگی")
            if allow and higher == "صعودی" and tf_align.get("align_long_ok"):
                if is_alt and btc_bias and btc_bias.get("bias") == "نزولی" and score < ALT_BTC_BLOCK_SCORE:
                    risks.append("لانگ آلت بلاک")
                elif regime_info.get("regime") == "رنج" and vol_ratio < 1.15:
                    risks.append("رنج+حجم ضعیف")
                else:
                    direction, signal, is_strong = "LONG", "🟢 LONG", True
            elif allow and higher == "نزولی" and tf_align.get("align_short_ok"):
                if regime_info.get("regime") == "رنج" and vol_ratio < 1.15:
                    risks.append("رنج+حجم ضعیف")
                else:
                    direction, signal, is_strong = "SHORT", "🔴 SHORT", True
            else:
                if not geo.get("trade_allowed"):
                    risks.append(geo.get("reason") or "NDS")
                elif score < min_score_now:
                    risks.append(f"امتیاز {score} < {min_score_now}")

            if direction:
                # دقت ورود بر اساس اندازه قیمت
                if abs(price) >= 100:
                    entry = round(price, 1)
                elif abs(price) >= 10:
                    entry = round(price, 2)
                else:
                    entry = round(price, 4)
                sl, tp = calculate_dollar_sl_tp(
                    entry, direction, atr, margin, lev, RISK_PER_TRADE,
                    structure_low=geo.get("last_low"), structure_high=geo.get("last_high"),
                    sl_hint=geo.get("sl_hint"), tp_hint=geo.get("tp_hint"), min_rr=MIN_RR,
                )
                set_signal_lock(uid, symbol, direction, entry, sl, tp, score, geo.get("reason") or "", session["name"], candle_close_ts)
                zlo, zhi = compute_pullback_zone(direction, entry, atr, geo, price)
                set_pullback_zone(uid, symbol, direction, zlo, zhi, entry, sl, tp, score)
            else:
                if abs(price) >= 100:
                    entry = round(price, 1)
                elif abs(price) >= 10:
                    entry = round(price, 2)
                else:
                    entry = round(price, 4)
                sl = round(price - atr * 1.6, 4 if abs(price) < 10 else 2 if abs(price) < 100 else 1)
                tp = round(price + atr * 1.6 * MIN_RR, 4 if abs(price) < 10 else 2 if abs(price) < 100 else 1)

        # ---------- Pre-Alert ----------
        pre_sent = False
        if (not is_strong and not open_pos and score >= PRE_ALERT_SCORE_MIN and score < min_score_now
                and can_send_pre_alert(uid, symbol)):
            note = f"امتیاز {score} نزدیک آستانه {min_score_now}"
            if geo.get("bias") in ("صعودی", "نزولی") and not geo.get("trade_allowed"):
                note += f" | NDS {geo['bias']} در حال تکمیل"
            mark_pre_alert(uid, symbol, score, note)
            pre_sent = True
            try:
                await update.message.reply_text(
                    f"🔔 <b>Pre-Alert</b>\n"
                    f"<code>{esc(symbol)}</code> | امتیاز <code>{score}</code> / آستانه <code>{min_score_now}</code>\n"
                    f"{esc(note)}\n"
                    f"سشن: {esc(session['name'])}\n"
                    f"⏱ نزدیک سیگنال — فرصت آماده‌شدن داری.\n"
                    f"هنوز ورود مجاز نیست.",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        risk_usd = abs(entry - sl) / entry * margin * lev if entry and sl else 0
        reward_usd = abs(tp - entry) / entry * margin * lev if entry and tp else 0
        rr = reward_usd / risk_usd if risk_usd > 0 else 0
        fibs = calc_fib(df_4h)
        obs = detect_order_blocks(df_4h)

        pb_zone = None
        if is_strong and direction:
            z = get_pullback_zone(uid, symbol)
            if z:
                pb_zone = (float(z["zone_low"]), float(z["zone_high"]))

        # نمودار اصلی تحلیل
        buf = create_pro_chart(
            df_4h, f"{symbol} | 4H closed | {higher}",
            fibs=fibs,
            entry=entry if is_strong else None,
            sl=sl if is_strong else None,
            tp=tp if is_strong else None,
            sims=sims, mean_path=mean_path,
            level_864=geo.get("level_864"),
            pullback_zone=pb_zone,
            max_bars=100,
        )

        q = "🟢" if session["quality"] == "high" else "🟡" if session["quality"] == "medium" else "🔴"
        nds_note = ""
        if geo.get("bias") == "صعودی" and not geo.get("trade_allowed"):
            nds_note = "ساختار محلی صعودی ولی تریگر ۱۲۳/۸۶٫۴٪ ناقص"
        elif geo.get("bias") == "نزولی" and not geo.get("trade_allowed"):
            nds_note = "ساختار محلی نزولی ولی تریگر ۱۲۳/۸۶٫۴٪ ناقص"
        elif geo.get("trade_allowed"):
            nds_note = geo.get("reason") or "تریگر کامل"

        if is_strong and direction:
            caption = clip_caption(
                f"📋 <b>{esc(symbol)}</b> | {now_utc}\n"
                f"{q} {esc(session['name'])} | ۴H بسته close <code>{pfmt(signal_price)}</code>\n"
                f"سیگنال: <b>{signal}</b> | امتیاز <code>{score}</code>/{min_score_now}\n"
                f"ورود <code>{pfmt(entry)}</code> | SL <code>{pfmt(sl)}</code> | TP <code>{pfmt(tp)}</code>\n"
                f"R:R 1:{rr:.1f} | NDS {esc(higher)}\n"
                f"📍 منطقه پولبک فعال — ⛔ ورود خودکار نیست"
            )
        else:
            caption = clip_caption(
                f"📋 <b>{esc(symbol)}</b> | {now_utc}\n"
                f"{q} {esc(session['name'])} | ۴H بسته close <code>{pfmt(signal_price)}</code>\n"
                f"سیگنال: <b>{signal}</b> | امتیاز <code>{score}</code>/{min_score_now}\n"
                f"NDS محلی: <b>{esc(geo.get('bias'))}</b> | بالاتر: <b>{esc(higher)}</b>\n"
                f"{esc(nds_note)}\n"
                f"⛔ ورود مجاز نیست"
            )
        await update.message.reply_photo(photo=buf, caption=caption, parse_mode="HTML")

        # ---------- نمودار جداگانه فقط برای سیگنال قطعی ----------
        if is_strong and direction and entry is not None and sl is not None and tp is not None:
            try:
                entry_buf = create_entry_signal_chart(
                    df_4h, symbol, direction, entry, sl, tp,
                    fibs=fibs, level_864=geo.get("level_864"),
                    pullback_zone=pb_zone, max_bars=80,
                )
                entry_caption = clip_caption(
                    f"🎯 <b>نمودار سیگنال قطعی | {esc(symbol)} | 4H</b>\n"
                    f"{'🟢 LONG' if direction == 'LONG' else '🔴 SHORT'}\n"
                    f"ENTRY <code>{pfmt(entry)}</code>\n"
                    f"SL <code>{pfmt(sl)}</code>  |  TP <code>{pfmt(tp)}</code>\n"
                    f"R:R 1:{rr:.1f} | امتیاز {score}\n"
                    f"EMA20 · EMA50 · ATR · فیبو نزدیک\n"
                    f"⛔ ورود خودکار نیست — سفارش روی XT.com با خودت"
                )
                await update.message.reply_photo(photo=entry_buf, caption=entry_caption, parse_mode="HTML")
            except Exception as e:
                print("Entry chart error:", e)

        detail1 = (
            f"<b>NDS</b>\n• مجوز {'✅' if geo.get('trade_allowed') else '❌'} | {esc(geo.get('reason'))}\n"
            f"• مقاومت <code>{pfmt(geo.get('last_high'))}</code> | حمایت <code>{pfmt(geo.get('last_low'))}</code>\n\n"
            + format_levels_block(signal_price, df_4h, fibs, geo)
        )
        if is_strong and pb_zone:
            detail1 += (
                f"\n\n📍 <b>منطقه ورود ترجیحی (پولبک)</b>\n"
                f"• <code>{pfmt(pb_zone[0])}</code> – <code>{pfmt(pb_zone[1])}</code>\n"
                f"• وقتی قیمت وارد این بازه شد نوتیف می‌گیری."
            )
        elif not is_strong:
            detail1 += (
                f"\n\n📌 <b>سطوح فرضی (ورود مجاز نیست)</b>\n"
                f"• فرضی Entry <code>{pfmt(entry)}</code> | SL <code>{pfmt(sl)}</code> | TP <code>{pfmt(tp)}</code>"
            )
        await update.message.reply_text(detail1, parse_mode="HTML")

        detail2 = (
            f"<b>فیلتر</b>\n"
            f"• رژیم {esc(regime_info.get('regime'))} | ATR {regime_info.get('atr_pct')}% | Vol {regime_info.get('vol_ratio')}x\n"
            f"• TF ↑{tf_align['bull']} ↓{tf_align['bear']} بالاتر {esc(tf_align['higher_bias'])}\n"
        )
        if is_alt and btc_bias:
            detail2 += f"• BTC {esc(btc_bias.get('bias'))}\n"
        if funding is not None:
            detail2 += f"• فاندینگ <code>{funding:.4f}%</code>"
            if funding_info.get("source"):
                detail2 += f" ({esc(funding_info['source'])})"
            detail2 += "\n"
        detail2 += f"📡 {format_price_sources(multi)}"
        if onchain and onchain.get("ok"):
            detail2 += "\n\n" + format_onchain_html(onchain)
        if obs:
            detail2 += "\n\n<b>OB</b>\n" + "\n".join(
                [f"• {'↑' if o['type']=='bullish_ob' else '↓'} <code>{pfmt(o['low'])}</code>–<code>{pfmt(o['high'])}</code>" for o in obs]
            )
        await update.message.reply_text(detail2, parse_mode="HTML")

        reason_text = "📌 <b>دلیل</b>\n" + "\n".join([f"• {esc(r)}" for r in reasons[:12]])
        reason_text += "\n\n⚠️ <b>ریسک</b>\n" + ("\n".join([f"• {esc(r)}" for r in risks[:10]]) if risks else "• —")
        await update.message.reply_text(reason_text, parse_mode="HTML")

        if is_strong and direction and not open_pos:
            pend = {"symbol": symbol, "direction": direction, "entry": entry, "sl": sl, "tp": tp, "candle_close_ts": candle_close_ts}
            set_pending_signal(uid, pend)
            if candle_close_ts and not was_notified(uid, symbol, candle_close_ts):
                mark_notified(uid, symbol, candle_close_ts)
            ztxt = ""
            if pb_zone:
                ztxt = f"\n📍 منطقه پولبک: <code>{pfmt(pb_zone[0])}</code>–<code>{pfmt(pb_zone[1])}</code>\n(وقتی قیمت رسید نوتیف جدا می‌آید)"
            await update.message.reply_text(
                f"🔔 نوتیف سیگنال (۴H بسته)\n{esc(direction)} | <code>{esc(symbol)}</code>\n"
                f"ورود {pfmt(entry)} | SL {pfmt(sl)} | TP {pfmt(tp)} | R:R 1:{rr:.1f}"
                f"{ztxt}\n"
                f"⛔ سفارش صرافی خودکار نیست.\n<code>/confirm</code> فقط ثبت ژورنال\n\n{DISCLAIMER}",
                parse_mode="HTML",
            )
        elif open_pos:
            await update.message.reply_text("ℹ️ پوزیشن باز — فقط تحلیل.")
        else:
            msg = "⏸ ورود پیشنهادی نیست."
            if pre_sent:
                msg += "\n(Pre-Alert ارسال شد)"
            await update.message.reply_text(f"{msg}\n{DISCLAIMER}", parse_mode="HTML")

        await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ {esc(str(e)[:120])}")

def main():
    if not BOT_TOKEN:
        print("BOT_TOKEN missing")
        return
    threading.Thread(target=run_web, daemon=True).start()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_for_monitor = Bot(token=BOT_TOKEN)
    threading.Thread(target=monitor_positions, args=(bot_for_monitor,), daemon=True).start()
    for cmd, handler in [
        ("start", start), ("buy", buy), ("status", status_cmd), ("tools", tools), ("news", news_command),
        ("onchain", onchain_command), ("predict", predict_command), ("nds", nds_command), ("market", market_command),
        ("alert", alert_cmd), ("alerts", alerts_cmd), ("removealert", removealert_cmd),
        ("log", log_trade), ("performance", performance), ("position", position_cmd), ("close", close_cmd),
        ("confirm", confirm_cmd), ("watch", watch_cmd), ("unwatch", unwatch_cmd), ("watchlist", watchlist_cmd),
        ("adduser", adduser), ("removeuser", removeuser), ("listusers", listusers),
    ]:
        application.add_handler(CommandHandler(cmd, handler))
    application.add_handler(CallbackQueryHandler(symbol_callback, pattern=r"^(predict|nds):"))
    print("Kronos Stable | Entry Chart + Pre-Alert + Pullback | defaults BTC/QNT | no auto-entry")
    application.run_polling()

if __name__ == "__main__":
    main()
