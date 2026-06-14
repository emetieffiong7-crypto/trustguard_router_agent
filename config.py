from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):

    # -------------------------------------------------------------------------
    # App
    # -------------------------------------------------------------------------
    app_name: str    = "TrustGuard Router"
    app_version: str = "1.0.0"
    debug: bool      = True
    port: int        = Field(default=8000, env="PORT")
    api_key: str     = Field(default="dev-key-123", env="TRUSTGUARD_API_KEY")

    # -------------------------------------------------------------------------
    # Database
    # -------------------------------------------------------------------------
    database_url: str = Field(
        default="sqlite+aiosqlite:///./trustguard.db",
        env="DATABASE_URL"
    )

    # -------------------------------------------------------------------------
    # Celo RPC
    # -------------------------------------------------------------------------
    celo_rpc_url: str  = Field(
        default=None,
        env="CELO_RPC_URL"
    )
    celo_chain_id: int = 42220

    # -------------------------------------------------------------------------
    # Backend wallet and contract — required
    # -------------------------------------------------------------------------
    router_private_key:           str = Field(..., env="ROUTER_PRIVATE_KEY")
    trustguard_contract_address:  str = Field(..., env="TRUSTGUARD_CONTRACT_ADDRESS")

    # -------------------------------------------------------------------------
    # Environment switch
    # -------------------------------------------------------------------------
    environment: str = Field(default="mainnet", env="ENVIRONMENT")

    # -------------------------------------------------------------------------
    # Subgraph URLs
    # -------------------------------------------------------------------------
    subgraph_url_sepolia: Optional[str] = Field(default=None, env="SUBGRAPH_URL_SEPOLIA")
    subgraph_url_mainnet: Optional[str] = Field(default=None, env="SUBGRAPH_URL_MAINNET")
    subgraph_url:         Optional[str] = Field(default=None, env="SUBGRAPH_URL")

    @property
    def active_subgraph_url(self) -> Optional[str]:
        if self.environment == "mainnet":
            return self.subgraph_url_mainnet or self.subgraph_url
        return self.subgraph_url_sepolia or self.subgraph_url

    # -------------------------------------------------------------------------
    # ERC-8004 and Self contracts — environment-aware via properties
    # -------------------------------------------------------------------------
    @property
    def erc8004_identity_registry(self) -> str:
        if self.environment == "mainnet":
            return "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
        return "0x8004A818BFB912233c491871b3d84c89A494BD9e"

    @property
    def erc8004_reputation_registry(self) -> str:
        if self.environment == "mainnet":
            return "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63"
        return "0x8004B663056A597Dffe9eCcC1965A193B7388713"

    @property
    def self_registry_address(self) -> str:
        if self.environment == "mainnet":
            return "0xaC3DF9ABf80d0F5c020C06B04Cced27763355944"
        return "0x043DaCac8b0771DD5b444bCC88f2f8BBDBEdd379"

    @property
    def celo_chain_id_active(self) -> int:
        return 42220 if self.environment == "mainnet" else 11142220

    # -------------------------------------------------------------------------
    # Self Agent ID API
    # -------------------------------------------------------------------------
    self_api_base_url:      str          = Field(default=None, env="SELF_API_BASE_URL")
    self_network:           str          = Field(default="celo", env="SELF_NETWORK")
    self_agent_private_key: Optional[str] = Field(default=None, env="SELF_AGENT_PRIVATE_KEY")
    self_agent_public_key:  Optional[str] = Field(default=None, env="SELF_AGENT_PUBLIC_KEY")

    # -------------------------------------------------------------------------
    # Token addresses (Celo Mainnet — same on both environments for payments)
    # -------------------------------------------------------------------------
    usdm_address: str = "0x765DE816845861e75A25fCA122bb6898B8B1282a"
    usdc_address: str = "0xcebA9300f2b948710d2653dD7B07f33A8B32118C"

    # -------------------------------------------------------------------------
    # TrustGuard's own ERC-8004 identity
    # -------------------------------------------------------------------------
    trustguard_agent_id:  Optional[int] = Field(default=None, env="TRUSTGUARD_AGENT_ID")
    trustguard_agent_uri: Optional[str] = Field(default=None, env="TRUSTGUARD_AGENT_URI")

    # -------------------------------------------------------------------------
    # Probe settings
    # -------------------------------------------------------------------------
    probe_timeout_seconds:       int   = 10
    probe_http_timeout:          float = 8.0
    blacklist_failure_threshold: int   = 5

    # -------------------------------------------------------------------------
    # Discovery settings
    # -------------------------------------------------------------------------
    discovery_min_score:   int = 0
    discovery_max_results: int = 20

    # -------------------------------------------------------------------------
    # Fee settings
    # -------------------------------------------------------------------------
    default_fee_bps: int = 50

    # -------------------------------------------------------------------------
    # LLM
    # -------------------------------------------------------------------------
    anthropic_api_key:  Optional[str] = Field(default=None, env="ANTHROPIC_API_KEY")
    openai_api_key:     Optional[str] = Field(default=None, env="OPENAI_API_KEY")
    groq_api_key:       Optional[str] = Field(default=None, env="GROQ_API_KEY")
    default_llm_model:  str           = Field(
        default="claude-sonnet-4-20250514",
        env="DEFAULT_LLM_MODEL"
    )

    # -------------------------------------------------------------------------
    # x402
    # -------------------------------------------------------------------------
    thirdweb_secret_key:   Optional[str] = Field(default=None, env="THIRDWEB_SECRET_KEY")
    x402_price_per_task:   int           = Field(default=1000,     env="X402_PRICE_PER_TASK")
    x402_escrow_threshold: int           = Field(default=10000000, env="X402_ESCROW_THRESHOLD")
    x402_enabled:          bool          = Field(default=True,     env="X402_ENABLED")

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------
    @field_validator(
        "subgraph_url_sepolia",
        "subgraph_url_mainnet",
        "subgraph_url",
        "self_agent_private_key",
        "self_agent_public_key",
        "trustguard_agent_uri",
        "anthropic_api_key",
        "openai_api_key",
        "groq_api_key",
        "thirdweb_secret_key",
        mode="before"
    )
    @classmethod
    def empty_str_to_none_str(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @field_validator("trustguard_agent_id", mode="before")
    @classmethod
    def empty_str_to_none_int(cls, v) -> Optional[int]:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    model_config = {
        "env_file":          ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive":    False,
        "extra":             "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()