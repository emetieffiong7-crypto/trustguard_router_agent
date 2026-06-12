from agent.base import ToolDefinition

TRUSTGUARD_TOOLS = [

    ToolDefinition(
        name        = "verify_agent",
        description = "Probe an agent's A2A and x402 endpoints, check Self verification, post result onchain.",
        parameters  = {
            "type": "object",
            "properties": {
                "agent_address": {
                    "type":        "string",
                    "description": "Wallet address of the agent"
                },
                "agent_id": {
                    "type":        "integer",
                    "description": "ERC-8004 agentId"
                }
            },
            "required": ["agent_address", "agent_id"]
        }
    ),

    ToolDefinition(
        name        = "discover_agents",
        description = "Search ERC-8004 registry for agents by capability, ranked by trust score.",
        parameters  = {
            "type": "object",
            "properties": {
                "capability": {
                    "type":        "string",
                    "description": "a2a, x402, payments, fx, or payroll"
                },
                "min_score": {
                    "type":    "integer",
                    "default": 0
                },
                "self_verified_only": {
                    "type":    "boolean",
                    "default": False
                },
                "limit": {
                    "type":    "integer",
                    "default": 5
                }
            },
            "required": []
        }
    ),

    ToolDefinition(
        name        = "get_agent_score",
        description = "Get trust score, interaction history, Self verification, and blacklist status for an agent.",
        parameters  = {
            "type": "object",
            "properties": {
                "agent_address": {
                    "type": "string"
                }
            },
            "required": ["agent_address"]
        }
    ),

    ToolDefinition(
        name        = "create_escrow",
        description = "Lock tokens in escrow for an agent payment, released on confirmed delivery.",
        parameters  = {
            "type": "object",
            "properties": {
                "payee_agent_id": {
                    "type":        "integer",
                    "description": "ERC-8004 agentId of payee"
                },
                "token": {
                    "type":        "string",
                    "description": "USDm: 0x765DE816845861e75A25fCA122bb6898B8B1282a or USDC: 0xcebA9300f2b948710d2653dD7B07f33A8B32118C"
                },
                "amount_wei": {
                    "type":        "string",
                    "description": "Amount in wei. 1 USDm = 1000000000000000000, 1 USDC = 1000000"
                },
                "timeout_seconds": {
                    "type":    "integer",
                    "default": 86400
                },
                "condition": {
                    "type":        "string",
                    "description": "What must be delivered to release payment"
                }
            },
            "required": ["payee_agent_id", "token", "amount_wei", "condition"]
        }
    ),

    ToolDefinition(
        name        = "check_escrow_status",
        description = "Check state of an escrow: ACTIVE, RELEASED, REFUNDED, or DISPUTED.",
        parameters  = {
            "type": "object",
            "properties": {
                "escrow_id": {
                    "type": "string"
                }
            },
            "required": ["escrow_id"]
        }
    ),

    ToolDefinition(
        name        = "release_escrow",
        description = "Release escrowed funds to payee after confirming service delivery.",
        parameters  = {
            "type": "object",
            "properties": {
                "escrow_id": {
                    "type": "string"
                },
                "completion_proof": {
                    "type":        "string",
                    "description": "Proof string matching the condition hash set at creation"
                }
            },
            "required": ["escrow_id", "completion_proof"]
        }
    ),

    ToolDefinition(
        name        = "execute_x402_payment",
        description = "Make a direct x402 micropayment to an agent endpoint for small low-risk amounts.",
        parameters  = {
            "type": "object",
            "properties": {
                "endpoint_url": {
                    "type": "string"
                },
                "method": {
                    "type":    "string",
                    "default": "POST"
                },
                "body": {
                    "type": "string"
                },
                "max_amount": {
                    "type": "integer"
                }
            },
            "required": ["endpoint_url"]
        }
    ),
    ToolDefinition(
        name        = "get_agent_profile",
        description = (
            "Get a complete intelligence profile for a specific agent by "
            "address or agentId. Returns identity, Self verification, "
            "reputation, endpoints, trust score breakdown, and risk level. "
            "Use this when asked about a specific agent by address or agentID."
        ),
        parameters  = {
            "type": "object",
            "properties": {
                "address": {
                    "type":        "string",
                    "description": "Agent wallet or owner address (0x...)"
                },
                "agent_id": {
                    "type":        "integer",
                    "description": "ERC-8004 agentId (token ID)"
                }
            },
            "required": []
        }
    ),
]