import json
import urllib.request
import urllib.error

url = "https://celobuilders.xyz/submissions/me/publish"
api_key = "sk-celo-hackathon_dMH9EdP6IiD7VDuqXGOut72PnQ-poeVwgs0rfDHBRAE"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}",
}
payload = {"confirm": True}

data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(url, data=data, headers=headers, method="POST")
try:
    with urllib.request.urlopen(req) as r:
        print(f"STATUS: {r.status}")
        print(r.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}")
    print(e.read().decode('utf-8'))
except Exception as e:
    print(f"ERROR: {e}")
