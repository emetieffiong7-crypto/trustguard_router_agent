## TrustGuard Router
### Celo Onchain Agents Hackathon
### Infrastructure Agent
---

### Project Description:
TrustGuard is a decentralized infrastructure agent for the Celo agent ecosystem. It acts as a trust layer and intelligent router — verifying agents, scoring their reputation, routing payments through escrow, and enabling natural language task execution via an LLM-powered agentic loop.
It is fully registered on the ERC-8004 Identity Registry (Agent ID: 9268) and participates natively in the agent-to-agent (A2A) economy via the x402 micropayment protocol and the A2A v0.3.0 JSON-RPC standard.

### What it does:

Agent Discovery — indexes and serves ERC-8004 agents from the subgraph, filterable by capability, score, and trust tier
Trust Scoring — computes on-chain reputation scores for agents based on transaction history, verification status, and probe results
Escrow Routing — creates, manages, and releases USDC escrow between agents.
x402 Payments — enforces micropayment requirements on agent task endpoints using the x402 HTTP payment protocol
A2A Protocol — accepts ERC-8004 JSON-RPC messages from other agents at /agent/a2a, making TrustGuard a first-class participant in multi-agent workflows
LLM Agent Loop — exposes /agent/task for natural language task execution, supporting Claude, GPT, and Groq models with tool use
Self-service API Keys — agents and developers can register at /admin/keys/register without a master key


### Tech Stack:

Backend: Python, FastAPI, SQLAlchemy (async), uvicorn
Blockchain: Celo Mainnet, Web3.py, custom TrustGuardRouter.sol
Standards: ERC-8004 Identity Registry, ERC-8004 Reputation Registry, x402, A2A v0.3.0
Subgraph: The Graph (trustguard-subgraph-mainnet)
LLMs: Anthropic Claude, OpenAI, Groq
Infra: Railway (backend), Self Agent ID


Smart Contracts:

- TrustGuardRouter.sol — 0x2257EF5a3A2e2dE1196af458572fabC865CD3A54
- ERC-8004 Identity Registry — 0x8004A169FB4a3325136EB29fA0ceB6D2e539a432
- ERC-8004 Reputation Registry — 0x8004BAa17C55a88189AE136b182e5fdA19dE9b63


Agent Identity:

- ERC-8004 Agent ID: 9268
- Agent Card: https://gist.githubusercontent.com/Etette/2b1fe04c6a13187db31f9ae806487d87/raw/219f0f3fddb74c67bdcc9e4a2a1abc04f93f033e/agent_card.json
- 8004scan: https://8004scan.xyz/agent/9268


Links:

- Live API: https://trustguardrouteragent-production.up.railway.app
- API Docs: https://trustguardrouteragent-production.up.railway.app/docs
- GitHub: https://github.com/Etette/trustguard_router_agent
- Demo: https://trustguard-khaki.vercel.app