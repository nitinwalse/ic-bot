# -*- coding: utf-8 -*-
# config.py मध्ये Client ID आणि Token भरणे — nano शिवाय.
# चालवा:   python3 setup_config.py
# रोज सकाळी नवीन Token टाकण्यासाठीही हीच कमांड वापरा (बॉट restart करायची गरज नाही).
import os, re, shutil

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(BASE, "config.py")
EX = os.path.join(BASE, "config_example.py")


def clean(v):
    v = v.strip().strip('"').strip("'").strip()
    h = len(v) // 2
    if h and len(v) % 2 == 0 and v[:h] == v[h:]:   # दुप्पट पेस्ट दुरुस्त
        v = v[:h]
    return v


if not os.path.exists(CFG):
    shutil.copy(EX, CFG)
    print("config.py नव्हती — config_example.py मधून नवीन बनवली.")

print("\n========== Dhan Client ID आणि Token भरणे ==========\n")
cid = clean(input("1) तुमचा Dhan Client ID टाईप करा आणि Enter दाबा: "))
while not cid.isdigit():
    cid = clean(input("   Client ID मध्ये फक्त आकडे हवेत. पुन्हा टाईप करा: "))

tok = clean(input("2) आता Dhan Access Token पेस्ट करा आणि Enter दाबा: "))
while not (tok.startswith("eyJ") and len(tok) > 100):
    tok = clean(input("   हा Token बरोबर वाटत नाही (eyJ ने सुरू व्हायला हवा). पुन्हा पेस्ट करा: "))

txt = open(CFG, encoding="utf-8").read()
txt = re.sub(r'^DHAN_CLIENT_ID\s*=.*$', lambda m: f'DHAN_CLIENT_ID = "{cid}"', txt, count=1, flags=re.M)
txt = re.sub(r'^DHAN_ACCESS_TOKEN\s*=\s*".*$', lambda m: f'DHAN_ACCESS_TOKEN = "{tok}"', txt, count=1, flags=re.M)
with open(CFG, "w", encoding="utf-8") as f:
    f.write(txt)

print(f"\n✅ config.py मध्ये सेव्ह झाले.  Client: {cid}  |  Token: {tok[:8]}...{tok[-6:]}")

try:
    from dhanhq import DhanContext, dhanhq
    r = dhanhq(DhanContext(cid, tok)).get_fund_limits()
    if isinstance(r, dict) and r.get("status") == "success":
        print("✅ Dhan ने Token स्वीकारला. आता पुढची कमांड:  python3 bot.py\n")
    else:
        rem = r.get("remarks") if isinstance(r, dict) else r
        print(f"❌ Dhan ने Token नाकारला: {rem}")
        print("   web.dhan.co वरून नवीन Token बनवा आणि पुन्हा चालवा:  python3 setup_config.py\n")
except Exception as e:
    print(f"⚠️ Token तपासता आला नाही: {e}\n")
