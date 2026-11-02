# -*- coding: utf-8 -*-
# Dhan Static IP तपासणे / नोंदवणे (सर्व्हरवरूनच)
#   तपासणे :  python3 ip_tool.py
#   नोंदवणे :  python3 ip_tool.py set
import sys, json, requests
from dhanhq import DhanLogin

ns = {}
exec(open("config.py", encoding="utf-8").read(), ns)
cid, tok = str(ns["DHAN_CLIENT_ID"]), str(ns["DHAN_ACCESS_TOKEN"])
login = DhanLogin(cid)

myip = requests.get("https://checkip.amazonaws.com", timeout=10).text.strip()
print("\nया सर्व्हरचा Public IP :", myip)
res = login.get_ip(tok)
print("Dhan मध्ये सध्या नोंदलेले IP :")
print(json.dumps(res, indent=2, ensure_ascii=False))

if len(sys.argv) > 1 and sys.argv[1].lower() == "set":
    ans = input(f"\n{myip} हा IP Dhan मध्ये PRIMARY म्हणून नोंदवायचा?\n"
                f"(७ दिवस बदलता येणार नाही) होय असेल तर मोठ्या अक्षरात YES टाईप करा: ")
    if ans.strip() == "YES":
        r = login.set_ip(tok, myip, "PRIMARY")
        print("setIP उत्तर:", r)
        if not (isinstance(r, dict) and r.get("status") == "success"):
            print("setIP जमले नाही, modifyIP प्रयत्न करत आहे...")
            print("modifyIP उत्तर:", login.modify_ip(tok, myip, "PRIMARY"))
        print("\nआता web.dhan.co वरून नवीन Access Token बनवा आणि config.py मध्ये टाका.")
    else:
        print("रद्द केले.")
