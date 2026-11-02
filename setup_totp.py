# -*- coding: utf-8 -*-
# Dhan TOTP सेटअप — PIN + TOTP Secret config.py मध्ये ठेवणे आणि लगेच नवीन Token घेणे.
# चालवा:   python3 setup_totp.py
# PIN आणि Secret स्क्रीनवर दिसत नाहीत. ते फक्त या सर्व्हरच्या config.py मध्ये राहतात.
import os, re, time, hmac, hashlib, base64, struct, shutil, getpass
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


def totp_now(secret):
    s = secret.upper() + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(s)
    if 30 - (int(time.time()) % 30) < 3:
        time.sleep(4)
    counter = int(time.time()) // 30
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000:06d}"


def set_line(txt, key, value):
    line = f'{key} = "{value}"'
    pat = r'^DHAN_ACCESS_TOKEN\s*=\s*".*$' if key == "DHAN_ACCESS_TOKEN" else rf'^{key}\s*=.*$'
    if re.search(pat, txt, flags=re.M):
        return re.sub(pat, lambda m: line, txt, count=1, flags=re.M)
    m = re.search(r'^DHAN_ACCESS_TOKEN\s*=\s*".*$', txt, flags=re.M)
    return txt[:m.end()] + "\n" + line + txt[m.end():]


if not os.path.exists(CFG):
    shutil.copy(EX, CFG)
txt = open(CFG, encoding="utf-8").read()
m = re.search(r'^DHAN_CLIENT_ID\s*=\s*"(\d+)"', txt, flags=re.M)
old_cid = m.group(1) if m else ""

print("\n================ Dhan TOTP सेटअप ================\n")
if old_cid:
    cid = clean(input(f"1) Client ID [{old_cid}] — बरोबर असेल तर नुसतं Enter दाबा: ")) or old_cid
else:
    cid = clean(input("1) तुमचा Dhan Client ID टाईप करा आणि Enter दाबा: "))
while not cid.isdigit():
    cid = clean(input("   Client ID मध्ये फक्त आकडे हवेत. पुन्हा टाईप करा: "))

pin = clean(getpass.getpass("2) Dhan PIN टाईप करा (स्क्रीनवर दिसणार नाही) आणि Enter दाबा: "))
while not (pin.isdigit() and 4 <= len(pin) <= 6):
    pin = clean(getpass.getpass("   PIN ४ ते ६ आकड्यांचा हवा. पुन्हा टाईप करा: "))

sec = clean(getpass.getpass("3) TOTP Secret Key पेस्ट करा (स्क्रीनवर दिसणार नाही) आणि Enter दाबा: ")).upper()
while not (re.fullmatch(r"[A-Z2-7]+=*", sec) and len(sec) >= 16):
    sec = clean(getpass.getpass(f"   ही Key बरोबर वाटत नाही (लांबी {len(sec)}). फक्त A-Z आणि 2-7 हवेत. पुन्हा पेस्ट करा: ")).upper()

print(f"\n   Secret मिळाली: {len(sec)} अक्षरं | सुरुवात {sec[:2]}…शेवट {sec[-2:]}")
code = totp_now(sec)
print(f"   आत्ताचा TOTP कोड: {code[:3]} {code[3:]}")
print("   👉 Google Authenticator मधला Dhan चा कोड हाच आहे का ते पाहा (३० सेकंदांत बदलतो).\n")

print("4) Dhan कडून नवीन Access Token मागवत आहे...")
try:
    code = totp_now(sec)
    r = requests.post("https://auth.dhan.co/app/generateAccessToken",
                      params={"dhanClientId": cid, "pin": pin, "totp": code}, timeout=20)
    j = r.json() if r.content else {}
except Exception as e:
    j = {"error": str(e)}
d = j.get("data") if isinstance(j.get("data"), dict) else {}
tok = j.get("accessToken") or j.get("access_token") or d.get("accessToken")

if not tok:
    print(f"\n❌ Dhan ने Token दिला नाही. Dhan चं उत्तर: {j}")
    print("   शक्य कारणं: PIN चुकीचा / Secret Key चुकीची / Dhan मध्ये TOTP अजून Confirm झाला नाही.")
    print("   config.py मध्ये काहीही बदललं नाही. तपासून पुन्हा चालवा:  python3 setup_totp.py\n")
    raise SystemExit(1)

txt = set_line(txt, "DHAN_CLIENT_ID", cid)
txt = set_line(txt, "DHAN_ACCESS_TOKEN", tok)
txt = set_line(txt, "DHAN_PIN", pin)
txt = set_line(txt, "DHAN_TOTP_SECRET", sec)
with open(CFG, "w", encoding="utf-8") as f:
    f.write(txt)
os.chmod(CFG, 0o600)
print(f"\n✅ नवीन Token मिळाला आणि config.py मध्ये PIN + Secret + Token सेव्ह झाले. (मुदत: {j.get('expiryTime', '२४ तास')})")

try:
    from dhanhq import DhanContext, dhanhq
    f = dhanhq(DhanContext(cid, tok)).get_fund_limits()
    if isinstance(f, dict) and f.get("status") == "success":
        print("✅ Dhan ने नवीन Token स्वीकारला. आता बॉट रोज स्वतः Token बनवेल.")
        print("   पुढची कमांड:  python3 bot.py\n")
    else:
        print(f"⚠️ Token मिळाला पण तपासणीत अडचण: {f.get('remarks') if isinstance(f, dict) else f}\n")
except Exception as e:
    print(f"⚠️ तपासणी करता आली नाही: {e}\n")
