# -*- coding: utf-8 -*-
"""
=====================================================================
 Nitin — NIFTY Bi-Weekly 15 Delta Hedged Iron Condor Bot
 Version : V1.6  (04-11-2026)
   + NSE सुट्ट्यांची यादी कोडमध्येच (१० नोव्हें. बलिप्रतिपदा, २४ नोव्हें., २५ डिसें.)
   + ३:१० ची दिवसअखेर तपासणी: Short premium फरक ≥ 1.65x असेल तर आजच ॲडजस्टमेंट
   + Target बुक झाल्यावर १५ सेकंदांनी नवीन एंट्री (TARGET_REENTRY_SEC)
   + Stop-loss बंद (USE_STOPLOSS = False): फक्त Target ला बुक, बाकी वेळ ॲडजस्टमेंट
   + तोट्याचे इशारे (फक्त माहिती) + Hedge-आधारित कमाल धोका /status मध्ये
   + Liquidity तपासणी: 15Δ/200 liquid नसेल तर 20Δ/100 चा पर्याय Telegram वर (/yes ने एंट्री)
   + दर तासाला ट्रेड status Telegram वर
   + Telegram रिपोर्ट आणि commands (/status /exit /adjust ...)
   + Google Sheet नोंदी (Orders + Trades, Trade ID)
   + Broker Match (तुमची qty वेगळी, १५ मिनिटांचा balancing नियम)
   + Gap नियम: ९:१५-९:१७ फक्त निरीक्षण
   + TOTP auto token (startup दुरुस्तीसह)
 Broker  : Dhan (dhanhq 2.3.x)   |   Server: AWS EC2 (Ubuntu)
---------------------------------------------------------------------
 या file ला हात लावायचा नाही. सर्व बदल config.py मध्येच.
=====================================================================
"""
import os
import sys
import json
import time
import math
import queue
import logging
import threading
import traceback
import hmac, hashlib, base64, struct, re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dhanhq import DhanContext, dhanhq, DhanLogin

BOT_VERSION = "V1.6"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.py")
LOG_FILE = os.path.join(BASE_DIR, "bot.log")
TRADES_FILE = os.path.join(BASE_DIR, "trades.csv")
IST = ZoneInfo("Asia/Kolkata")

# ---------------- रणनीतीचे स्थिर नियम (Rulebook) ----------------
UNDERLYING_ID = 13            # NIFTY 50
UNDERLYING_SEG = "IDX_I"
STRIKE_STEP = 100             # फक्त १०० च्या पटीतील strikes
TARGET_DELTA = 15.0
ENTRY_TIME = (9, 35)          # नवीन ट्रेड
ENTRY_WINDOW_END = (9, 40)    # ९:३५ ची एंट्री फेल झाली तर ९:४० पर्यंत पुन्हा प्रयत्न
ROLLOVER_EXIT_TIME = (9, 30)  # मधल्या expiry दिवशी पूर्ण एक्झिट
GAP_WATCH_END = (9, 17)       # ९:१५-९:१७ फक्त निरीक्षण (SL/TP/2x निर्णय नाहीत)
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)
LAST_NEW_ENTRY = (15, 15)     # यानंतर नवीन ट्रेड/री-एंट्री नाही
DAILY_SUMMARY_TIME = (15, 31)
CHAIN_MIN_GAP = 3.2           # Dhan option chain: ३ सेकंदात १ request
LOOP_SLEEP = 0.5
STATUS_EVERY_SEC = 60
SHEET_STATUS_EVERY_SEC = 300
BROKER_CHECK_SEC = 30
BALANCE_WAIT_MIN = 15
CONFIRM_SEC = 60

REQUIRED_KEYS = ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "PAPER_MODE", "LOTS", "LOT_SIZE",
                 "DEPLOYED_CAPITAL", "PROFIT_TARGET_PCT", "MAX_SL_PCT", "HEDGE_DISTANCE",
                 "REENTRY_WAIT_MIN"]
OPTIONAL_KEYS = {"HOLIDAYS": [], "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "",
                 "SHEET_WEBHOOK_URL": "", "SHEET_SECRET": "", "DHAN_PIN": "", "DHAN_TOTP_SECRET": ""}
NUM_KEYS = {"FALLBACK_DELTA": 20.0, "FALLBACK_HEDGE": 100, "LIQ_MAX_SPREAD_PCT": 5.0,
            "LIQ_MIN_SPREAD_RS": 1.0, "LIQ_MIN_OI": 0, "ENTRY_CONFIRM_MIN": 10, "HOURLY_STATUS": True,
            "USE_STOPLOSS": False, "LOSS_ALERT_STEP_PCT": 1.25, "TARGET_REENTRY_SEC": 15,
            "EOD_RATIO": 1.65, "EOD_CHECK_HHMM": 1510}
ROLE_NAME = {"CE_S": "Short CE", "PE_S": "Short PE", "CE_H": "Hedge CE", "PE_H": "Hedge PE"}

# ---------------- Logging (IST वेळेत) ----------------
logger = logging.getLogger("icbot")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")
_fmt.converter = lambda *a: datetime.now(IST).timetuple()
_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
logger.addHandler(_fh)
logger.addHandler(_sh)


def log(msg):
    logger.info(msg)


# ---------------- Config ----------------
CFG = {}
CFG_MTIME = 0.0
PAPER = True
QTY = 0
STATE_FILE = ""
dhan = None


def read_config():
    ns = {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        exec(f.read(), ns)
    missing = [k for k in REQUIRED_KEYS if k not in ns]
    if missing:
        raise ValueError(f"config.py मध्ये हे नाहीत: {missing}")
    cfg = {k: ns[k] for k in REQUIRED_KEYS}
    for k, dv in OPTIONAL_KEYS.items():
        v = ns.get(k, dv)
        cfg[k] = list(v) if isinstance(dv, list) else str(v or "").strip()
    cfg["DHAN_TOTP_SECRET"] = cfg["DHAN_TOTP_SECRET"].replace(" ", "").upper()
    for k, dv in NUM_KEYS.items():
        v = ns.get(k, dv)
        try:
            cfg[k] = bool(v) if isinstance(dv, bool) else type(dv)(v)
        except Exception:
            cfg[k] = dv
    return cfg


def build_client():
    global dhan
    dhan = dhanhq(DhanContext(str(CFG["DHAN_CLIENT_ID"]), str(CFG["DHAN_ACCESS_TOKEN"])))


def reload_token_if_changed():
    """config.py बदलली की token / Telegram / Sheet restart न करता घेतो."""
    global CFG_MTIME
    try:
        m = os.path.getmtime(CONFIG_FILE)
        if m == CFG_MTIME:
            return
        new = read_config()
        CFG_MTIME = m
        if new["DHAN_ACCESS_TOKEN"] != CFG["DHAN_ACCESS_TOKEN"] or new["DHAN_CLIENT_ID"] != CFG["DHAN_CLIENT_ID"]:
            CFG["DHAN_ACCESS_TOKEN"] = new["DHAN_ACCESS_TOKEN"]
            CFG["DHAN_CLIENT_ID"] = new["DHAN_CLIENT_ID"]
            build_client()
            log("🔑 config.py मधला नवीन Access Token लोड झाला.")
        for k in OPTIONAL_KEYS:
            CFG[k] = new[k]
    except Exception as e:
        log(f"config.py वाचताना चूक: {e}")


# ---------------- Outbox: Telegram + Sheet (पार्श्वभूमीत, ट्रेडिंग थांबत नाही) ----------------
OUTBOX = queue.Queue()


def _outbox_worker():
    while True:
        kind, payload, tries = OUTBOX.get()
        try:
            if kind == "tg":
                t, c = CFG.get("TELEGRAM_BOT_TOKEN"), CFG.get("TELEGRAM_CHAT_ID")
                if t and c:
                    r = requests.post(f"https://api.telegram.org/bot{t}/sendMessage",
                                      data={"chat_id": c, "text": payload[:4000]}, timeout=15)
                    if r.status_code == 429 or r.status_code >= 500:
                        raise Exception(f"Telegram HTTP {r.status_code}")
            elif kind == "sheet":
                url = CFG.get("SHEET_WEBHOOK_URL")
                if url:
                    body = dict(payload)
                    body["secret"] = CFG.get("SHEET_SECRET", "")
                    r = requests.post(url, json=body, timeout=30)
                    if r.status_code >= 400:
                        raise Exception(f"Sheet HTTP {r.status_code}")
                    if '"ok":false' in r.text.replace(" ", ""):
                        logger.info(f"Sheet ने नकार दिला: {r.text[:200]}")
        except Exception as e:
            if tries < 4:
                time.sleep(5 * (tries + 1))
                OUTBOX.put((kind, payload, tries + 1))
            else:
                logger.info(f"{kind} पाठवता आले नाही: {e}")


def tg(msg):
    OUTBOX.put(("tg", msg, 0))


def alert(msg):
    log(msg)
    tg(f"[IC {'PAPER' if PAPER else 'LIVE'}] {msg}")


# ---------------- वेळ ----------------
def now():
    return datetime.now(IST)


def today_str():
    return now().strftime("%Y-%m-%d")


def hm(n=None):
    n = n or now()
    return (n.hour, n.minute)


# NSE equity/F&O सुट्ट्या (कोडमध्ये कायम). config.py मधली HOLIDAYS यादी यात मिळवली जाते.
NSE_HOLIDAYS = {"2026-11-10", "2026-11-24", "2026-12-25"}


def is_trading_day(n):
    d = n.strftime("%Y-%m-%d")
    return n.weekday() < 5 and d not in NSE_HOLIDAYS and d not in CFG.get("HOLIDAYS", [])


def market_open(n):
    return is_trading_day(n) and MARKET_OPEN <= hm(n) <= MARKET_CLOSE


# ---------------- TOTP ने आपोआप Token ----------------
_last_token_try = 0.0
_token_day = None


def totp_enabled():
    return bool(CFG.get("DHAN_PIN")) and bool(CFG.get("DHAN_TOTP_SECRET"))


def totp_now(secret):
    s = secret.replace(" ", "").upper()
    s += "=" * ((8 - len(s) % 8) % 8)
    key = base64.b32decode(s)
    if 30 - (int(time.time()) % 30) < 3:
        time.sleep(4)
    counter = int(time.time()) // 30
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000
    return f"{code:06d}"


def save_token_to_config(tok):
    global CFG_MTIME
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        txt = f.read()
    txt = re.sub(r'^DHAN_ACCESS_TOKEN\s*=\s*".*$', lambda m: f'DHAN_ACCESS_TOKEN = "{tok}"', txt, count=1, flags=re.M)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(txt)
    CFG_MTIME = os.path.getmtime(CONFIG_FILE)


def refresh_token(reason):
    """PIN + TOTP ने Dhan कडून नवीन Access Token. यश मिळालं तर True."""
    global _last_token_try, _token_day
    if not totp_enabled():
        return False
    if time.time() - _last_token_try < 600:
        return False
    _last_token_try = time.time()
    try:
        code = totp_now(CFG["DHAN_TOTP_SECRET"])
        r = requests.post("https://auth.dhan.co/app/generateAccessToken",
                          params={"dhanClientId": str(CFG["DHAN_CLIENT_ID"]), "pin": CFG["DHAN_PIN"], "totp": code},
                          timeout=20)
        j = r.json() if r.content else {}
        d = j.get("data") if isinstance(j.get("data"), dict) else {}
        tok = j.get("accessToken") or j.get("access_token") or d.get("accessToken")
        if not tok:
            alert(f"❌ TOTP ने Token मिळाला नाही ({reason}): {j}")
            return False
        CFG["DHAN_ACCESS_TOKEN"] = tok
        save_token_to_config(tok)
        build_client()
        _token_day = today_str()
        alert(f"🔑 नवीन Token TOTP ने आपोआप तयार ✅ ({reason}) | मुदत: {j.get('expiryTime', '२४ तास')}")
        return True
    except Exception as e:
        alert(f"❌ TOTP Token error ({reason}): {e}")
        return False


# ---------------- State ----------------
def blank_state():
    return {"in_trade": False, "halted": False, "paused": False,
            "expiry": None, "rollover": None, "legs": {},
            "realized": 0.0, "shifts": 0, "trade_no": 0,
            "reentry_at": 0.0, "entry_time": None, "no_entry_alert_day": None,
            "imbalance": None, "extra_noted": [], "summary_day": None,
            "entry_pending": None, "skip_entry_day": None, "last_hourly": None,
            "plan": "", "hedge_dist": 0, "eod_check_day": None, "loss_alert_level": 0}


state = blank_state()


def load_state():
    global state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
            base = blank_state()
            base.update(s)
            state = base
            return
        except Exception as e:
            log(f"State file वाचता आली नाही ({e}) — नवीन सुरुवात.")
    state = blank_state()


def save_state():
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def halt(reason):
    state["halted"] = True
    save_state()
    alert(f"⛔ बॉट थांबवला (HALT): {reason}\nDhan मध्ये positions तपासा. सगळं ठीक झाल्यावर Telegram वर /resume पाठवा.")


def trade_id(n=None):
    return f"{'P' if PAPER else 'L'}-IC-{(n or state['trade_no']):04d}"


# ---------------- Google Sheet नोंदी ----------------
def sheet_order(typ, role, strike, opt, side, qty, price, booked=None, oid="", status="TRADED", note=""):
    if not CFG.get("SHEET_WEBHOOK_URL"):
        return
    OUTBOX.put(("sheet", {"kind": "order", "id": trade_id(), "mode": "PAPER" if PAPER else "LIVE",
                          "time": now().strftime("%Y-%m-%d %H:%M:%S"), "type": typ,
                          "leg": ROLE_NAME.get(role, role), "strike": strike, "opt": opt, "side": side,
                          "qty": qty, "price": round(float(price or 0), 2),
                          "booked": "" if booked is None else round(booked, 2),
                          "order_id": oid, "status": status, "note": note}, 0))


def leg_txt(role):
    lg = state["legs"].get(role)
    return f"{lg['strike']} @{lg['entry']:.2f}" if lg else "-"


def sheet_trade(status="OPEN", unbooked=None, net=None, exit_reason=""):
    if not CFG.get("SHEET_WEBHOOK_URL"):
        return
    OUTBOX.put(("sheet", {"kind": "trade", "id": trade_id(), "mode": "PAPER" if PAPER else "LIVE",
                          "status": status, "entry_time": state.get("entry_time") or "",
                          "expiry": state.get("expiry") or "", "rollover": state.get("rollover") or "",
                          "short_ce": leg_txt("CE_S"), "short_pe": leg_txt("PE_S"),
                          "hedge_ce": leg_txt("CE_H"), "hedge_pe": leg_txt("PE_H"),
                          "shifts": state.get("shifts", 0), "booked": round(state.get("realized", 0.0), 2),
                          "unbooked": "" if unbooked is None else round(unbooked, 2),
                          "net": "" if net is None else round(net, 2),
                          "updated": now().strftime("%Y-%m-%d %H:%M:%S"),
                          "exit_time": now().strftime("%Y-%m-%d %H:%M:%S") if status == "CLOSED" else "",
                          "exit_reason": exit_reason}, 0))


# ---------------- Dhan उत्तर उघडणे ----------------
_last_auth_alert = 0.0


def unwrap(res, what=""):
    """dhanhq 2.3 चे उत्तर दोन थरात येते: res['data']['data']. ते नीट उघडणे."""
    global _last_auth_alert
    if not isinstance(res, dict):
        log(f"{what}: अनपेक्षित उत्तर: {res}")
        return None
    if res.get("status") != "success":
        rem = res.get("remarks")
        log(f"{what} अयशस्वी: {rem}")
        txt = json.dumps(rem, ensure_ascii=False) if rem else ""
        if "DH-901" in txt and refresh_token("Token बाद (DH-901)"):
            return None
        if any(x in txt for x in ("DH-901", "DH-905", "Invalid", "invalid", "expired", "Expired", "IP")):
            if time.time() - _last_auth_alert > 900:
                _last_auth_alert = time.time()
                alert(f"🔑 Dhan अडचण ({what}): {txt}")
        return None
    d = res.get("data")
    if isinstance(d, dict) and d.get("status") == "failure":
        log(f"{what} अयशस्वी: {d}")
        return None
    if isinstance(d, dict) and "data" in d:
        d = d["data"]
    return d


# ---------------- Expiry आणि Option Chain ----------------
def get_expiries():
    """valid[0] = मधली expiry (Rollover दिवस), valid[1] = Bi-weekly trading expiry."""
    d = unwrap(dhan.expiry_list(under_security_id=UNDERLYING_ID,
                                under_exchange_segment=UNDERLYING_SEG), "Expiry list")
    if not isinstance(d, list):
        return None, None
    t = today_str()
    valid = sorted(e for e in d if isinstance(e, str) and e > t)
    if len(valid) < 2:
        return None, None
    return valid[0], valid[1]


_last_chain = 0.0


def fetch_chain(expiry):
    global _last_chain
    wait = CHAIN_MIN_GAP - (time.time() - _last_chain)
    if wait > 0:
        time.sleep(wait)
    _last_chain = time.time()
    try:
        res = dhan.option_chain(under_security_id=UNDERLYING_ID,
                                under_exchange_segment=UNDERLYING_SEG, expiry=expiry)
    except Exception as e:
        log(f"Option chain error: {e}")
        return None, None
    d = unwrap(res, "Option chain")
    if not isinstance(d, dict):
        return None, None
    spot = float(d.get("last_price") or 0)
    oc = {}
    for k, v in (d.get("oc") or {}).items():
        try:
            oc[int(round(float(k)))] = v
        except Exception:
            pass
    if spot <= 0 or not oc:
        log("Option chain रिकामी आली.")
        return None, None
    return spot, oc


def leg_info(oc, strike, opt):
    node = oc.get(int(strike)) or {}
    leg = node.get("ce" if opt == "CE" else "pe") or {}
    ltp = float(leg.get("last_price") or 0)
    sid = leg.get("security_id")
    return ltp, (str(sid) if sid else None), leg


# Option chain मध्ये security_id नसेल तर Dhan scrip master मधून शोधणे (बॅकअप)
_master = {}
_master_day = None


def master_lookup(expiry, strike, opt):
    global _master, _master_day
    if _master_day != today_str():
        try:
            import pandas as pd
            log("Scrip master डाउनलोड करत आहे (security_id बॅकअप)...")
            df = pd.read_csv("https://images.dhan.co/api-data/api-scrip-master.csv", low_memory=False)
            df = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_INSTRUMENT_NAME"] == "OPTIDX")
                    & (df["SEM_TRADING_SYMBOL"].astype(str).str.startswith("NIFTY-"))]
            m = {}
            for _, r in df.iterrows():
                m[(str(r["SEM_EXPIRY_DATE"])[:10], int(round(float(r["SEM_STRIKE_PRICE"]))),
                   str(r["SEM_OPTION_TYPE"]).upper())] = str(int(r["SEM_SMST_SECURITY_ID"]))
            _master, _master_day = m, today_str()
            log(f"Scrip master लोड: {len(m)} NIFTY options")
        except Exception as e:
            log(f"Scrip master error: {e}")
            return None
    return _master.get((expiry, int(strike), opt))


def get_sid(oc, expiry, strike, opt):
    _, sid, _ = leg_info(oc, strike, opt)
    return sid or master_lookup(expiry, strike, opt)


# ---------------- Delta ----------------
def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(S, K, T, iv, opt):
    if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (0.07 + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    return norm_cdf(d1) * 100.0 if opt == "CE" else (1.0 - norm_cdf(d1)) * 100.0


def leg_delta(leg, spot, strike, T, opt):
    g = leg.get("greeks") or {}
    try:
        dl = abs(float(g.get("delta") or 0))
    except Exception:
        dl = 0.0
    if 0 < dl <= 1.0:
        return dl * 100.0
    if dl > 1.0:
        return dl
    iv = float(leg.get("implied_volatility") or 0) / 100.0
    return bs_delta(spot, strike, T, max(iv, 0.08), opt)


def closest_premium(oc, target, opt, allowed):
    """target प्रिमियमच्या सर्वात जवळचा (कमी फरक) strike. उदा. 150 हवा, 148/150.5 असतील तर 150.5.
    फरक अगदी सारखा असेल तर जास्त OTM (सुरक्षित) strike."""
    best = None
    for k in sorted(oc):
        if k % STRIKE_STEP or not allowed(k):
            continue
        ltp, _, _ = leg_info(oc, k, opt)
        if ltp <= 0:
            continue
        diff = abs(ltp - target)
        far = k if opt == "CE" else -k
        if best is None or diff < best[0] - 1e-9 or (abs(diff - best[0]) < 1e-9 and far > best[2]):
            best = (diff, k, far, ltp)
    return (best[1], best[3]) if best else (None, None)


# ---------------- ऑर्डर ----------------
_last_oid = ""
_last_order_time = 0.0


def place(sec_id, side, ltp, tag, qty=None):
    """LIVE मध्ये ऑर्डर टाकून ती TRADED झाली का ते तपासते. (ok, भाव) परत देते."""
    global _last_oid, _last_order_time
    q = int(qty or QTY)
    _last_oid = ""
    if PAPER:
        log(f"📝 PAPER ORDER [{tag}] {side} {q} Qty | SecID {sec_id} | @ {ltp:.2f}")
        _last_oid = "PAPER"
        return True, ltp
    _last_order_time = time.time()
    try:
        res = dhan.place_order(security_id=str(sec_id), exchange_segment=dhan.NSE_FNO,
                               transaction_type=dhan.BUY if side == "BUY" else dhan.SELL,
                               quantity=q, order_type=dhan.MARKET,
                               product_type=dhan.MARGIN, price=0)
    except Exception as e:
        alert(f"❌ ORDER error [{tag}]: {e}")
        return False, 0.0
    d = unwrap(res, f"Order {tag}")
    oid = d.get("orderId") if isinstance(d, dict) else None
    if not oid:
        alert(f"❌ ORDER नाकारली [{tag}]: {res}")
        return False, 0.0
    _last_oid = str(oid)
    for _ in range(20):
        time.sleep(0.5)
        try:
            o = unwrap(dhan.get_order_by_id(oid), "Order status")
        except Exception:
            o = None
        if isinstance(o, list):
            o = o[0] if o else None
        if not isinstance(o, dict):
            continue
        st = str(o.get("orderStatus", "")).upper()
        if st == "TRADED":
            px = float(o.get("averageTradedPrice") or 0) or ltp
            log(f"✅ ORDER TRADED [{tag}] {side} {q} @ {px:.2f} (ID {oid})")
            _last_order_time = time.time()
            return True, px
        if st in ("REJECTED", "CANCELLED", "EXPIRED"):
            alert(f"❌ ORDER {st} [{tag}] कारण: {o.get('omsErrorDescription') or o.get('omsErrorCode')}")
            return False, 0.0
    alert(f"⚠️ ORDER १० सेकंदात TRADED झाली नाही [{tag}] ID {oid} — Dhan मध्ये तपासा!")
    return False, 0.0


def close_legs(roles, ltps, qtys=None, typ="EXIT"):
    """आधी Short (BUY), मग Hedge (SELL). qtys दिल्यास फक्त तेवढीच qty (उपलब्ध qty) बंद."""
    L = state["legs"]
    ok_all = True
    for group, side in ((("CE_S", "PE_S"), "BUY"), (("CE_H", "PE_H"), "SELL")):
        for role in group:
            if role not in roles or role not in L:
                continue
            lg = L[role]
            q = QTY if qtys is None else int(qtys.get(role, QTY))
            if q <= 0:
                L.pop(role)
                save_state()
                continue
            ok, px = place(lg["sec_id"], side, ltps.get(role) or lg["entry"], f"{typ}_{role}_{side}", q)
            if ok:
                b = ((lg["entry"] - px) if side == "BUY" else (px - lg["entry"])) * q
                state["realized"] += b
                sheet_order(typ, role, lg["strike"], lg["opt"], side, q, px, b, _last_oid)
                L.pop(role)
                save_state()
            else:
                sheet_order(typ, role, lg["strike"], lg["opt"], side, q, 0, None, _last_oid, "FAILED")
                ok_all = False
        time.sleep(0.5)
    return ok_all


def journal(reason):
    new = not os.path.exists(TRADES_FILE)
    with open(TRADES_FILE, "a", encoding="utf-8") as f:
        if new:
            f.write("trade_no,mode,entry_time,exit_time,expiry,shifts,net_pnl,reason\n")
        f.write(f"{state['trade_no']},{'PAPER' if PAPER else 'LIVE'},{state.get('entry_time')},"
                f"{now().strftime('%Y-%m-%d %H:%M:%S')},{state.get('expiry')},{state.get('shifts')},"
                f"{state['realized']:.2f},\"{reason}\"\n")


# ---------------- Liquidity तपासणी ----------------
def leg_liquidity(oc, strike, opt):
    """(ok, कारण). bid/ask नसतील किंवा spread मोठा असेल तर illiquid."""
    ltp, _, leg = leg_info(oc, strike, opt)
    if ltp <= 0:
        return False, "भाव नाही"
    if "top_bid_price" not in leg and "top_ask_price" not in leg:
        return True, ""          # Dhan ने bid/ask दिले नाहीत → तपासता येत नाही, चालू द्या
    bid = float(leg.get("top_bid_price") or 0)
    ask = float(leg.get("top_ask_price") or 0)
    oi = float(leg.get("oi") or 0)
    if bid <= 0 or ask <= 0:
        return False, f"bid {bid}/ask {ask}"
    lim = max(float(CFG["LIQ_MIN_SPREAD_RS"]), ltp * float(CFG["LIQ_MAX_SPREAD_PCT"]) / 100.0)
    if ask - bid > lim:
        return False, f"spread {ask - bid:.2f} > {lim:.2f}"
    if oi < float(CFG["LIQ_MIN_OI"]):
        return False, f"OI {oi:.0f}"
    return True, ""


def liquidity_issues(oc, legs):
    out = []
    for role in ("CE_S", "PE_S", "CE_H", "PE_H"):
        lg = legs[role]
        ok, why = leg_liquidity(oc, lg["strike"], lg["opt"])
        if not ok:
            out.append(f"{ROLE_NAME[role]} {lg['strike']}{lg['opt']}: {why}")
    return out


# ---------------- एंट्री (नियम २) ----------------
def build_plan(oc, spot, T, bw, target_delta, hd):
    """15Δ (किंवा 20Δ) + premium जुळवणी + hedge. (legs, None) किंवा (None, कारण)."""
    best = {"CE": None, "PE": None}
    for k in oc:
        if k % STRIKE_STEP:
            continue
        for opt in ("CE", "PE"):
            if (opt == "CE" and k <= spot) or (opt == "PE" and k >= spot):
                continue
            ltp, _, leg = leg_info(oc, k, opt)
            if ltp <= 0:
                continue
            dl = leg_delta(leg, spot, k, T, opt)
            gap = abs(dl - target_delta)
            if best[opt] is None or gap < best[opt][0]:
                best[opt] = (gap, k, ltp, dl)
    if not best["CE"] or not best["PE"]:
        return None, f"{target_delta:.0f} Delta चे strikes सापडले नाहीत"
    ce_k, ce_ltp, pe_k, pe_ltp = best["CE"][1], best["CE"][2], best["PE"][1], best["PE"][2]
    note = (f"{target_delta:.0f}Δ → CE {ce_k} @{ce_ltp} (Δ{best['CE'][3]:.1f}) | PE {pe_k} @{pe_ltp} (Δ{best['PE'][3]:.1f})")
    if ce_ltp >= pe_ltp:
        pe_k, pe_ltp = closest_premium(oc, ce_ltp, "PE", lambda k: k < spot)
    else:
        ce_k, ce_ltp = closest_premium(oc, pe_ltp, "CE", lambda k: k > spot)
    if not ce_k or not pe_k:
        return None, "Matching strike सापडला नाही"
    plan = {"CE_S": (ce_k, "CE"), "PE_S": (pe_k, "PE"), "CE_H": (ce_k + hd, "CE"), "PE_H": (pe_k - hd, "PE")}
    legs = {}
    for role, (k, opt) in plan.items():
        ltp, _, _ = leg_info(oc, k, opt)
        sid = get_sid(oc, bw, k, opt)
        if ltp <= 0 or not sid:
            return None, f"{ROLE_NAME[role]} {k}{opt}: LTP किंवा SecurityID मिळाले नाही"
        legs[role] = {"strike": k, "opt": opt, "sec_id": sid, "ltp": ltp}
    log(f"प्लॅन {target_delta:.0f}Δ/{hd}: {note} → Short {ce_k}CE @{legs['CE_S']['ltp']} / {pe_k}PE @{legs['PE_S']['ltp']}"
        f" | Hedge {ce_k + hd}CE / {pe_k - hd}PE")
    return legs, None


def plan_lines(legs):
    return (f"Short SELL: {legs['CE_S']['strike']} CE @{legs['CE_S']['ltp']} , {legs['PE_S']['strike']} PE @{legs['PE_S']['ltp']}\n"
            f"Hedge BUY: {legs['CE_H']['strike']} CE @{legs['CE_H']['ltp']} , {legs['PE_H']['strike']} PE @{legs['PE_H']['ltp']}")


_last_liq_alert = 0.0


def enter_trade(source, use_fallback=False):
    """True = ट्रेड घेतला, False = नाही, "ASKED" = 20Δ/100 चा पर्याय Telegram वर विचारला."""
    mid, bw = get_expiries()
    if not bw:
        log("Expiry list मिळाली नाही — एंट्री नाही.")
        return False
    spot, oc = fetch_chain(bw)
    if not spot:
        return False
    days = (datetime.strptime(bw, "%Y-%m-%d").date() - now().date()).days
    T = max(days, 0.5) / 365.0
    hd_std, d_fb, hd_fb = int(CFG["HEDGE_DISTANCE"]), float(CFG["FALLBACK_DELTA"]), int(CFG["FALLBACK_HEDGE"])

    if use_fallback:
        legs, err = build_plan(oc, spot, T, bw, d_fb, hd_fb)
        if err:
            alert(f"❌ {d_fb:.0f}Δ/{hd_fb} प्लॅन बनला नाही: {err}")
            return False
        bad = liquidity_issues(oc, legs)
        if bad:
            alert(f"❌ {d_fb:.0f}Δ/{hd_fb} सुद्धा आत्ता liquid नाही — एंट्री रद्द:\n" + "\n".join(bad))
            return False
        return place_entry(legs, bw, mid, f"{d_fb:.0f}Δ / {hd_fb} OTM (पर्याय)", hd_fb, source)

    legs, err = build_plan(oc, spot, T, bw, TARGET_DELTA, hd_std)
    if err:
        log(f"[{source}] {err} — एंट्री नाही.")
        return False
    bad = liquidity_issues(oc, legs)
    if not bad:
        return place_entry(legs, bw, mid, f"{TARGET_DELTA:.0f}Δ / {hd_std} OTM", hd_std, source)

    # 15Δ/200 liquid नाही → 20Δ/100 चा पर्याय विचारा
    legs2, err2 = build_plan(oc, spot, T, bw, d_fb, hd_fb)
    bad2 = liquidity_issues(oc, legs2) if legs2 else [err2]
    head = f"⚠️ {TARGET_DELTA:.0f}Δ / {hd_std} OTM चे strikes liquid नाहीत ({source}):\n" + "\n".join(bad)
    if bad2:
        global _last_liq_alert
        msg = head + f"\n\n{d_fb:.0f}Δ / {hd_fb} OTM सुद्धा liquid नाही:\n" + "\n".join(bad2) + "\n→ आत्ता एंट्री नाही (थोड्या वेळाने पुन्हा तपासेन)."
        if time.time() - _last_liq_alert > 600:
            _last_liq_alert = time.time()
            alert(msg)
        else:
            log(msg.replace("\n", " | "))
        return False
    ttl = int(CFG["ENTRY_CONFIRM_MIN"]) * 60
    state["entry_pending"] = {"source": source, "until": time.time() + ttl}
    save_state()
    ask_confirm("entry_fallback", {"source": source},
                head + f"\n\nपर्याय: {d_fb:.0f}Δ + Hedge {hd_fb} OTM (Expiry {bw}, Spot {spot:.0f}):\n" + plan_lines(legs2) +
                "\n(होकार दिल्यावर त्या क्षणाचे ताजे भाव घेऊन एंट्री होईल)", ttl)
    return "ASKED"


def decline_fallback(why):
    ep = state.get("entry_pending") or {}
    state["entry_pending"] = None
    if "Re-entry" in str(ep.get("source", "")):
        state["reentry_at"] = 0.0
        nxt = "पुढच्या ट्रेडिंग दिवशी ९:३५ ला पुन्हा प्रयत्न."
    else:
        state["skip_entry_day"] = today_str()
        nxt = "आज नवीन एंट्री नाही. पुढच्या ट्रेडिंग दिवशी ९:३५ ला पुन्हा प्रयत्न."
    save_state()
    alert(f"❎ 20Δ/100 पर्याय: {why} → {nxt}")


def place_entry(legs, bw, mid, plan_name, hd, source):
    # नवीन ट्रेड नंबर (Sheet मधला Trade ID यावरून)
    state["trade_no"] += 1
    state.update({"legs": {}, "realized": 0.0, "shifts": 0, "expiry": bw, "rollover": mid,
                  "imbalance": None, "extra_noted": [], "plan": plan_name, "hedge_dist": hd, "loss_alert_level": 0,
                  "entry_time": now().strftime("%Y-%m-%d %H:%M:%S")})
    save_state()

    # ऑर्डर: आधी Hedge BUY, मग Short SELL
    for role, side in (("CE_H", "BUY"), ("PE_H", "BUY"), ("CE_S", "SELL"), ("PE_S", "SELL")):
        lg = legs[role]
        ok, px = place(lg["sec_id"], side, lg["ltp"], f"ENTRY_{role}_{side}")
        if not ok:
            sheet_order("ENTRY", role, lg["strike"], lg["opt"], side, QTY, 0, None, _last_oid, "FAILED", plan_name)
            close_legs(list(state["legs"].keys()), {}, typ="ENTRY-UNDO")
            state["in_trade"] = False
            save_state()
            sheet_trade("CANCELLED", exit_reason=f"{ROLE_NAME[role]} {side} अयशस्वी")
            halt(f"एंट्री: {ROLE_NAME[role]} {side} अयशस्वी — बाकी legs बंद केले.")
            return False
        state["legs"][role] = {**lg, "entry": px}
        save_state()
        sheet_order("ENTRY", role, lg["strike"], lg["opt"], side, QTY, px, None, _last_oid, note=plan_name)
        if role == "PE_H":
            time.sleep(0.5)

    state.update({"in_trade": True, "reentry_at": 0.0})
    save_state()
    sheet_trade("OPEN", 0.0, 0.0)
    L = state["legs"]
    alert(f"🟢 नवीन ट्रेड {trade_id()} ({source}) | प्लॅन: {plan_name}\nExpiry {bw} | Rollover {mid}\n"
          f"Hedge BUY: {L['CE_H']['strike']} CE @{L['CE_H']['entry']} , {L['PE_H']['strike']} PE @{L['PE_H']['entry']}\n"
          f"Short SELL: {L['CE_S']['strike']} CE @{L['CE_S']['entry']} , {L['PE_S']['strike']} PE @{L['PE_S']['entry']}\n"
          f"Qty {QTY} | Target +₹{target_amt():.0f} | {sl_txt()}")
    return True


# ---------------- एक्झिट ----------------
def target_amt():
    return float(CFG["DEPLOYED_CAPITAL"]) * float(CFG["PROFIT_TARGET_PCT"]) / 100.0


def sl_amt():
    return float(CFG["DEPLOYED_CAPITAL"]) * float(CFG["MAX_SL_PCT"]) / 100.0


def sl_on():
    return bool(CFG.get("USE_STOPLOSS")) and float(CFG["MAX_SL_PCT"]) > 0


def sl_txt():
    return f"SL -₹{sl_amt():.0f}" if sl_on() else "SL: बंद (फक्त ॲडजस्टमेंट)"


def max_risk():
    """expiry ला सगळ्यात वाईट स्थितीतला एकूण P&L (Hedge मुळे मर्यादित). (रक्कम, बाजू)"""
    L = state["legs"]
    if not all(r in L for r in ("CE_S", "PE_S", "CE_H", "PE_H")):
        return None, ""
    credit = (L["CE_S"]["entry"] + L["PE_S"]["entry"] - L["CE_H"]["entry"] - L["PE_H"]["entry"]) * QTY
    w_ce = L["CE_H"]["strike"] - L["CE_S"]["strike"]
    w_pe = L["PE_S"]["strike"] - L["PE_H"]["strike"]
    ce_worst = state["realized"] + credit - w_ce * QTY
    pe_worst = state["realized"] + credit - w_pe * QTY
    return (ce_worst, f"CE बाजू, अंतर {w_ce}") if ce_worst < pe_worst else (pe_worst, f"PE बाजू, अंतर {w_pe}")


def current_ltps():
    spot, oc = fetch_chain(state["expiry"])
    out = {}
    if oc:
        for r, lg in state["legs"].items():
            out[r] = leg_info(oc, lg["strike"], lg["opt"])[0]
    return spot, oc, out


def schedule_reentry(msg, wait_sec=None):
    if wait_sec is None:
        wait_sec = float(CFG["REENTRY_WAIT_MIN"]) * 60
    at = time.time() + float(wait_sec)
    at_dt = datetime.fromtimestamp(at, IST)
    if hm(at_dt) < LAST_NEW_ENTRY:
        state["reentry_at"] = at
        return msg + f"\n⏳ री-एंट्री {at_dt.strftime('%H:%M:%S')} ला (त्याच expiry मध्ये)."
    state["reentry_at"] = 0.0
    return msg + "\n⏳ आज वेळ संपली — पुढच्या ट्रेडिंग दिवशी ९:३५ ला नवीन ट्रेड."


def full_exit(reason, reentry, ltps=None, qtys=None):
    if not state["in_trade"]:
        return
    log(f"=== FULL EXIT सुरू === कारण: {reason}")
    if ltps is None:
        _, _, ltps = current_ltps()
    ok = close_legs(list(state["legs"].keys()), ltps or {}, qtys)
    if not ok or state["legs"]:
        halt(f"FULL EXIT मध्ये काही ऑर्डर अयशस्वी ({reason}). उरलेले legs: {list(state['legs'].keys())}")
        return
    state["in_trade"] = False
    state["imbalance"] = None
    journal(reason)
    sheet_trade("CLOSED", 0.0, state["realized"], reason)
    msg = f"🔴 ट्रेड {trade_id()} बंद | कारण: {reason} | शिफ्ट: {state['shifts']} | Net P&L: ₹{state['realized']:.2f}"
    if reentry:
        msg = schedule_reentry(msg, None if reentry is True else reentry)
    save_state()
    alert(msg)


# ---------------- मॉनिटरिंग, 2x शिफ्टिंग (नियम ३, ४, ५) ----------------
_last_status = 0.0
_last_sheet_status = 0.0
LAST = {"time": 0.0, "spot": 0.0, "ltp": {}, "unreal": 0.0, "net": 0.0}


def calc_pnl(ltp):
    L = state["legs"]
    unreal = 0.0
    for r, lg in L.items():
        p = ltp.get(r) or lg["entry"]
        unreal += ((lg["entry"] - p) if r.endswith("_S") else (p - lg["entry"])) * QTY
    return unreal, state["realized"] + unreal


def monitor():
    global _last_status, _last_sheet_status
    spot, oc = fetch_chain(state["expiry"])
    if not spot:
        return
    L = state["legs"]
    ltp = {r: leg_info(oc, L[r]["strike"], L[r]["opt"])[0] for r in L}
    if ltp.get("CE_S", 0) <= 0 or ltp.get("PE_S", 0) <= 0:
        return
    for r in ("CE_H", "PE_H"):
        if r in L and ltp.get(r, 0) <= 0:
            ltp[r] = L[r]["entry"]
    unreal, net = calc_pnl(ltp)
    LAST.update({"time": time.time(), "spot": spot, "ltp": ltp, "unreal": unreal, "net": net})

    if time.time() - _last_status >= STATUS_EVERY_SEC:
        _last_status = time.time()
        log(f"STATUS {trade_id()} | Spot {spot:.2f} | CE {L['CE_S']['strike']} @{ltp['CE_S']} | PE {L['PE_S']['strike']} @{ltp['PE_S']} | "
            f"Booked ₹{state['realized']:.0f} | Net ₹{net:.0f} | T +{target_amt():.0f} | {sl_txt()}")
    if time.time() - _last_sheet_status >= SHEET_STATUS_EVERY_SEC:
        _last_sheet_status = time.time()
        sheet_trade("OPEN", unreal, net)

    # Gap नियम: ९:१५-९:१७ फक्त निरीक्षण
    if hm() < GAP_WATCH_END:
        return

    if net >= target_amt():
        full_exit(f"Target +{CFG['PROFIT_TARGET_PCT']}% (Net ₹{net:.0f})", int(CFG["TARGET_REENTRY_SEC"]) or 1, ltp, imbalance_qtys())
        return
    if sl_on() and net <= -sl_amt():
        full_exit(f"Max SL -{CFG['MAX_SL_PCT']}% (Net ₹{net:.0f})", True, ltp, imbalance_qtys())
        return
    # SL बंद: तोट्याचे फक्त इशारे (प्रत्येक नवीन पायरीवर एकदा), एक्झिट नाही
    step = float(CFG["DEPLOYED_CAPITAL"]) * float(CFG["LOSS_ALERT_STEP_PCT"]) / 100.0
    if step > 0 and net < 0:
        lvl = int(-net // step)
        if lvl > int(state.get("loss_alert_level") or 0):
            state["loss_alert_level"] = lvl
            save_state()
            risk, side = max_risk()
            alert(f"⚠️ तोटा इशारा ({trade_id()}): Net ₹{net:.0f} (−₹{lvl * step:.0f} ची पायरी ओलांडली). SL बंद आहे — बॉट ॲडजस्टमेंट चालू ठेवेल.\n"
                  + (f"Hedge मुळे expiry ला कमाल धोका: ₹{risk:.0f} ({side}).\n" if risk is not None else "")
                  + "हाताने बंद करायचे असल्यास /exit")

    if state.get("imbalance"):      # असंतुलन असताना 2x शिफ्टिंग थांबलेली
        return
    ce, pe = ltp["CE_S"], ltp["PE_S"]
    if pe >= 2.0 * ce:
        log(f"⚡ 2x Trigger: PE @{pe} ≥ 2 × CE @{ce}")
        auto_shift("CE", pe, spot, oc, ltp)
    elif ce >= 2.0 * pe:
        log(f"⚡ 2x Trigger: CE @{ce} ≥ 2 × PE @{pe}")
        auto_shift("PE", ce, spot, oc, ltp)


def plan_shift(side, spot, oc, target_prem=None, strike=None):
    """नवीन strike ठरवणे. (new_k, new_ltp, sid, error) परत."""
    L = state["legs"]
    role = f"{side}_S"
    other = "PE_S" if side == "CE" else "CE_S"
    old_k, other_k = L[role]["strike"], L[other]["strike"]
    if strike is None:
        if side == "CE":
            new_k, new_ltp = closest_premium(oc, target_prem, "CE", lambda k: k < old_k)
        else:
            new_k, new_ltp = closest_premium(oc, target_prem, "PE", lambda k: k > old_k)
    else:
        new_k = int(strike)
        new_ltp = leg_info(oc, new_k, side)[0]
        if new_k % STRIKE_STEP:
            return None, None, None, "strike १०० च्या पटीत हवा"
        if new_ltp <= 0:
            return None, None, None, f"{new_k}{side} चा भाव मिळाला नाही"
        if new_k == old_k:
            return None, None, None, "हा strike आधीच चालू आहे"
    if new_k is None:
        return None, None, None, "योग्य strike सापडला नाही"
    if side == "CE" and (new_k <= other_k or new_k <= spot):
        return new_k, new_ltp, None, f"Strike Limit: {new_k}CE हा Straddle/ITM/ओव्हरलॅप मध्ये जातो"
    if side == "PE" and (new_k >= other_k or new_k >= spot):
        return new_k, new_ltp, None, f"Strike Limit: {new_k}PE हा Straddle/ITM/ओव्हरलॅप मध्ये जातो"
    sid = get_sid(oc, state["expiry"], new_k, side)
    if not sid:
        return new_k, new_ltp, None, f"{new_k}{side} चा SecurityID मिळाला नाही"
    return new_k, new_ltp, sid, None


def execute_shift(side, new_k, new_ltp, sid, ltp, typ, target_note=""):
    role = f"{side}_S"
    L = state["legs"]
    old = L[role]
    ok, px = place(old["sec_id"], "BUY", ltp.get(role) or old["entry"], f"{typ}_BOOK_{side}")
    if not ok:
        sheet_order(f"{typ} BOOK", role, old["strike"], side, "BUY", QTY, 0, None, _last_oid, "FAILED")
        alert(f"शिफ्ट: {old['strike']}{side} बुकिंग अयशस्वी — पुढच्या फेरीत पुन्हा प्रयत्न.")
        return False
    booked = (old["entry"] - px) * QTY
    state["realized"] += booked
    sheet_order(f"{typ} BOOK", role, old["strike"], side, "BUY", QTY, px, booked, _last_oid)
    L.pop(role)
    save_state()
    ok, px2 = place(sid, "SELL", new_ltp, f"{typ}_NEW_{side}")
    if not ok:
        sheet_order(f"{typ} NEW", role, new_k, side, "SELL", QTY, 0, None, _last_oid, "FAILED")
        full_exit(f"शिफ्ट: नवीन {new_k}{side} विकता आला नाही — सुरक्षेसाठी पूर्ण एक्झिट", False, ltp)
        halt("शिफ्ट मधली SELL ऑर्डर अयशस्वी.")
        return False
    L[role] = {"strike": new_k, "opt": side, "sec_id": sid, "entry": px2}
    state["shifts"] += 1
    save_state()
    sheet_order(f"{typ} NEW", role, new_k, side, "SELL", QTY, px2, None, _last_oid, note=target_note)
    unreal, net = calc_pnl(ltp)
    sheet_trade("OPEN", unreal, net)
    alert(f"🔁 {typ} #{state['shifts']} ({trade_id()}): {old['strike']}{side} बुक (₹{booked:.0f}) → नवीन {new_k}{side} SELL @{px2} "
          f"{target_note} | Booked एकूण ₹{state['realized']:.0f}")
    return True


def auto_shift(side, target_prem, spot, oc, ltp, typ="SHIFT"):
    new_k, new_ltp, sid, err = plan_shift(side, spot, oc, target_prem=target_prem)
    if err and err.startswith("Strike Limit"):
        full_exit(f"Strike Limit (नियम ५.४): {err}", True, ltp)
        return
    if err:
        log(f"शिफ्ट पुढे ढकलली: {err}")
        return
    execute_shift(side, new_k, new_ltp, sid, ltp, typ, f"(हवा होता {target_prem})")


def eod_check():
    """३:१० ची दिवसअखेर तपासणी: मोठा premium ≥ 1.65 × लहान असेल तर आजच ॲडजस्टमेंट."""
    state["eod_check_day"] = today_str()
    save_state()
    spot, oc, ltp = current_ltps()
    if not spot or ltp.get("CE_S", 0) <= 0 or ltp.get("PE_S", 0) <= 0:
        alert("🕒 ३:१० तपासणी: भाव मिळाले नाहीत — आज तपासणी झाली नाही.")
        return
    for r in ("CE_H", "PE_H"):
        if r in state["legs"] and ltp.get(r, 0) <= 0:
            ltp[r] = state["legs"][r]["entry"]
    ce, pe = ltp["CE_S"], ltp["PE_S"]
    big, small = max(ce, pe), min(ce, pe)
    ratio = big / small if small > 0 else 0
    thr = float(CFG["EOD_RATIO"])
    L = state["legs"]
    head = (f"🕒 ३:१० दिवसअखेर तपासणी ({trade_id()}): CE {L['CE_S']['strike']} @{ce} | PE {L['PE_S']['strike']} @{pe}"
            f" → फरक {ratio:.3f}x (मर्यादा {thr}x)")
    if state.get("imbalance"):
        return alert(head + "\n⚠️ Qty असंतुलन चालू आहे — ॲडजस्टमेंट नाही.")
    if ratio + 1e-9 < thr:
        return alert(head + "\n✅ मर्यादेपेक्षा कमी — आज ॲडजस्टमेंट नाही.")
    side = "CE" if ce < pe else "PE"
    alert(head + f"\n⚡ मर्यादा ओलांडली → लहान बाजू {side} बुक करून {big} च्या जवळचा strike विकत आहे.")
    auto_shift(side, big, spot, oc, ltp, "EOD-ADJ")


# ---------------- Broker Match (फक्त LIVE) ----------------
_last_broker_check = 0.0


def broker_positions():
    """Dhan मधल्या MARGIN positions: {securityId: netQty} आणि पूर्ण यादी."""
    d = unwrap(dhan.get_positions(), "Positions")
    if not isinstance(d, list):
        return None, None
    net = {}
    for p in d:
        if str(p.get("productType", "")).upper() != "MARGIN":
            continue
        sid = str(p.get("securityId"))
        net[sid] = net.get(sid, 0) + int(float(p.get("netQty") or 0))
    return net, d


def bot_available(net):
    """प्रत्येक बॉट-leg साठी Dhan मध्ये बॉटच्या दिशेने किती qty उपलब्ध आहे (कमाल QTY)."""
    avail, extra = {}, {}
    for role, lg in state["legs"].items():
        b = net.get(str(lg["sec_id"]), 0)
        have = max(0, -b) if role.endswith("_S") else max(0, b)
        avail[role] = min(have, QTY)
        extra[role] = max(0, have - QTY)
    return avail, extra


def imbalance_qtys():
    imb = state.get("imbalance")
    if not imb:
        return None
    return {r: QTY - int(imb["need"].get(r, 0)) for r in state["legs"]}


def need_text(need):
    out = []
    for role, q in need.items():
        lg = state["legs"][role]
        side = "SELL" if role.endswith("_S") else "BUY"
        out.append(f"• {lg['strike']} {lg['opt']} ({ROLE_NAME[role]}) — {q} qty {side} करा")
    return "\n".join(out)


def manual_exit():
    unreal, net = calc_pnl(LAST.get("ltp") or {})
    state["realized"] = net
    state["legs"] = {}
    state["in_trade"] = False
    state["imbalance"] = None
    state["paused"] = True
    reason = "MANUAL EXIT (Dhan मधून हाताने बंद)"
    journal(reason + " — P&L अंदाजे")
    sheet_trade("CLOSED", None, net, reason + " — P&L अंदाजे")
    save_state()
    alert(f"⚠️ {trade_id()} चे सगळे legs Dhan मध्ये बंद दिसले → MANUAL EXIT नोंदवला (अंदाजे P&L ₹{net:.0f}).\n"
          f"⏸️ बॉट PAUSE. नवीन ट्रेड हवे असतील तेव्हा /resume पाठवा.")


def broker_check():
    global _last_broker_check
    if PAPER or not state["in_trade"] or state["halted"]:
        return
    if time.time() - _last_broker_check < BROKER_CHECK_SEC or time.time() - _last_order_time < 20:
        return
    _last_broker_check = time.time()
    net, _ = broker_positions()
    if net is None:
        return
    avail, extra = bot_available(net)
    hedges_gone = all(avail.get(r, 0) == 0 for r in ("CE_H", "PE_H"))
    shorts_cut = all(avail.get(r, 0) < QTY for r in ("CE_S", "PE_S"))
    if all(v == 0 for v in avail.values()) or (hedges_gone and shorts_cut):
        manual_exit()
        return
    for role, x in extra.items():
        key = f"{state['legs'][role]['sec_id']}"
        if x > 0 and key not in state["extra_noted"]:
            state["extra_noted"].append(key)
            save_state()
            lg = state["legs"][role]
            alert(f"ℹ️ {lg['strike']} {lg['opt']} मध्ये तुमची {x} qty वेगळी ओळखली. बॉट फक्त स्वतःच्या {QTY} qty सांभाळेल.")
    need = {r: QTY - a for r, a in avail.items() if a < QTY}
    imb = state.get("imbalance")
    if need:
        if not imb:
            dl = time.time() + BALANCE_WAIT_MIN * 60
            state["imbalance"] = {"since": time.time(), "deadline": dl, "need": need}
            save_state()
            alert(f"⚠️ असंतुलन ({trade_id()}): बॉटच्या qty पैकी काही Dhan मध्ये कमी आहेत.\n"
                  f"संतुलनासाठी:\n{need_text(need)}\n(किंवा /fix पाठवा — बॉट स्वतः करेल)\n"
                  f"⏳ {datetime.fromtimestamp(dl, IST).strftime('%H:%M')} पर्यंत संतुलित न झाल्यास बॉट आपला ट्रेड पूर्ण एक्झिट करेल.\n"
                  f"तोपर्यंत SL/Target चालू, 2x शिफ्टिंग थांबलेली.")
        else:
            imb["need"] = need
            save_state()
            if time.time() >= imb["deadline"]:
                state["paused"] = True
                full_exit("Qty असंतुलन १५ मिनिटांत दूर झाले नाही", False, None, avail)
                alert("⏸️ बॉट PAUSE (असंतुलनामुळे). नवीन ट्रेड हवे असतील तेव्हा /resume पाठवा.")
    elif imb:
        state["imbalance"] = None
        save_state()
        alert(f"✅ {trade_id()} पुन्हा संतुलित झाला. 2x शिफ्टिंग पुन्हा सुरू.")


# ---------------- Telegram commands ----------------
CMDQ = queue.Queue()
PENDING = {}


def _tg_poller():
    offset = None
    while True:
        t = CFG.get("TELEGRAM_BOT_TOKEN")
        if not t:
            time.sleep(10)
            continue
        try:
            if offset is None:      # जुने (बॉट बंद असतानाचे) मेसेज दुर्लक्षित
                r = requests.get(f"https://api.telegram.org/bot{t}/getUpdates", params={"offset": -1}, timeout=20)
                res = r.json().get("result", [])
                offset = (res[-1]["update_id"] + 1) if res else 0
                continue
            r = requests.get(f"https://api.telegram.org/bot{t}/getUpdates",
                             params={"timeout": 25, "offset": offset}, timeout=35)
            for u in r.json().get("result", []):
                offset = u["update_id"] + 1
                m = u.get("message") or u.get("edited_message") or {}
                txt = (m.get("text") or "").strip()
                cid = str((m.get("chat") or {}).get("id", ""))
                name = (m.get("from") or {}).get("first_name", "")
                if txt:
                    CMDQ.put((cid, txt, name))
        except Exception:
            time.sleep(5)


HELP = """🤖 IC बॉट commands
माहिती:
/status — चालू ट्रेड, legs, भाव, P&L
/positions — Dhan मधल्या सगळ्या positions
/pnl — या ट्रेडचा / आजचा / महिन्याचा P&L
/trades — शेवटचे ५ ट्रेड
/today — आजचा सारांश
/log — log च्या शेवटच्या ओळी
/ping — बॉट चालू आहे का
/settings — सेटिंग्ज
नियंत्रण (प्लॅन दाखवून ६० सेकंदांत /yes):
/pause — नवीन ट्रेड थांबवा
/resume — पुन्हा सुरू (HALT सुद्धा काढतो)
/exit — आत्ता पूर्ण एक्झिट + pause
/adjust — लहान बाजू मोठ्या premium जवळ हलवा
/adjust ce  किंवा  /adjust pe — ती बाजू हलवा
/shift ce 24300 — CE short त्या strike वर
/fix — qty असंतुलन बॉट स्वतः भरून काढेल
/yes /no — प्लॅनला होकार / नकार"""


def read_trades():
    rows = []
    if os.path.exists(TRADES_FILE):
        with open(TRADES_FILE, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i == 0:
                    continue
                p = line.strip().split(",", 7)
                if len(p) >= 8:
                    rows.append(p)
    return rows


def status_text():
    flags = []
    if state["halted"]:
        flags.append("⛔ HALT")
    if state["paused"]:
        flags.append("⏸️ PAUSE")
    if state.get("imbalance"):
        flags.append("⚠️ असंतुलन")
    head = f"📊 {'PAPER' if PAPER else 'LIVE'} | {BOT_VERSION} {' '.join(flags)}"
    if not state["in_trade"]:
        nxt = "पुढचा ट्रेड: ९:३५ (ट्रेडिंग दिवशी)"
        if state.get("entry_pending"):
            nxt = "⏳ 20Δ/100 पर्यायासाठी तुमच्या /yes किंवा /no ची वाट पाहतोय"
        if state.get("reentry_at"):
            nxt = f"री-एंट्री: {datetime.fromtimestamp(state['reentry_at'], IST).strftime('%H:%M:%S')}"
        return f"{head}\nचालू ट्रेड नाही.\n{nxt}"
    L, ltp = state["legs"], LAST.get("ltp") or {}
    age = int(time.time() - LAST["time"]) if LAST["time"] else -1
    lines = [head, f"{trade_id()} | प्लॅन {state.get('plan') or '-'} | Expiry {state['expiry']} | Rollover {state['rollover']} | शिफ्ट {state['shifts']}",
             f"Spot {LAST['spot']:.2f} (भाव {age if age >= 0 else '?'} सेकंदांपूर्वीचा)"]
    for r in ("CE_S", "PE_S", "CE_H", "PE_H"):
        if r in L:
            lines.append(f"{ROLE_NAME[r]}: {L[r]['strike']} | एंट्री {L[r]['entry']:.2f} | आत्ता {ltp.get(r, 0):.2f}")
    lines.append(f"Booked ₹{state['realized']:.0f} | Unbooked ₹{LAST['unreal']:.0f} | Net ₹{LAST['net']:.0f}")
    lines.append(f"Target +₹{target_amt():.0f} (बाकी ₹{target_amt() - LAST['net']:.0f}) | "
                 + (f"SL -₹{sl_amt():.0f} (बाकी ₹{LAST['net'] + sl_amt():.0f})" if sl_on() else "SL: बंद"))
    risk, side = max_risk()
    if risk is not None:
        lines.append(f"Hedge मुळे expiry ला कमाल धोका: ₹{risk:.0f} ({side})")
    return "\n".join(lines)


def pnl_text():
    rows = read_trades()
    mode = "PAPER" if PAPER else "LIVE"
    t, mth = today_str(), today_str()[:7]
    day = sum(float(r[6]) for r in rows if r[1] == mode and r[3][:10] == t)
    mon = sum(float(r[6]) for r in rows if r[1] == mode and r[3][:7] == mth)
    cur = LAST["net"] if state["in_trade"] else 0.0
    return (f"💰 P&L ({mode})\nचालू ट्रेड: ₹{cur:.0f}\nआज बंद झालेले: ₹{day:.0f} | आज एकूण: ₹{day + cur:.0f}\n"
            f"या महिन्यात बंद झालेले: ₹{mon:.0f} | एकूण: ₹{mon + cur:.0f}")


def trades_text():
    rows = [r for r in read_trades() if r[1] == ("PAPER" if PAPER else "LIVE")][-5:]
    if not rows:
        return "अजून एकही बंद ट्रेड नाही."
    out = ["📒 शेवटचे ट्रेड"]
    for r in rows:
        tid = f"{'P' if r[1] == 'PAPER' else 'L'}-IC-{int(r[0]):04d}"
        out.append(f"{tid} | {r[3][5:16]} | शिफ्ट {r[5]} | ₹{float(r[6]):.0f} | {r[7].strip(chr(34))[:40]}")
    return "\n".join(out)


def today_text():
    rows = [r for r in read_trades() if r[1] == ("PAPER" if PAPER else "LIVE") and r[3][:10] == today_str()]
    out = [f"📅 आजचा सारांश ({today_str()})", f"आज बंद झालेले ट्रेड: {len(rows)}"]
    for r in rows:
        out.append(f"• #{r[0]} | शिफ्ट {r[5]} | ₹{float(r[6]):.0f} | {r[7].strip(chr(34))[:40]}")
    out.append(pnl_text())
    if state["in_trade"]:
        out.append(status_text())
    return "\n".join(out)


def positions_text():
    net, rows = broker_positions()
    if rows is None:
        return "Dhan positions मिळाल्या नाहीत."
    bot = {str(lg["sec_id"]): (r, lg) for r, lg in state["legs"].items()} if not PAPER else {}
    out = ["📋 Dhan positions (उघड्या)"]
    for p in rows:
        q = int(float(p.get("netQty") or 0))
        if q == 0:
            continue
        sid = str(p.get("securityId"))
        tag = ""
        if sid in bot and str(p.get("productType", "")).upper() == "MARGIN":
            r, lg = bot[sid]
            mine = q - (-QTY if r.endswith("_S") else QTY)
            tag = f" 🤖 बॉट {(-QTY if r.endswith('_S') else QTY)} | तुमची {mine}"
        out.append(f"{p.get('tradingSymbol', sid)} | {p.get('productType', '')} | Qty {q}{tag}")
    if len(out) == 1:
        out.append("एकही उघडी position नाही.")
    if PAPER:
        out.append("(PAPER मोड: बॉटचे ट्रेड Dhan मध्ये दिसत नाहीत)")
    return "\n".join(out)


def settings_text():
    return (f"⚙️ {BOT_VERSION} | {'PAPER' if PAPER else 'LIVE'}\nLots {CFG['LOTS']} × {CFG['LOT_SIZE']} = {QTY}\n"
            f"Capital ₹{CFG['DEPLOYED_CAPITAL']} | Target {CFG['PROFIT_TARGET_PCT']}% | "
            f"{('SL ' + str(CFG['MAX_SL_PCT']) + '%') if sl_on() else 'SL: बंद'} | तोटा इशारा दर {CFG['LOSS_ALERT_STEP_PCT']}%\n"
            f"Hedge {CFG['HEDGE_DISTANCE']} pts | री-एंट्री: Target नंतर {CFG['TARGET_REENTRY_SEC']} सेकंद, Strike Limit नंतर {CFG['REENTRY_WAIT_MIN']} मि. | Balance वेळ {BALANCE_WAIT_MIN} मि.\n"
            f"Liquidity: spread ≤ {CFG['LIQ_MAX_SPREAD_PCT']}% (किमान ₹{CFG['LIQ_MIN_SPREAD_RS']}) | पर्याय {CFG['FALLBACK_DELTA']:.0f}Δ / {CFG['FALLBACK_HEDGE']} OTM\n"
            f"दर तासाला status: {'✅' if CFG['HOURLY_STATUS'] else '❌'} | दिवसअखेर तपासणी {int(CFG['EOD_CHECK_HHMM']) // 100}:{int(CFG['EOD_CHECK_HHMM']) % 100:02d} ≥ {CFG['EOD_RATIO']}x\n"
            f"Gap निरीक्षण ९:१५-९:१७ | TOTP: {'✅' if totp_enabled() else '❌'} | Sheet: {'✅' if CFG.get('SHEET_WEBHOOK_URL') else '❌'}")


def log_text():
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            lines = f.readlines()[-20:]
        return "🧾 log:\n" + "".join(l[11:] for l in lines)[-3500:]
    except Exception as e:
        return f"log वाचता आला नाही: {e}"


def ask_confirm(kind, data, plan_txt, ttl=CONFIRM_SEC):
    PENDING.clear()
    PENDING.update({"kind": kind, "data": data, "expires": time.time() + ttl})
    wait = f"{ttl // 60} मिनिटांत" if ttl >= 120 else f"{ttl} सेकंदांत"
    tg(f"📋 प्लॅन ({trade_id() if state['in_trade'] else '-'}):\n{plan_txt}\n\nपक्कं करायला {wait} /yes पाठवा (रद्द: /no)")


def plan_adjust(side=None, strike=None):
    if not state["in_trade"]:
        return tg("चालू ट्रेड नाही.")
    spot, oc, ltp = current_ltps()
    if not spot:
        return tg("भाव मिळाले नाहीत, थोड्या वेळाने पुन्हा प्रयत्न करा.")
    if side is None:
        side = "CE" if ltp["CE_S"] < ltp["PE_S"] else "PE"
    other = "PE_S" if side == "CE" else "CE_S"
    target = ltp[other] if strike is None else None
    new_k, new_ltp, sid, err = plan_shift(side, spot, oc, target_prem=target, strike=strike)
    if err:
        return tg(f"❌ ॲडजस्टमेंट शक्य नाही: {err}")
    old = state["legs"][f"{side}_S"]
    b = (old["entry"] - ltp[f"{side}_S"]) * QTY
    txt = (f"{old['strike']}{side} @ {ltp[f'{side}_S']:.2f} → BUY (बुक ₹{b:.0f})\n"
           f"नवीन: {new_k}{side} @ {new_ltp:.2f} → SELL\n"
           f"दुसरी बाजू: {state['legs'][other]['strike']} @ {ltp[other]:.2f}\nStrike Limit तपासणी: ✅")
    if strike is not None and new_ltp >= 2 * ltp[other]:
        txt += "\n⚠️ नवीन premium दुसऱ्या बाजूच्या दुप्पट होईल → पुढच्या फेरीत 2x ॲडजस्टमेंट होईल."
    ask_confirm("adjust", {"side": side, "new_k": new_k, "sid": sid}, txt)


def handle_command(cid, txt, name):
    owner = str(CFG.get("TELEGRAM_CHAT_ID", ""))
    if cid != owner:
        alert(f"⚠️ अनोळखी व्यक्तीने ({name}, id {cid}) '{txt[:30]}' पाठवले — दुर्लक्ष केले.")
        return
    parts = txt.lower().replace("@", " ").split()
    cmd = parts[0] if parts else ""
    if PENDING and time.time() > PENDING.get("expires", 0):
        PENDING.clear()
    if cmd in ("/start", "/help"):
        tg(HELP)
    elif cmd == "/status":
        tg(status_text())
    elif cmd == "/positions":
        tg(positions_text())
    elif cmd == "/pnl":
        tg(pnl_text())
    elif cmd == "/trades":
        tg(trades_text())
    elif cmd == "/today":
        tg(today_text())
    elif cmd == "/log":
        tg(log_text())
    elif cmd == "/settings":
        tg(settings_text())
    elif cmd == "/ping":
        age = int(time.time() - LAST["time"]) if LAST["time"] else None
        tg(f"✅ बॉट चालू | {'PAPER' if PAPER else 'LIVE'} | {now().strftime('%H:%M:%S')}"
           + (f" | शेवटचा भाव {age} सेकंदांपूर्वी" if age is not None else ""))
    elif cmd == "/pause":
        state["paused"] = True
        save_state()
        alert("⏸️ PAUSE: नवीन ट्रेड घेणार नाही (चालू ट्रेड सांभाळत राहील). सुरू करायला /resume")
    elif cmd == "/resume":
        state["halted"] = False
        state["paused"] = False
        save_state()
        alert("▶️ RESUME: बॉट पुन्हा नियमांप्रमाणे सुरू.")
    elif cmd == "/exit":
        if not state["in_trade"]:
            return tg("चालू ट्रेड नाही.")
        ask_confirm("exit", {}, f"{trade_id()} चे सगळे legs बंद (आधी Short, मग Hedge) + PAUSE")
    elif cmd == "/adjust":
        side = parts[1].upper() if len(parts) > 1 and parts[1] in ("ce", "pe") else None
        plan_adjust(side)
    elif cmd == "/shift":
        if len(parts) < 3 or parts[1] not in ("ce", "pe") or not parts[2].isdigit():
            return tg("असं लिहा: /shift ce 24300  किंवा  /shift pe 23700")
        plan_adjust(parts[1].upper(), int(parts[2]))
    elif cmd == "/fix":
        imb = state.get("imbalance")
        if PAPER:
            return tg("PAPER मोडमध्ये /fix लागू नाही.")
        if not imb:
            return tg("सध्या कोणतंही असंतुलन नाही ✅")
        ask_confirm("fix", {"need": imb["need"]}, "बॉट या ऑर्डर टाकेल:\n" + need_text(imb["need"]))
    elif cmd == "/yes":
        if not PENDING:
            return tg("पक्कं करायला कोणताही प्लॅन नाही (किंवा ६० सेकंद उलटले).")
        p = dict(PENDING)
        PENDING.clear()
        run_pending(p)
    elif cmd == "/no":
        kind = PENDING.get("kind")
        PENDING.clear()
        if kind == "entry_fallback" or state.get("entry_pending"):
            decline_fallback("तुम्ही नकार दिला")
        else:
            tg("❎ प्लॅन रद्द केला.")
    else:
        tg("हा command समजला नाही. /help पाठवा.")


def run_pending(p):
    kind, d = p["kind"], p["data"]
    if kind == "entry_fallback":
        state["entry_pending"] = None
        save_state()
        n = now()
        if state["in_trade"] or not market_open(n) or hm(n) >= LAST_NEW_ENTRY:
            return tg("आता एंट्रीची वेळ/स्थिती नाही — रद्द.")
        tg("👍 होकार मिळाला — 20Δ/100 प्लॅनने ताज्या भावात एंट्री करत आहे...")
        if not enter_trade(d.get("source", "") + " + 20Δ/100 पर्याय", use_fallback=True):
            decline_fallback("एंट्री होऊ शकली नाही")
        return
    if kind == "exit":
        state["paused"] = True
        save_state()
        full_exit("हाताने /exit (Telegram)", False)
        alert("🛑 /exit पूर्ण + PAUSE. नवीन ट्रेडसाठी /resume")
    elif kind == "adjust":
        if not state["in_trade"]:
            return tg("ट्रेड आता चालू नाही — रद्द.")
        spot, oc, ltp = current_ltps()
        if not spot:
            return tg("भाव मिळाले नाहीत — रद्द.")
        new_ltp = leg_info(oc, d["new_k"], d["side"])[0]
        execute_shift(d["side"], d["new_k"], new_ltp, d["sid"], ltp, "MANUAL-ADJ", "(Telegram /adjust)")
    elif kind == "fix":
        L = state["legs"]
        spot, oc, ltp = current_ltps()
        for role, q in d["need"].items():
            if role not in L:
                continue
            lg = L[role]
            side = "SELL" if role.endswith("_S") else "BUY"
            ok, px = place(lg["sec_id"], side, ltp.get(role) or lg["entry"], f"FIX_{role}_{side}", q)
            sheet_order("FIX", role, lg["strike"], lg["opt"], side, q, px if ok else 0, None, _last_oid,
                        "TRADED" if ok else "FAILED")
        tg("🔧 /fix ऑर्डर पाठवल्या. ३० सेकंदांत Broker Match पुन्हा तपासेल.")


def process_commands():
    while not CMDQ.empty():
        try:
            handle_command(*CMDQ.get_nowait())
        except Exception as e:
            log(f"Command error: {e}\n{traceback.format_exc()}")


# ---------------- Control files (terminal मधून, बॅकअप) ----------------
def check_control_files():
    for name in ("RESUME", "PAUSE", "EXITNOW"):
        p = os.path.join(BASE_DIR, name)
        if not os.path.exists(p):
            continue
        try:
            os.remove(p)
        except Exception:
            pass
        if name == "RESUME":
            state["halted"] = False
            state["paused"] = False
            save_state()
            alert("▶️ RESUME: बॉट पुन्हा सुरू.")
        elif name == "PAUSE":
            state["paused"] = True
            save_state()
            alert("⏸️ PAUSE: नवीन ट्रेड घेणार नाही.")
        elif name == "EXITNOW":
            state["paused"] = True
            save_state()
            if state["in_trade"]:
                full_exit("हाताने EXITNOW", False)
            alert("🛑 EXITNOW: पूर्ण एक्झिट + PAUSE.")


# ---------------- Startup ----------------
def startup_checks():
    global _token_day
    log("=" * 60)
    log(f"NIFTY 15Δ IRON CONDOR BOT {BOT_VERSION} | मोड: {'📝 PAPER (खोट्या ऑर्डर)' if PAPER else '💰 LIVE (खऱ्या ऑर्डर)'}")
    log(f"Lots {CFG['LOTS']} × {CFG['LOT_SIZE']} = {QTY} Qty | Capital ₹{CFG['DEPLOYED_CAPITAL']} | "
        f"Target +₹{target_amt():.0f} | {sl_txt()} | Hedge {CFG['HEDGE_DISTANCE']} pts")
    tok = str(CFG["DHAN_ACCESS_TOKEN"])
    log(f"Client {CFG['DHAN_CLIENT_ID']} | Token {tok[:8]}...{tok[-6:]} | State: {os.path.basename(STATE_FILE)} | "
        f"TOTP {'✅' if totp_enabled() else '❌'} | Telegram {'✅' if CFG.get('TELEGRAM_CHAT_ID') else '❌'} | "
        f"Sheet {'✅' if CFG.get('SHEET_WEBHOOK_URL') else '❌'}")
    try:
        f = unwrap(dhan.get_fund_limits(), "Fund limits")
        if isinstance(f, dict):
            if _token_day is None:
                _token_day = today_str()     # token चालू आहे → आज पुन्हा मागायची गरज नाही
            log(f"✅ Token बरोबर आहे. Available margin: {f.get('availabelBalance', f.get('availableBalance'))}")
        elif _token_day == today_str():
            log("✅ जुना Token बाद होता — TOTP ने नवीन Token घेतला.")
        else:
            alert("❌ Token तपासणी अयशस्वी — TOTP सेटअप पुन्हा करा: python3 setup_totp.py")
    except Exception as e:
        log(f"Fund check error: {e}")
    try:
        myip = requests.get("https://checkip.amazonaws.com", timeout=8).text.strip()
        ipinfo = DhanLogin(str(CFG["DHAN_CLIENT_ID"])).get_ip(str(CFG["DHAN_ACCESS_TOKEN"]))
        info = ipinfo.get("data") if isinstance(ipinfo, dict) else ipinfo
        log(f"सर्व्हर IP: {myip} | Dhan Static IP: {info}")
    except Exception as e:
        log(f"IP check error: {e}")
    if state["in_trade"]:
        log(f"चालू ट्रेड {trade_id()} सापडला (expiry {state['expiry']}) — मॉनिटरिंग पुढे सुरू.")
    if state["halted"]:
        log("⚠️ बॉट HALT स्थितीत आहे. सुरू करायला Telegram वर /resume")
    log("=" * 60)


def main():
    global CFG, CFG_MTIME, PAPER, QTY, STATE_FILE
    CFG = read_config()
    CFG_MTIME = os.path.getmtime(CONFIG_FILE)
    PAPER = bool(CFG["PAPER_MODE"])
    QTY = int(CFG["LOTS"]) * int(CFG["LOT_SIZE"])
    STATE_FILE = os.path.join(BASE_DIR, "state_paper.json" if PAPER else "state_live.json")
    threading.Thread(target=_outbox_worker, daemon=True).start()
    threading.Thread(target=_tg_poller, daemon=True).start()
    build_client()
    load_state()
    startup_checks()
    alert(f"🚀 बॉट सुरू {BOT_VERSION} ({'PAPER' if PAPER else 'LIVE'}) — {QTY} Qty. Commands साठी /help")

    last_halt_log = 0.0
    last_cfg_check = 0.0
    while True:
        try:
            if time.time() - last_cfg_check > 20:
                last_cfg_check = time.time()
                reload_token_if_changed()
            check_control_files()
            process_commands()
            n = now()
            if totp_enabled() and is_trading_day(n) and hm(n) >= (8, 45) and _token_day != today_str():
                refresh_token("रोजचा सकाळचा Token")

            if state["halted"]:
                if time.time() - last_halt_log > 300:
                    last_halt_log = time.time()
                    log("⛔ HALT — काहीही करत नाही. सुरू करायला Telegram वर /resume")
                time.sleep(2)
                continue

            if is_trading_day(n) and hm(n) >= DAILY_SUMMARY_TIME and state.get("summary_day") != today_str():
                state["summary_day"] = today_str()
                save_state()
                tg(today_text())

            if not market_open(n):
                time.sleep(2)
                continue

            t = today_str()
            hk = n.strftime("%Y-%m-%d %H")
            if CFG["HOURLY_STATUS"] and n.minute == 0 and 10 <= n.hour <= 15 and state.get("last_hourly") != hk:
                state["last_hourly"] = hk
                save_state()
                tg(f"🕐 {n.strftime('%H:%M')} तासाचा रिपोर्ट\n" + status_text())

            ep = state.get("entry_pending")
            if ep and time.time() > float(ep.get("until", 0)):
                if PENDING.get("kind") == "entry_fallback":
                    PENDING.clear()
                decline_fallback(f"{CFG['ENTRY_CONFIRM_MIN']} मिनिटांत उत्तर आलं नाही")
            # एक्झिट नियम ५.१: मधल्या expiry दिवशी ९:३० (किंवा ती तारीख उलटून गेली असेल तर लगेच)
            if state["in_trade"] and state.get("rollover") and (
                    t > state["rollover"] or (t == state["rollover"] and hm(n) >= ROLLOVER_EXIT_TIME)):
                full_exit("Rollover Time Exit (नियम ५.१)", False, None, imbalance_qtys())

            if not state["in_trade"] and state.get("entry_pending"):
                pass                                  # 20Δ/100 पर्यायासाठी तुमच्या उत्तराची वाट
            elif not state["in_trade"]:
                ra = float(state.get("reentry_at") or 0)
                if ra > 0:
                    if time.time() >= ra and hm(n) < LAST_NEW_ENTRY and not state["paused"]:
                        state["reentry_at"] = 0.0
                        save_state()
                        r = enter_trade("Re-entry (नियम ६)")
                        if r is False and not state["halted"]:
                            state["reentry_at"] = time.time() + 60
                            save_state()
                elif ENTRY_TIME <= hm(n) < ENTRY_WINDOW_END and not state["paused"] and state.get("skip_entry_day") != t:
                    enter_trade("९:३५ एंट्री")
                elif ENTRY_WINDOW_END <= hm(n) < (9, 45) and state.get("no_entry_alert_day") != t \
                        and not state["paused"] and state.get("skip_entry_day") != t:
                    state["no_entry_alert_day"] = t
                    save_state()
                    alert("ℹ️ आज ९:३५-९:४० मध्ये ट्रेड घेतला गेला नाही (/log पाहा).")
            else:
                broker_check()
                if state["in_trade"] and not state["halted"]:
                    monitor()
                    eh, em = divmod(int(CFG["EOD_CHECK_HHMM"]), 100)
                    if state["in_trade"] and not state["halted"] and hm(n) >= (eh, em) \
                            and state.get("eod_check_day") != t:
                        eod_check()
                    if state["in_trade"] and state["imbalance"] and time.time() >= state["imbalance"]["deadline"]:
                        broker_check()
            time.sleep(LOOP_SLEEP)
        except KeyboardInterrupt:
            log("Ctrl+C — बॉट बंद केला.")
            break
        except Exception as e:
            log(f"Error: {e}\n{traceback.format_exc()}")
            time.sleep(5)


if __name__ == "__main__":
    main()
