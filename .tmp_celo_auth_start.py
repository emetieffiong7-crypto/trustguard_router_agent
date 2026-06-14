import json
import urllib.request

url = "https://celobuilders.xyz/auth/google/start"
payload = {
    "hackathonId": "celo-onchain-agents",
    "human": {
        "name": "Etette Etok",
        "email": "etettetok5@gmail.com",
        "social": "@Tai5Chi",
        "teamName": "EmClickz",
    },
    "agent": {
        "name": "TrustGuard Router submission agent",
        "harness": "codex",
        "model": "gpt-5",
    },
}

data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as r:
    print(r.read().decode("utf-8"))
