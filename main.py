import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from db.base import init_db
from subgraph.client import subgraph_client
from self_id.client import self_id_client

from routes.agent    import router as agent_router
from routes.verify   import router as verify_router
from routes.discovery import router as discover_router
from routes.escrow   import router as escrow_router
from routes.score    import router as score_router
from routes.admin    import router as admin_router
from middleware.auth import RouterAuthMiddleware
from x402.middleware import X402ServerMiddleware

logging.basicConfig(
    level  = logging.DEBUG if settings.debug else logging.INFO,
    format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("trustguard")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("TrustGuard starting up...")
    await init_db()
    logger.info("Database initialised")

    # Run scoring pass in background on startup
    # post_onchain=False on startup to avoid gas on every restart
    import asyncio
    from services.scoring_engine import run_full_scoring_pass

    async def startup_scoring():
        await asyncio.sleep(5)  # wait for all connections to settle
        logger.info("Running startup scoring pass...")
        await run_full_scoring_pass(post_onchain=False)

    asyncio.create_task(startup_scoring())

    yield

    logger.info("TrustGuard shutting down...")
    await subgraph_client.close()
    await self_id_client.close()
    logger.info("Shutdown complete")


app = FastAPI(
    title       = settings.app_name,
    version     = settings.app_version,
    description = (
        "Infrastructure agent for the Celo ERC-8004 ecosystem. "
        "Verification, discovery, escrow routing, trust scoring, "
        "x402 payments, and LLM-powered agentic task execution."
    ),
    lifespan  = lifespan,
    docs_url  = "/docs",
    redoc_url = "/redoc",
)



# Middleware order matters — auth first, then x402
app.add_middleware(X402ServerMiddleware)
app.add_middleware(RouterAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins = ["*"],
    allow_methods = ["*"],
    allow_headers = ["*"],
)

app.include_router(agent_router)
app.include_router(verify_router)
app.include_router(discover_router)
app.include_router(escrow_router)
app.include_router(score_router)
app.include_router(admin_router)

@app.get("/", include_in_schema=False)
async def root():
    return {
        "name": "TrustGuard Router",
        "version": "1.0.0",
        "docs": "/docs",
        "agent_card": "/.well-known/agent.json"
    }