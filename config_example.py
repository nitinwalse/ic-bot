# ================= Nitin IC Bot V3.0 — config.py =================
# फक्त या file मध्ये बदल करायचे. bot.py ला हात लावायचा नाही.
# Token/Telegram/Sheet बदलले तर बॉट आपोआप घेतो. लॉट Telegram वर /lots 12 ने बदला.
# बाकी कोणताही बदल केला तर:  sudo systemctl restart icbot

DHAN_CLIENT_ID = "तुमचा_CLIENT_ID"
DHAN_ACCESS_TOKEN = "इथे_नवीन_TOKEN_पेस्ट_करा"
DHAN_PIN = ""              # setup_totp.py भरेल — GitHub वर कधीच भरू नका
DHAN_TOTP_SECRET = ""      # setup_totp.py भरेल — GitHub वर कधीच भरू नका

PAPER_MODE = True          # True = खोट्या ऑर्डर (टेस्ट)   |   False = खऱ्या ऑर्डर (LIVE)

LOTS = 12                  # NIFTY लॉट (Telegram: /lots 12)
LOT_SIZE = 65              # NIFTY चा १ लॉट
MAX_LOTS = 27              # /lots ची कमाल मर्यादा (27 × 65 = 1755, NSE freeze 1800 च्या आत)

# ---- Target / Stop-loss (प्रति लॉट) ----
TARGET_PER_LOT = 1100      # 12 लॉट → +₹13,200
SL_PER_LOT = 1800          # 12 लॉट → −₹21,600

# ---- Hedge ----
HEDGE_FALLBACK_OTM = 100   # Short पासून इतके points OTM, पुढच्या expiry मध्ये

# ---- ॲडजस्टमेंट ----
EOD_RATIO = 1.65           # या वेळेपासून 2x ऐवजी 1.65x (कितीही वेळा)
EOD_CHECK_HHMM = 1510      # १५:१०

# ---- री-एंट्री ----
REENTRY_COOL_MIN = 15      # एक्झिटनंतर cooling (सकाळी ९:३५ / ३:१० नंतर ३:१४ हे अपवाद)

# ---- Liquidity ----
FALLBACK_DELTA = 20        # 15Δ liquid नसेल तर आपोआप हा Delta (फक्त Telegram वर माहिती)
LIQ_MAX_SPREAD_PCT = 5.0   # bid-ask spread LTP च्या या % पेक्षा जास्त = illiquid
LIQ_MIN_SPREAD_RS = 1.0    # पण ₹1 पर्यंतचा spread नेहमी चालेल
LIQ_MIN_OI = 0

HOURLY_STATUS = True       # दर तासाला (१०:०० ते ३:००) Telegram वर status

# ---- जुन्या आवृत्तीच्या ओळी (V3.0 मध्ये वापरात नाहीत, पण असल्या पाहिजेत) ----
DEPLOYED_CAPITAL = 600000
PROFIT_TARGET_PCT = 1.25
MAX_SL_PCT = 1.25
HEDGE_DISTANCE = 200
REENTRY_WAIT_MIN = 15

# NSE सुट्ट्या (कोडमध्ये १० नोव्हें., २४ नोव्हें., २५ डिसें. आधीच आहेत) — जास्तीच्या असल्यास इथे
HOLIDAYS = []

# Telegram / Google Sheet (setup_extras.py भरेल)
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""
SHEET_WEBHOOK_URL = ""
SHEET_SECRET = ""

# --- दुप्पट पेस्ट झालेला Token आपोआप दुरुस्त (या ओळींना हात लावू नका) ---
DHAN_ACCESS_TOKEN = DHAN_ACCESS_TOKEN.strip()
if len(DHAN_ACCESS_TOKEN) % 2 == 0 and DHAN_ACCESS_TOKEN[:len(DHAN_ACCESS_TOKEN)//2] == DHAN_ACCESS_TOKEN[len(DHAN_ACCESS_TOKEN)//2:]: DHAN_ACCESS_TOKEN = DHAN_ACCESS_TOKEN[:len(DHAN_ACCESS_TOKEN)//2]
