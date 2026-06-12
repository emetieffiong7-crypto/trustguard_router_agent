import json
import os
from pathlib import Path
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from eth_account import Account
from config import settings


def _load_abi(filename: str) -> list:
    abi_path = Path(__file__).parent.parent / "contracts" / filename
    with open(abi_path) as f:
        return json.load(f)


# Minimal ABIs for external contracts we only read from.
# We only include the functions we actually call.

IDENTITY_REGISTRY_ABI = [
    {
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "name": "ownerOf",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "name": "getAgentWallet",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "name": "tokenURI",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "agentId", "type": "uint256"}, {"name": "addr", "type": "address"}],
        "name": "isAuthorizedOrOwner",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "agentURI", "type": "string"}
        ],
        "name": "register",
        "outputs": [
            {"name": "agentId", "type": "uint256"}
        ],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "index", "type": "uint256"}],
        "name": "tokenByIndex",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

REPUTATION_REGISTRY_ABI = [
    {
        "inputs": [
            {"name": "agentId", "type": "uint256"},
            {"name": "clientAddresses", "type": "address[]"}
        ],
        "name": "getSummary",
        "outputs": [
            {"name": "count",    "type": "uint256"},
            {"name": "sum",      "type": "int256"},
            {"name": "decimals", "type": "uint8"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "name": "getClients",
        "outputs": [{"name": "", "type": "address[]"}],
        "stateMutability": "view",
        "type": "function"
    }
]

SELF_REGISTRY_ABI = [
    {
        "inputs": [{"name": "agentKey", "type": "bytes32"}],
        "name": "isVerifiedAgent",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "agentKey", "type": "bytes32"}],
        "name": "getAgentId",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "name": "isProofFresh",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "agentId", "type": "uint256"}],
        "name": "proofExpiresAt",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]


class Web3Client:
    """
    Single web3 client instance shared across the application.
    Wraps all contract instances and the signing account.
    """

    def __init__(self):
        self.w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(settings.celo_rpc_url))

        # Celo is a POA chain — this middleware handles the extra data field
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        # Backend signing account
        self.account = Account.from_key(settings.router_private_key)
        self.router_address = self.account.address

        # Contract instances
        self.trustguard = self.w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(settings.trustguard_contract_address),
            abi=list(_load_abi("trustguard_abi.json"))
        )

        self.identity_registry = self.w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(settings.erc8004_identity_registry),
            abi=IDENTITY_REGISTRY_ABI
        )
        self.reputation_registry = self.w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(settings.erc8004_reputation_registry),
            abi=REPUTATION_REGISTRY_ABI
        )
        self.self_registry = self.w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(settings.self_registry_address),
            abi=SELF_REGISTRY_ABI
        )

    async def get_nonce(self) -> int:
        return await self.w3.eth.get_transaction_count(self.router_address)

    async def get_gas_price(self) -> int:
        return await self.w3.eth.gas_price

    async def send_transaction(self, tx: dict) -> str:
        """Sign and broadcast a transaction. Returns the tx hash as a hex string."""
        signed = self.account.sign_transaction(tx)
        tx_hash = await self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()

    async def wait_for_receipt(self, tx_hash: str, timeout: int = 120) -> dict:
        receipt = await self.w3.eth.wait_for_transaction_receipt(
            tx_hash, timeout=timeout
        )
        return dict(receipt)


# Single importable instance
web3_client = Web3Client()