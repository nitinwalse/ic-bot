# ================= Nitin Iron Condor Bot — config.py =================
# फक्त या file मध्ये बदल करायचे. bot.py ला हात लावायचा नाही.
# बदल केल्यावर: Token बदलला तर बॉट आपोआप घेतो (restart नको).
# बाकी कोणताही बदल केला तर:  sudo systemctl restart icbot

DHAN_CLIENT_ID = "तुमचा_CLIENT_ID"
DHAN_ACCESS_TOKEN = "इथे_नवीन_TOKEN_पेस्ट_करा"
DHAN_PIN = ""              # setup_totp.py भरेल — GitHub वर कधीच भरू नका
DHAN_TOTP_SECRET = ""      # setup_totp.py भरेल — GitHub वर कधीच भरू नका

PAPER_MODE = True          # True = खोट्या ऑर्डर (टेस्ट)   |   False = खऱ्या ऑर्डर (LIVE)

LOTS = 8                   # NIFTY लॉट
LOT_SIZE = 65              # NIFTY चा १ लॉट
DEPLOYED_CAPITAL = 600000  # ₹ भांडवल
PROFIT_TARGET_PCT = 1.25   # एकूण नफा या % ला पोहोचला की पूर्ण एक्झिट
MAX_SL_PCT = 1.25          # एकूण तोटा या % ला पोहोचला की पूर्ण एक्झिट
HEDGE_DISTANCE = 200       # Short strike पासून Hedge किती points बाहेर
REENTRY_WAIT_MIN = 5       # Target/SL/Strike-limit नंतर किती मिनिटांनी री-एंट्री

# NSE सुट्ट्या (YYYY-MM-DD) — NSE च्या वेबसाईटवर तपासून यादी पूर्ण करा
HOLIDAYS = ["2026-11-24", "2026-12-25"]

# Telegram alert (ऐच्छिक — रिकामे ठेवले तर alert जाणार नाहीत)
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# Google Sheet (ऐच्छिक — setup_extras.py भरेल)
SHEET_WEBHOOK_URL = ""
SHEET_SECRET = ""

# --- दुप्पट पेस्ट झालेला Token आपोआप दुरुस्त (या ओळींना हात लावू नका) ---
DHAN_ACCESS_TOKEN = DHAN_ACCESS_TOKEN.strip()
if len(DHAN_ACCESS_TOKEN) % 2 == 0 and DHAN_ACCESS_TOKEN[:len(DHAN_ACCESS_TOKEN)//2] == DHAN_ACCESS_TOKEN[len(DHAN_ACCESS_TOKEN)//2:]: DHAN_ACCESS_TOKEN = DHAN_ACCESS_TOKEN[:len(DHAN_ACCESS_TOKEN)//2]
