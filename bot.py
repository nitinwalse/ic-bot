# -*- coding: utf-8 -*-
"""
=====================================================================
 Nitin — NIFTY Bi-Weekly 15 Delta Hedged Iron Condor Bot
 Version : V1.0  (02-11-2026)
 Broker  : Dhan (dhanhq 2.3.x — DhanContext पद्धत)
 Server  : AWS EC2 (Ubuntu)
 Mode    : PAPER (खोट्या ऑर्डर) / LIVE (खऱ्या ऑर्डर) — config.py मध्ये
---------------------------------------------------------------------
 या file ला हात लावायचा नाही. सर्व बदल config.py मध्येच.
=====================================================================
"""
import os
import sys
import json
import time
import math
import logging
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dhanhq import DhanContext, dhanhq, DhanLogin

BOT_VERSION = "V1.0"
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
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)
LAST_NEW_ENTRY = (15, 15)     # यानंतर नवीन ट्रेड/री-एंट्री नाही
CHAIN_MIN_GAP = 3.2           # Dhan option chain: ३ सेकंदात १ request
LOOP_SLEEP = 0.5
STATUS_EVERY_SEC = 60

REQUIRED_KEYS = ["DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "PAPER_MODE", "LOTS", "LOT_SIZE",
                 "DEPLOYED_CAPITAL", "PROFIT_TARGET_PCT", "MAX_SL_PCT", "HEDGE_DISTANCE",
                 "REENTRY_WAIT_MIN"]

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
    cfg["HOLIDAYS"] = list(ns.get("HOLIDAYS", []))
    cfg["TELEGRAM_BOT_TOKEN"] = str(ns.get("TELEGRAM_BOT_TOKEN", "") or "")
    cfg["TELEGRAM_CHAT_ID"] = str(ns.get("TELEGRAM_CHAT_ID", "") or "")
    return cfg


def build_client():
    global dhan
    dhan = dhanhq(DhanContext(str(CFG["DHAN_CLIENT_ID"]), str(CFG["DHAN_ACCESS_TOKEN"])))


def reload_token_if_changed():
    """config.py मध्ये नवीन token टाकला की बॉट restart न करता तो वापरतो."""
    global CFG, CFG_MTIME
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
            alert("🔑 नवीन Access Token लोड झाला (restart न करता).")
        CFG["HOLIDAYS"] = new["HOLIDAYS"]
        CFG["TELEGRAM_BOT_TOKEN"] = new["TELEGRAM_BOT_TOKEN"]
        CFG["TELEGRAM_CHAT_ID"] = new["TELEGRAM_CHAT_ID"]
    except Exception as e:
        log(f"config.py वाचताना चूक: {e}")


# ---------------- Telegram ----------------
def tg(msg):
    t, c = CFG.get("TELEGRAM_BOT_TOKEN"), CFG.get("TELEGRAM_CHAT_ID")
    if not t or not c:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{t}/sendMessage",
                      data={"chat_id": c, "text": msg}, timeout=8)
    except Exception as e:
        log(f"Telegram पाठवता आला नाही: {e}")


def alert(msg):
    log(msg)
    tg(f"[IC-BOT {'PAPER' if PAPER else 'LIVE'}] {msg}")


# ---------------- वेळ ----------------
def now():
    return datetime.now(IST)


def today_str():
    return now().strftime("%Y-%m-%d")


def hm(n=None):
    n = n or now()
    return (n.hour, n.minute)


def is_trading_day(n):
    return n.weekday() < 5 and n.strftime("%Y-%m-%d") not in CFG.get("HOLIDAYS", [])


def market_open(n):
    return is_trading_day(n) and MARKET_OPEN <= hm(n) <= MARKET_CLOSE


# ---------------- State ----------------
def blank_state():
    return {"in_trade": False, "halted": False, "paused": False,
            "expiry": None, "rollover": None, "legs": {},
            "realized": 0.0, "shifts": 0, "trade_no": 0,
            "reentry_at": 0.0, "entry_time": None, "no_entry_alert_day": None}


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
    alert(f"⛔ बॉट थांबवला (HALT): {reason}\n"
          f"Dhan मध्ये positions तपासा. सगळं ठीक झाल्यावर terminal मध्ये: touch ~/ic/RESUME")


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
        if any(x in txt for x in ("DH-901", "DH-905", "Invalid", "invalid", "expired", "Expired", "IP")):
            if time.time() - _last_auth_alert > 900:
                _last_auth_alert = time.time()
                alert(f"🔑 Dhan अडचण ({what}): {txt}\nToken expire / चुकीचा किंवा Static IP ची अडचण असू शकते.")
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
def place(sec_id, side, ltp, tag):
    """LIVE मध्ये ऑर्डर टाकून ती TRADED झाली का ते तपासते. (ok, भाव) परत देते."""
    if PAPER:
        log(f"📝 PAPER ORDER [{tag}] {side} {QTY} Qty | SecID {sec_id} | @ {ltp:.2f}")
        return True, ltp
    try:
        res = dhan.place_order(security_id=str(sec_id), exchange_segment=dhan.NSE_FNO,
                               transaction_type=dhan.BUY if side == "BUY" else dhan.SELL,
                               quantity=QTY, order_type=dhan.MARKET,
                               product_type=dhan.MARGIN, price=0)
    except Exception as e:
        alert(f"❌ ORDER error [{tag}]: {e}")
        return False, 0.0
    d = unwrap(res, f"Order {tag}")
    oid = d.get("orderId") if isinstance(d, dict) else None
    if not oid:
        alert(f"❌ ORDER नाकारली [{tag}]: {res}")
        return False, 0.0
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
            log(f"✅ ORDER TRADED [{tag}] {side} {QTY} @ {px:.2f} (ID {oid})")
            return True, px
        if st in ("REJECTED", "CANCELLED", "EXPIRED"):
            alert(f"❌ ORDER {st} [{tag}] कारण: {o.get('omsErrorDescription') or o.get('omsErrorCode')}")
            return False, 0.0
    alert(f"⚠️ ORDER १० सेकंदात TRADED झाली नाही [{tag}] ID {oid} — Dhan मध्ये तपासा!")
    return False, 0.0


def close_legs(roles, ltps):
    """roles मधले legs बंद करणे: आधी Short (BUY), मग Hedge (SELL). बंद झालेले legs state मधून काढले जातात."""
    L = state["legs"]
    ok_all = True
    for role in ("CE_S", "PE_S"):
        if role in roles and role in L:
            ok, px = place(L[role]["sec_id"], "BUY", ltps.get(role) or L[role]["entry"], f"EXIT_{role}_BUY")
            if ok:
                state["realized"] += (L[role]["entry"] - px) * QTY
                L.pop(role)
                save_state()
            else:
                ok_all = False
    time.sleep(0.5)
    for role in ("CE_H", "PE_H"):
        if role in roles and role in L:
            ok, px = place(L[role]["sec_id"], "SELL", ltps.get(role) or L[role]["entry"], f"EXIT_{role}_SELL")
            if ok:
                state["realized"] += (px - L[role]["entry"]) * QTY
                L.pop(role)
                save_state()
            else:
                ok_all = False
    return ok_all


def journal(reason):
    new = not os.path.exists(TRADES_FILE)
    with open(TRADES_FILE, "a", encoding="utf-8") as f:
        if new:
            f.write("trade_no,mode,entry_time,exit_time,expiry,shifts,net_pnl,reason\n")
        f.write(f"{state['trade_no']},{'PAPER' if PAPER else 'LIVE'},{state.get('entry_time')},"
                f"{now().strftime('%Y-%m-%d %H:%M:%S')},{state.get('expiry')},{state.get('shifts')},"
                f"{state['realized']:.2f},\"{reason}\"\n")


# ---------------- एंट्री (नियम २) ----------------
def enter_trade(source):
    mid, bw = get_expiries()
    if not bw:
        log("Expiry list मिळाली नाही — एंट्री नाही.")
        return False
    spot, oc = fetch_chain(bw)
    if not spot:
        return False
    days = (datetime.strptime(bw, "%Y-%m-%d").date() - now().date()).days
    T = max(days, 0.5) / 365.0

    # स्टेप १: दोन्ही बाजूंचे 15 Delta (१०० च्या पटीत)
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
            gap = abs(dl - TARGET_DELTA)
            if best[opt] is None or gap < best[opt][0]:
                best[opt] = (gap, k, ltp, dl)
    if not best["CE"] or not best["PE"]:
        log("15 Delta चे strikes सापडले नाहीत.")
        return False
    ce_k, ce_ltp, pe_k, pe_ltp = best["CE"][1], best["CE"][2], best["PE"][1], best["PE"][2]
    log(f"[{source}] Expiry {bw} | Spot {spot:.2f} | 15Δ → CE {ce_k} @{ce_ltp} (Δ{best['CE'][3]:.1f}) | "
        f"PE {pe_k} @{pe_ltp} (Δ{best['PE'][3]:.1f})")

    # स्टेप २: मोठा प्रिमियम base, दुसऱ्या बाजूला सर्वात जवळचा प्रिमियम
    if ce_ltp >= pe_ltp:
        base = ce_ltp
        pe_k, pe_ltp = closest_premium(oc, base, "PE", lambda k: k < spot)
        log(f"स्टेप २: CE base {base} → PE बाजूला जवळचा: {pe_k} @{pe_ltp}")
    else:
        base = pe_ltp
        ce_k, ce_ltp = closest_premium(oc, base, "CE", lambda k: k > spot)
        log(f"स्टेप २: PE base {base} → CE बाजूला जवळचा: {ce_k} @{ce_ltp}")
    if not ce_k or not pe_k:
        log("Matching strike सापडला नाही.")
        return False

    # स्टेप ३: 200 Points OTM Hedge
    hd = int(CFG["HEDGE_DISTANCE"])
    plan = {"CE_S": (ce_k, "CE"), "PE_S": (pe_k, "PE"), "CE_H": (ce_k + hd, "CE"), "PE_H": (pe_k - hd, "PE")}
    legs = {}
    for role, (k, opt) in plan.items():
        ltp, _, _ = leg_info(oc, k, opt)
        sid = get_sid(oc, bw, k, opt)
        if ltp <= 0 or not sid:
            log(f"{role} {k}{opt}: LTP किंवा SecurityID मिळाले नाही — एंट्री रद्द.")
            return False
        legs[role] = {"strike": k, "opt": opt, "sec_id": sid, "ltp": ltp}

    # ऑर्डर: आधी Hedge BUY, मग Short SELL
    state["legs"] = {}
    state["realized"] = 0.0
    for role in ("CE_H", "PE_H"):
        ok, px = place(legs[role]["sec_id"], "BUY", legs[role]["ltp"], f"ENTRY_{role}_BUY")
        if not ok:
            close_legs(list(state["legs"].keys()), {})
            halt(f"एंट्री: {role} Hedge BUY अयशस्वी — Short विकले नाहीत.")
            return False
        state["legs"][role] = {**legs[role], "entry": px}
        save_state()
    time.sleep(0.5)
    for role in ("CE_S", "PE_S"):
        ok, px = place(legs[role]["sec_id"], "SELL", legs[role]["ltp"], f"ENTRY_{role}_SELL")
        if not ok:
            close_legs(list(state["legs"].keys()), {})
            halt(f"एंट्री: {role} SELL अयशस्वी — बाकी legs बंद केले.")
            return False
        state["legs"][role] = {**legs[role], "entry": px}
        save_state()

    state.update({"in_trade": True, "expiry": bw, "rollover": mid, "shifts": 0,
                  "trade_no": state["trade_no"] + 1, "reentry_at": 0.0,
                  "entry_time": now().strftime("%Y-%m-%d %H:%M:%S")})
    save_state()
    L = state["legs"]
    alert(f"🟢 नवीन ट्रेड #{state['trade_no']} ({source}) | Expiry {bw} | Rollover {mid}\n"
          f"Hedge BUY: {L['CE_H']['strike']} CE @{L['CE_H']['entry']} , {L['PE_H']['strike']} PE @{L['PE_H']['entry']}\n"
          f"Short SELL: {L['CE_S']['strike']} CE @{L['CE_S']['entry']} , {L['PE_S']['strike']} PE @{L['PE_S']['entry']}\n"
          f"Qty {QTY} | Target +₹{target_amt():.0f} | SL -₹{sl_amt():.0f}")
    return True


# ---------------- एक्झिट ----------------
def target_amt():
    return float(CFG["DEPLOYED_CAPITAL"]) * float(CFG["PROFIT_TARGET_PCT"]) / 100.0


def sl_amt():
    return float(CFG["DEPLOYED_CAPITAL"]) * float(CFG["MAX_SL_PCT"]) / 100.0


def current_ltps():
    spot, oc = fetch_chain(state["expiry"])
    out = {}
    if oc:
        for r, lg in state["legs"].items():
            out[r] = leg_info(oc, lg["strike"], lg["opt"])[0]
    return spot, oc, out


def full_exit(reason, reentry, ltps=None):
    if not state["in_trade"]:
        return
    log(f"=== FULL EXIT सुरू === कारण: {reason}")
    if ltps is None:
        _, _, ltps = current_ltps()
    ok = close_legs(list(state["legs"].keys()), ltps or {})
    if not ok or state["legs"]:
        halt(f"FULL EXIT मध्ये काही ऑर्डर अयशस्वी ({reason}). उरलेले legs: {list(state['legs'].keys())}")
        return
    state["in_trade"] = False
    journal(reason)
    msg = f"🔴 ट्रेड #{state['trade_no']} बंद | कारण: {reason} | शिफ्ट: {state['shifts']} | Net P&L: ₹{state['realized']:.2f}"
    if reentry:
        at = time.time() + float(CFG["REENTRY_WAIT_MIN"]) * 60
        at_dt = datetime.fromtimestamp(at, IST)
        if hm(at_dt) < LAST_NEW_ENTRY:
            state["reentry_at"] = at
            msg += f"\n⏳ री-एंट्री {at_dt.strftime('%H:%M:%S')} ला (त्याच expiry मध्ये)."
        else:
            state["reentry_at"] = 0.0
            msg += "\n⏳ आज वेळ संपली — उद्या ९:३५ ला नवीन ट्रेड."
    save_state()
    alert(msg)


# ---------------- मॉनिटरिंग आणि 2x शिफ्टिंग (नियम ३, ४, ५) ----------------
_last_status = 0.0


def monitor():
    global _last_status
    spot, oc = fetch_chain(state["expiry"])
    if not spot:
        return
    L = state["legs"]
    ltp = {r: leg_info(oc, L[r]["strike"], L[r]["opt"])[0] for r in L}
    if ltp.get("CE_S", 0) <= 0 or ltp.get("PE_S", 0) <= 0:
        return
    for r in ("CE_H", "PE_H"):
        if ltp.get(r, 0) <= 0:
            ltp[r] = L[r]["entry"]
    unreal = ((L["CE_S"]["entry"] - ltp["CE_S"]) + (L["PE_S"]["entry"] - ltp["PE_S"])) * QTY \
        + ((ltp["CE_H"] - L["CE_H"]["entry"]) + (ltp["PE_H"] - L["PE_H"]["entry"])) * QTY
    net = state["realized"] + unreal

    if time.time() - _last_status >= STATUS_EVERY_SEC:
        _last_status = time.time()
        log(f"STATUS | Spot {spot:.2f} | CE {L['CE_S']['strike']} @{ltp['CE_S']} | PE {L['PE_S']['strike']} @{ltp['PE_S']} | "
            f"Booked ₹{state['realized']:.0f} | Net ₹{net:.0f} | T +{target_amt():.0f} / SL -{sl_amt():.0f}")

    if net >= target_amt():
        full_exit(f"Target +{CFG['PROFIT_TARGET_PCT']}% (Net ₹{net:.0f})", True, ltp)
        return
    if net <= -sl_amt():
        full_exit(f"Max SL -{CFG['MAX_SL_PCT']}% (Net ₹{net:.0f})", True, ltp)
        return

    ce, pe = ltp["CE_S"], ltp["PE_S"]
    if pe >= 2.0 * ce:
        shift("CE", pe, spot, oc, ltp)
    elif ce >= 2.0 * pe:
        shift("PE", ce, spot, oc, ltp)


def shift(side, target_prem, spot, oc, ltp):
    role = f"{side}_S"
    other = "PE_S" if side == "CE" else "CE_S"
    L = state["legs"]
    old_k, other_k = L[role]["strike"], L[other]["strike"]
    log(f"⚡ 2x Trigger: {other} @{ltp[other]} ≥ 2 × {role} @{ltp[role]}")
    if side == "CE":
        new_k, new_ltp = closest_premium(oc, target_prem, "CE", lambda k: k < old_k)
        bad = new_k is None or new_k <= other_k or new_k <= spot
    else:
        new_k, new_ltp = closest_premium(oc, target_prem, "PE", lambda k: k > old_k)
        bad = new_k is None or new_k >= other_k or new_k >= spot
    if bad:
        full_exit(f"Strike Limit (नियम ५.४): नवीन {side} {new_k} — Straddle/ITM/ओव्हरलॅप", True, ltp)
        return
    sid = get_sid(oc, state["expiry"], new_k, side)
    if not sid:
        log(f"नवीन {new_k}{side} चा SecurityID मिळाला नाही — पुढच्या फेरीत पुन्हा.")
        return
    ok, px = place(L[role]["sec_id"], "BUY", ltp[role], f"SHIFT_BOOK_{side}")
    if not ok:
        alert(f"शिफ्ट: {old_k}{side} बुकिंग अयशस्वी — पुढच्या फेरीत पुन्हा प्रयत्न.")
        return
    booked = (L[role]["entry"] - px) * QTY
    state["realized"] += booked
    L.pop(role)
    save_state()
    ok, px2 = place(sid, "SELL", new_ltp, f"SHIFT_NEW_{side}")
    if not ok:
        full_exit(f"शिफ्ट: नवीन {new_k}{side} विकता आला नाही — सुरक्षेसाठी पूर्ण एक्झिट", False, ltp)
        halt("शिफ्ट मधली SELL ऑर्डर अयशस्वी.")
        return
    L[role] = {"strike": new_k, "opt": side, "sec_id": sid, "entry": px2}
    state["shifts"] += 1
    save_state()
    alert(f"🔁 शिफ्ट #{state['shifts']}: {old_k}{side} बुक (₹{booked:.0f}) → नवीन {new_k}{side} SELL @{px2} "
          f"(हवा होता {target_prem}) | Booked एकूण ₹{state['realized']:.0f}")


# ---------------- Control files ----------------
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
            alert("⏸️ PAUSE: नवीन ट्रेड घेणार नाही (चालू ट्रेड सांभाळत राहील). सुरू करायला: touch ~/ic/RESUME")
        elif name == "EXITNOW":
            state["paused"] = True
            save_state()
            if state["in_trade"]:
                full_exit("हाताने EXITNOW", False)
            alert("🛑 EXITNOW: पूर्ण एक्झिट + PAUSE. सुरू करायला: touch ~/ic/RESUME")


# ---------------- Startup ----------------
def startup_checks():
    log("=" * 60)
    log(f"NIFTY 15Δ IRON CONDOR BOT {BOT_VERSION} | मोड: {'📝 PAPER (खोट्या ऑर्डर)' if PAPER else '💰 LIVE (खऱ्या ऑर्डर)'}")
    log(f"Lots {CFG['LOTS']} × {CFG['LOT_SIZE']} = {QTY} Qty | Capital ₹{CFG['DEPLOYED_CAPITAL']} | "
        f"Target +₹{target_amt():.0f} | SL -₹{sl_amt():.0f} | Hedge {CFG['HEDGE_DISTANCE']} pts")
    tok = str(CFG["DHAN_ACCESS_TOKEN"])
    log(f"Client {CFG['DHAN_CLIENT_ID']} | Token {tok[:8]}...{tok[-6:]} | State: {os.path.basename(STATE_FILE)}")
    try:
        f = unwrap(dhan.get_fund_limits(), "Fund limits")
        if isinstance(f, dict):
            log(f"✅ Token बरोबर आहे. Available margin: {f.get('availabelBalance', f.get('availableBalance'))}")
        else:
            alert("❌ Token तपासणी अयशस्वी — config.py मध्ये नवीन token टाका.")
    except Exception as e:
        log(f"Fund check error: {e}")
    try:
        myip = requests.get("https://checkip.amazonaws.com", timeout=8).text.strip()
        ipinfo = DhanLogin(str(CFG["DHAN_CLIENT_ID"])).get_ip(tok)
        log(f"सर्व्हर IP: {myip} | Dhan Static IP माहिती: {ipinfo.get('data') if isinstance(ipinfo, dict) else ipinfo}")
    except Exception as e:
        log(f"IP check error: {e}")
    if state["in_trade"]:
        log(f"चालू ट्रेड #{state['trade_no']} सापडला (expiry {state['expiry']}) — मॉनिटरिंग पुढे सुरू.")
    if state["halted"]:
        log("⚠️ बॉट HALT स्थितीत आहे. सुरू करायला: touch ~/ic/RESUME")
    log("=" * 60)


def main():
    global CFG, CFG_MTIME, PAPER, QTY, STATE_FILE
    CFG = read_config()
    CFG_MTIME = os.path.getmtime(CONFIG_FILE)
    PAPER = bool(CFG["PAPER_MODE"])
    QTY = int(CFG["LOTS"]) * int(CFG["LOT_SIZE"])
    STATE_FILE = os.path.join(BASE_DIR, "state_paper.json" if PAPER else "state_live.json")
    build_client()
    load_state()
    startup_checks()
    alert(f"🚀 बॉट सुरू ({'PAPER' if PAPER else 'LIVE'}) — {QTY} Qty")

    last_halt_log = 0.0
    last_cfg_check = 0.0
    while True:
        try:
            if time.time() - last_cfg_check > 20:
                last_cfg_check = time.time()
                reload_token_if_changed()
            check_control_files()
            n = now()

            if state["halted"]:
                if time.time() - last_halt_log > 300:
                    last_halt_log = time.time()
                    log("⛔ HALT — काहीही करत नाही. सुरू करायला: touch ~/ic/RESUME")
                time.sleep(5)
                continue

            if not market_open(n):
                time.sleep(15)
                continue

            t = today_str()
            # एक्झिट नियम ५.१: मधल्या expiry दिवशी ९:३० (किंवा ती तारीख उलटून गेली असेल तर लगेच)
            if state["in_trade"] and state.get("rollover") and (
                    t > state["rollover"] or (t == state["rollover"] and hm(n) >= ROLLOVER_EXIT_TIME)):
                full_exit("Rollover Time Exit (नियम ५.१)", False)

            if not state["in_trade"]:
                ra = float(state.get("reentry_at") or 0)
                if ra > 0:
                    if time.time() >= ra and hm(n) < LAST_NEW_ENTRY and not state["paused"]:
                        state["reentry_at"] = 0.0
                        save_state()
                        if not enter_trade("Re-entry (नियम ६)"):
                            if not state["halted"]:
                                state["reentry_at"] = time.time() + 60
                                save_state()
                elif ENTRY_TIME <= hm(n) < ENTRY_WINDOW_END and not state["paused"]:
                    enter_trade("९:३५ एंट्री")
                elif hm(n) >= ENTRY_WINDOW_END and hm(n) < (9, 45) and state.get("no_entry_alert_day") != t \
                        and not state["paused"]:
                    state["no_entry_alert_day"] = t
                    save_state()
                    alert("ℹ️ आज ९:३५-९:४० मध्ये ट्रेड घेतला गेला नाही (log तपासा).")
            else:
                monitor()
            time.sleep(LOOP_SLEEP)
        except KeyboardInterrupt:
            log("Ctrl+C — बॉट बंद केला.")
            break
        except Exception as e:
            log(f"Error: {e}\n{traceback.format_exc()}")
            time.sleep(5)


if __name__ == "__main__":
    main()
