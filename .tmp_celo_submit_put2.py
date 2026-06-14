import json
import urllib.request
import urllib.error

url = "https://celobuilders.xyz/submissions/me"
api_key = "sk-celo-hackathon_dMH9EdP6IiD7VDuqXGOut72PnQ-poeVwgs0rfDHBRAE"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}",
}
payload = {
    "projectName": "TrustGuard Router",
    "tagline": "A Celo infrastructure agent for trust, escrow routing, x402 payments, and ERC-8004 agent discovery",
    "description": "TrustGuard Router is a decentralized Celo infrastructure agent that verifies ERC-8004 agents, scores reputation, routes payments through onchain escrow, supports x402 micropayments, and enables LLM-powered natural-language task execution.",
    "trackIds": ["best-agent", "most-activity", "8004scan-rank"],
    "bountyIds": ["best-agent-1st", "most-activity-1st", "8004scan-rank-1st"],
    "githubUrl": "https://github.com/Etette/trustguard_router_agent",
    "demoUrl": "https://trustguardrouteragent-production.up.railway.app",
    "socialLink": "https://x.com/i/status/2066286854085779850",
    "celoNetwork": "celo-mainnet",
    "contractAddresses": ["0x2257EF5a3A2e2dE1196af458572fabC865CD3A54"],
    "agentContributionNotes": "The agent helped implement the escrow routing, x402 payment enforcement, ERC-8004 agent discovery, and the LLM-powered natural language task execution flow.",
}

data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
try:
    with urllib.request.urlopen(req) as r:
        print(f"STATUS: {r.status}")
        print(r.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}")
    print(e.read().decode('utf-8'))
except Exception as e:
    print(f"ERROR: {e}")
