import json
import urllib.request

url = "https://celobuilders.xyz/auth/google/claim"
payload = {"claimCode": "CELO-LDUEN-M7EXS-TPQUZ-7C8J8-Q38ET-M"}

data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as r:
    print(r.read().decode("utf-8"))
