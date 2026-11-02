# -*- coding: utf-8 -*-
# Telegram + Google Sheet जोडणी — config.py मध्ये आपोआप भरते.
# चालवा:   python3 setup_extras.py      (आधी:  sudo systemctl stop icbot)
import os, re, time, getpass, shutil
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(BASE, "config.py")
EX = os.path.join(BASE, "config_example.py")


def clean(v):
    v = v.strip().strip('"').strip("'").replace(" ", "").strip()
    h = len(v) // 2
    if h and len(v) % 2 == 0 and v[:h] == v[h:]:   # दुप्पट पेस्ट दुरुस्त
        v = v[:h]
    return v


def set_line(txt, key, value):
    line = f'{key} = "{value}"'
    pat = rf'^{key}\s*=.*$'
    if re.search(pat, txt, flags=re.M):
        return re.sub(pat, lambda m: line, txt, count=1, flags=re.M)
    m = re.search(r'^DHAN_ACCESS_TOKEN\s*=\s*".*$', txt, flags=re.M)
    return txt[:m.end()] + "\n" + line + txt[m.end():]


if not os.path.exists(CFG):
    shutil.copy(EX, CFG)
txt = open(CFG, encoding="utf-8").read()
changed = False

print("\n============ भाग १: Telegram ============\n")
ans = input("Telegram जोडायचा? (होय असेल तर Enter, वगळायचे असेल तर n टाईप करून Enter): ").strip().lower()
if ans != "n":
    while True:
        tok = clean(getpass.getpass("1) BotFather ने दिलेला Bot Token पेस्ट करा (दिसणार नाही) आणि Enter: "))
        try:
            me = requests.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=15).json()
        except Exception as e:
            me = {"ok": False, "description": str(e)}
        if me.get("ok"):
            uname = me["result"].get("username")
            print(f"   ✅ बॉट सापडला: @{uname}")
            break
        print(f"   ❌ हा Token चालत नाही ({me.get('description')}). पुन्हा पेस्ट करा.")
    chat_id = ""
    for attempt in range(5):
        input(f"\n2) आता Telegram मध्ये @{uname} उघडा, START दाबा किंवा /start पाठवा.\n   पाठवल्यावर इथे Enter दाबा: ")
        try:
            res = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates", timeout=15).json().get("result", [])
        except Exception:
            res = []
        for u in reversed(res):
            m = u.get("message") or {}
            if m.get("chat", {}).get("id"):
                chat_id = str(m["chat"]["id"])
                who = m.get("from", {}).get("first_name", "")
                break
        if chat_id:
            print(f"   ✅ तुमचा Chat ID सापडला: {chat_id} ({who})")
            break
        print("   अजून मेसेज दिसला नाही. Telegram मध्ये /start पुन्हा पाठवा आणि Enter दाबा.")
    if chat_id:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat_id, "text": "✅ IC बॉटशी जोडणी झाली! बॉट सुरू झाल्यावर /help पाठवा."}, timeout=15)
        print("   📩 Telegram वर चाचणी मेसेज पाठवला — मोबाईलवर पाहा.")
        txt = set_line(txt, "TELEGRAM_BOT_TOKEN", tok)
        txt = set_line(txt, "TELEGRAM_CHAT_ID", chat_id)
        changed = True
    else:
        print("   ❌ Chat ID मिळाला नाही — Telegram भाग जतन केला नाही.")

print("\n============ भाग २: Google Sheet ============\n")
url = clean(input("3) Apps Script चा Web App URL पेस्ट करा (वगळायचे असेल तर नुसतं Enter): "))
if url:
    while not (url.startswith("https://script.google.com/") and url.rstrip("/").endswith("/exec")):
        url = clean(input("   URL https://script.google.com/... ने सुरू होऊन /exec ने संपायला हवा. पुन्हा पेस्ट करा: "))
    sec = clean(input("4) Apps Script मधला SECRET शब्द टाईप करा [NitinIC2026] (तोच असेल तर नुसतं Enter): ")) or "NitinIC2026"
    try:
        r = requests.post(url, json={"kind": "ping", "secret": sec}, timeout=40)
        body = r.text.replace(" ", "")
    except Exception as e:
        body = str(e)
    if '"ok":true' in body:
        print("   ✅ Google Sheet शी जोडणी झाली.")
        txt = set_line(txt, "SHEET_WEBHOOK_URL", url)
        txt = set_line(txt, "SHEET_SECRET", sec)
        changed = True
    elif '"err":"secret"' in body:
        print("   ❌ SECRET जुळत नाही — Apps Script मधला SECRET आणि इथे टाकलेला शब्द सारखा हवा. पुन्हा चालवा.")
    else:
        print(f"   ❌ Sheet ने उत्तर दिले नाही: {body[:200]}")
        print("   Deploy करताना 'Who has access: Anyone' निवडलं का ते तपासा, आणि पुन्हा चालवा.")

if changed:
    with open(CFG, "w", encoding="utf-8") as f:
        f.write(txt)
    os.chmod(CFG, 0o600)
    print("\n✅ config.py मध्ये सेव्ह झाले. आता बॉट सुरू करा:  sudo systemctl start icbot\n")
else:
    print("\nकाहीही बदललं नाही.\n")
