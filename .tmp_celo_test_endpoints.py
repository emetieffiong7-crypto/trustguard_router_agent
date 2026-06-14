import json
import urllib.request
import urllib.error

api_key = "sk-celo-hackathon_dMH9EdP6IiD7VDuqXGOut72PnQ-poeVwgs0rfDHBRAE"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}

for path in [
    "https://celobuilders.xyz/participants/me",
    "https://celobuilders.xyz/submissions/me",
    "https://celobuilders.xyz/submissions/me/publish",
]:
    req = urllib.request.Request(path, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            print(f"{path} -> {r.status}")
            print(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print(f"{path} -> HTTP {e.code}")
        try:
            print(e.read().decode('utf-8'))
        except Exception:
            pass
    except Exception as e:
        print(f"{path} -> ERROR {e}")
