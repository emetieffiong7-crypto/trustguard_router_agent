import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from db.base import get_db
from services.agent import AgentLoop
from config import settings

logger = logging.getLogger("trustguard.routes.agent")

router = APIRouter(tags=["Agent"])


# -------------------------------------------------------------------------
# Schemas
# -------------------------------------------------------------------------

class TaskRequest(BaseModel):
    task:  str            = Field(..., description="Natural language instruction for TrustGuard")
    model: Optional[str] = Field(
        default=None,
        description="LLM model to use. Defaults to groq-llama-3.3-70b-versatile. "
                    "Supports: claude-*, gpt-*, o1-*, o3-*, llama-*, llama3-*, mixtral-*, gemma*, whisper-*"
    )
    stream: bool = Field(
        default=False,
        description="Stream reasoning steps as server-sent events"
    )


class A2AMessage(BaseModel):
    """
    A2A protocol v0.3.0 JSON-RPC message format.
    Other ERC-8004 agents send requests in this format.
    """
    jsonrpc: str = "2.0"
    id:      int = 1
    method:  str
    params:  dict


# -------------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------------

@router.get("/.well-known/agent.json", include_in_schema=False)
async def serve_agent_card():
    """Serves TrustGuard's ERC-8004 compliant agent card."""
    import json
    from pathlib import Path
    from fastapi.responses import JSONResponse

    card_path = Path(__file__).parent.parent / "agent_card.json"
    with open(card_path) as f:
        card = json.load(f)
    return JSONResponse(content=card)


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": settings.app_name}


# @router.post("/agent/task")
# async def agent_task(
#     request: TaskRequest,
#     db: AsyncSession = Depends(get_db)
# ):
#     """
#     Submit a natural language task to TrustGuard's LLM agent.
#     TrustGuard will reason about the task and autonomously call
#     the appropriate tools to complete it.

#     Set stream=true to receive reasoning steps as server-sent events.
#     """
#     if not settings.anthropic_api_key and not settings.openai_api_key and not settings.groq_api_key:
#         raise HTTPException(
#             status_code=503,
#             detail="No LLM API key configured. Set LLM key."
#         )

#     loop = AgentLoop(model=request.model, db=db)

#     if request.stream:
#         async def event_stream():
#             async for event in loop.stream(request.task):
#                 yield f"data: {json.dumps(event)}\n\n"

#         return StreamingResponse(
#             event_stream(),
#             media_type="text/event-stream",
#             headers={
#                 "Cache-Control":  "no-cache",
#                 "X-Accel-Buffering": "no",
#             }
#         )

#     result = await loop.run(request.task)
#     return result
@router.post("/agent/task")
async def agent_task(
    request: TaskRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Submit a natural language task to TrustGuard's LLM agent.
    """
    if not settings.anthropic_api_key and not settings.openai_api_key and not settings.groq_api_key:
        raise HTTPException(
            status_code=503,
            detail="No LLM API key configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GROQ_API_KEY."
        )

    # Anonymous callers (public landing page) don't trigger onchain writes —
    # everything else (debug mode, API key, master key, Self agent) does.
    auth_type     = getattr(http_request.state, "auth_type", "trusted")
    allow_onchain = auth_type != "anonymous"

    loop = AgentLoop(model=request.model, db=db, allow_onchain=allow_onchain)

    if request.stream:
        async def event_stream():
            async for event in loop.stream(request.task):
                yield f"data: {json.dumps(event)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control":     "no-cache",
                "X-Accel-Buffering": "no",
            }
        )

    result = await loop.run(request.task)
    return result

# @router.post("/agent/a2a")
# async def agent_a2a(
#     message: A2AMessage,
#     db: AsyncSession = Depends(get_db)
# ):
#     """
#     A2A protocol v0.3.0 endpoint.
#     Other ERC-8004 agents send JSON-RPC messages here.
#     This endpoint is listed in TrustGuard's agent card and
#     is how TrustGuard participates in the agent ecosystem.
#     """
#     if message.method != "message/send":
#         raise HTTPException(
#             status_code=400,
#             detail=f"Unsupported A2A method: {message.method}"
#         )

#     # Extract the task text from the A2A message format
#     parts = message.params.get("message", {}).get("parts", [])
#     task  = ""
#     for part in parts:
#         if part.get("kind") == "text":
#             task += part.get("text", "")

#     if not task:
#         raise HTTPException(status_code=400, detail="No text content in A2A message")

#     # Parse task from JSON if it contains intent
#     try:
#         task_data = json.loads(task)
#         if "intent" in task_data:
#             task = task_data.get("description") or task_data.get("intent")
#     except (json.JSONDecodeError, TypeError):
#         pass

#     if not settings.groq_api_key and not settings.openai_api_key and not settings.anthropic_api_key:
#         return {
#             "jsonrpc": "2.0",
#             "id":      message.id,
#             "result":  {
#                 "status": "error",
#                 "error":  "LLM not configured on this TrustGuard instance"
#             }
#         }

#     loop   = AgentLoop(db=db)
#     result = await loop.run(task)

#     # Return in A2A JSON-RPC response format
#     return {
#         "jsonrpc": "2.0",
#         "id":      message.id,
#         "result":  {
#             "status":   "completed",
#             "response": result["response"],
#             "tool_calls_made": result["tool_calls_made"],
#             "iterations":      result["iterations"],
#         }
#     }

@router.post("/agent/a2a")
async def agent_a2a(
    message: A2AMessage,
    http_request: Request,
    db: AsyncSession = Depends(get_db)
):
    if message.method != "message/send":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported A2A method: {message.method}"
        )

    parts = message.params.get("message", {}).get("parts", [])
    task  = ""
    for part in parts:
        if part.get("kind") == "text":
            task += part.get("text", "")

    if not task:
        raise HTTPException(status_code=400, detail="No text content in A2A message")

    try:
        task_data = json.loads(task)
        if "intent" in task_data:
            task = task_data.get("description") or task_data.get("intent")
    except (json.JSONDecodeError, TypeError):
        pass

    if not settings.anthropic_api_key and not settings.openai_api_key and not settings.groq_api_key:
        return {
            "jsonrpc": "2.0",
            "id":      message.id,
            "result":  {
                "status": "error",
                "error":  "LLM not configured on this TrustGuard instance"
            }
        }

    auth_type     = getattr(http_request.state, "auth_type", "trusted")
    allow_onchain = auth_type != "anonymous"

    loop   = AgentLoop(db=db, allow_onchain=allow_onchain)
    result = await loop.run(task)

    return {
        "jsonrpc": "2.0",
        "id":      message.id,
        "result":  {
            "status":          "completed",
            "response":        result["response"],
            "tool_calls_made": result["tool_calls_made"],
            "iterations":      result["iterations"],
        }
    }

@router.get("/agent/a2a")
async def agent_a2a_info():
    return {
        "protocol": "A2A",
        "version": "0.3.0",
        "description": "TrustGuard A2A endpoint. Send POST requests with JSON-RPC message/send format.",
        "docs": "https://trustguardrouteragent-production.up.railway.app/docs"
    }