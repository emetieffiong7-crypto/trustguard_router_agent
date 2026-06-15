import json
import logging
from typing import Optional, AsyncGenerator
from datetime import datetime
from fastapi import params
from sqlalchemy.ext.asyncio import AsyncSession
import re
from agent.base import LLMMessage, ToolCall, LLMResponse
from agent.router import get_llm_provider
from agent.tools import TRUSTGUARD_TOOLS
from config import settings

logger = logging.getLogger("trustguard.agent")

# -------------------------------------------------------------------------
# Shortened system prompt — ~80 tokens vs original ~200
# -------------------------------------------------------------------------

SYSTEM_PROMPT = """You are TrustGuard, a Celo infrastructure agent.
Help people find, check, and safely pay agents in the Celo ecosystem.

CRITICAL: You MUST call a tool before answering ANY question about agents.
Never answer from memory. If someone asks about agent 9268 etc, call get_agent_profile
with agent_id=9268 immediately. Never ask for more information first.

Rules:
- Only talk about agents and data your tools actually returned. Never invent
  agent names, hashes, block numbers, or addresses.
- Never use placeholder values like "0x..." as an address. If you don't have
  a real address from a previous tool result, don't call a tool that needs one.
- Be decisive: once a tool gives you useful results, use them. Don't repeat
  the same tool with similar filters — pick the best result and answer.
- Verify unknown agents before suggesting payment.
- Use escrow for amounts above the threshold or for low-trust agents.
- Use x402 only for small amounts to trusted agents.
- Keep your written answer short and conversational — 2 to 4 sentences.
  The agent's ID, score, address, and other raw details are shown to the
  user separately in a card, so don't repeat long numbers, addresses, or
  hashes in your text. Focus on what the data means and what you'd suggest."""

# -------------------------------------------------------------------------
# Tool selection — send only relevant tools per task
# -------------------------------------------------------------------------

def _select_tools_for_task(task: str) -> list:
    task_lower = task.lower()
    selected   = set()

    # Always include discover as baseline
    selected.add("discover_agents")

    # Named agent search — always use search_agents first
    # Detects: quoted names, "agent named X", "find X agent", known patterns
    has_agent_id = bool(re.search(r'\bagent\s+\d+\b|\b\d{4,}\b', task_lower))
    if has_agent_id:
        selected.add("get_agent_profile")
        selected.add("search_agents")

    has_name_search = any(w in task_lower for w in [
        "named", "called", "find agent", "search for",
        "agent with name", "look for", "where is"
    ]) or (
        # Has a proper noun (capitalized word that is not a verb starter)
        any(
            word[0].isupper() and word.lower() not in [
                "find", "get", "show", "list", "discover",
                "tell", "what", "who", "can", "how"
            ]
            for word in task.split()
            if len(word) > 2
        )
    )

    if has_name_search:
        selected.add("search_agents")
        selected.add("get_agent_profile")

    # Single agent lookup
    if any(w in task_lower for w in [
        "about agent", "tell me about", "who is",
        "agent 0x", "agentid", "agent id", "address 0x",
        "profile", "details about", "information about"
    ]):
        selected.add("get_agent_profile")
        selected.add("search_agents")

    # Verification intent
    if any(w in task_lower for w in [
        "verify", "check", "probe", "test", "endpoint",
        "working", "reachable", "valid", "confirm"
    ]):
        selected.add("verify_agent")
        selected.add("get_agent_profile")

    # Score and trust intent
    if any(w in task_lower for w in [
        "score", "trust", "reputation", "history",
        "reliable", "safe", "risk", "blacklist"
    ]):
        selected.add("get_agent_score")
        selected.add("get_agent_profile")

    # Payment intent
    # if any(w in task_lower for w in [
    #     "pay", "escrow", "transfer", "send", "fund",
    #     "payment", "settle", "release", "refund", "lock"
    # ]):
    payment_phrases = [
        "pay for", "pay them", "pay this", "pay that", "pay agent",
        "make a payment", "send a payment", "send payment",
        "create an escrow", "create escrow", "set up escrow",
        "release escrow", "release the payment", "refund",
        "lock funds", "settle the payment", "settle payment",
        "transfer funds", "transfer money",
    ]
    if any(p in task_lower for p in payment_phrases):
        selected.add("create_escrow")
        selected.add("release_escrow")
        selected.add("check_escrow_status")
        selected.add("get_agent_profile")

    # x402 intent
    if any(w in task_lower for w in [
        "x402", "micropay", "micro", "small payment"
    ]):
        selected.add("execute_x402_payment")

    # Ambiguous task — send everything
    if len(selected) <= 1:
        return TRUSTGUARD_TOOLS

    name_to_tool = {t.name: t for t in TRUSTGUARD_TOOLS}
    return [name_to_tool[name] for name in selected if name in name_to_tool]
# -------------------------------------------------------------------------
# Model selection — cheaper model for simple tasks
# -------------------------------------------------------------------------

def _select_model_for_task(
    task: str,
    requested_model: Optional[str]
) -> str:
    """
    If no model was explicitly requested, use the cheapest model
    that can handle the task complexity.

    Complex multi-step tasks (pay, escrow, route) get the full model.
    Simple queries (find, list, what is) get the fast cheap model.
    """
    if requested_model:
        return requested_model

    task_lower = task.lower()

    is_complex = any(w in task_lower for w in [
        "pay", "escrow", "transfer", "route", "negotiate",
        "verify then", "find and pay", "create escrow",
        "release", "refund", "dispute"
    ])

    if is_complex:
        return settings.default_llm_model

    # Simple queries — use fastest available model
    # Falls back gracefully if groq is not configured
    if settings.groq_api_key:
        return "llama-3.1-8b-instant"

    return settings.default_llm_model


# -------------------------------------------------------------------------
# Message truncation — keep context window small
# -------------------------------------------------------------------------

def _truncate_messages(
    messages: list,
    max_messages: int = 6
) -> list:
    """
    Keep only the most recent messages to control context window size.
    Always preserves the original user message so the LLM remembers
    what the task is even in later iterations.

    With max_messages=6 and typical 2-3 tool calls per task, this
    covers the full conversation history for most tasks while capping
    token growth on longer flows.
    """
    if len(messages) <= max_messages:
        return messages

    first_message = messages[0]
    recent        = messages[-(max_messages - 1):]

    # Avoid duplicating if first message already appears in recent slice
    if recent and recent[0].role == first_message.role and \
       recent[0].content == first_message.content:
        return recent

    return [first_message] + recent


# -------------------------------------------------------------------------
# Agent loop
# -------------------------------------------------------------------------

class AgentLoop:
    """
    The core agent loop. Receives a task, calls the LLM with relevant
    tools, executes tool calls, feeds results back, and loops until
    the LLM returns a final answer with no more tool calls.

    Optimisations applied:
    - Dynamic tool selection reduces tool token overhead 50-70%
    - Message truncation caps context window growth
    - Shortened system prompt saves ~120 tokens per request
    - Automatic model selection routes simple tasks to cheap/fast models
    - Groq 400 error recovery falls back to text-only response
    - max_tokens reduced to 1024 from 2048 (sufficient for most responses)
    """

    MAX_ITERATIONS = 8  # reduced from 10 — most tasks complete in 3-4

    def __init__(
        self,
        model: Optional[str] = None,
        db:    Optional[AsyncSession] = None,
        allow_onchain: bool = True,
    ):
        self.requested_model = model
        self.db              = db
        self.allow_onchain   = allow_onchain
        self.messages: list[LLMMessage] = []
        self.iterations      = 0
        self.tool_calls_made: list[dict] = []
        self._resolved_model: Optional[str] = None
        self._provider       = None

    def _get_provider(self, task: str):
        """Lazy provider initialisation after model is resolved."""
        if self._provider is None:
            self._resolved_model = _select_model_for_task(
                task, self.requested_model
            )
            self._provider = get_llm_provider(self._resolved_model)
        return self._provider

    async def _call_llm(
        self,
        task:  str,
        tools: list
    ) -> LLMResponse:
        """
        Call the LLM with error recovery.
        On Groq tool_use_failed errors, retries without tools
        so the model can respond in plain text rather than crashing.
        """
        provider = self._get_provider(task)

        try:
            return await provider.complete(
                messages   = _truncate_messages(self.messages),
                tools      = tools,
                system     = SYSTEM_PROMPT,
                max_tokens = 1024,
            )

        except Exception as e:
            error_str = str(e).lower()

            # Groq-specific: malformed tool call or tool_use_failed
            is_groq_tool_error = (
                "tool_use_failed" in error_str or
                ("400" in error_str and "failed_generation" in error_str) or
                ("bad request" in error_str and tools)
            )

            if is_groq_tool_error:
                logger.warning(
                    f"LLM tool call failed ({e.__class__.__name__}). "
                    f"Retrying without tools for text response."
                )
                # Add a hint so the LLM understands why tools are unavailable
                recovery_messages = _truncate_messages(self.messages) + [
                    LLMMessage(
                        role    = "user",
                        content = (
                            "The previous tool call could not be parsed. "
                            "Please summarise what you found so far and "
                            "what the result is based on available information."
                        )
                    )
                ]
                return await provider.complete(
                    messages   = recovery_messages,
                    tools      = [],       # no tools — force text response
                    system     = SYSTEM_PROMPT,
                    max_tokens = 512,
                )

            # Any other error — re-raise so the route handler catches it
            raise
    # async def _force_synthesis(self, task: str) -> str:
    #     """
    #     Called when the loop ends without a real text answer — either the
    #     LLM hit max iterations or returned empty content. Makes one final
    #     call with no tools, grounded strictly in what was already found,
    #     so the user never sees a blank or hallucinated response.
    #     """
    #     if not self.tool_calls_made:
    #         return (
    #             "I couldn't find anything useful for that. Try rephrasing, "
    #             "or ask about a specific agent by name or ID."
    #         )

    #     findings = []
    #     for tc in self.tool_calls_made:
    #         result_str = json.dumps(tc["result"], default=str)
    #         findings.append(f"- {tc['tool']}: {result_str[:500]}")

    #     synthesis_prompt = (
    #         f"The user asked: {task}\n\n"
    #         "Here is everything that was found:\n"
    #         + "\n".join(findings) +
    #         "\n\nWrite a short, direct answer using only the information above. "
    #         "Do not invent agents, names, addresses, hashes, or numbers that "
    #         "are not shown above. If nothing useful was found, say so plainly "
    #         "and suggest what to try next."
    #     )

    #     try:
    #         provider = self._get_provider(task)
    #         response = await provider.complete(
    #             messages   = [LLMMessage(role="user", content=synthesis_prompt)],
    #             tools      = [],
    #             system     = SYSTEM_PROMPT,
    #             max_tokens = 500,
    #         )
    #         return response.content.strip() or (
    #             "I found some information but couldn't summarise it. Please try again."
    #         )
    #     except Exception as e:
    #         logger.warning(f"Synthesis pass failed: {e}")
    #         return "I found some information but ran into an issue summarising it. Please try again."
    async def _force_synthesis(self, task: str) -> str:
        """
        Fallback only — called when the loop ended without any real text
        answer (empty content or max iterations with nothing). Makes one
        final call with no tools, grounded strictly in what was found, so
        the user never sees a blank or hallucinated response.
        """
        if not self.tool_calls_made:
            return (
                "I couldn't find anything useful for that. Try rephrasing, "
                "or ask about a specific agent by name or ID."
            )

        findings = []
        for tc in self.tool_calls_made:
            result_str = json.dumps(tc["result"], default=str)
            findings.append(f"- {tc['tool']} returned: {result_str[:600]}")

        synthesis_prompt = (
            f"The user asked: {task}\n\n"
            "Here is the data your tools returned:\n"
            + "\n".join(findings) +
            "\n\nWrite a short, natural 2-4 sentence answer based only on this "
            "data. The exact IDs, scores, and addresses will be shown to the "
            "user separately in a card, so focus on what it means and what "
            "you'd suggest — don't repeat long addresses or hashes. If nothing "
            "useful was found, say so plainly and suggest what to try next. "
            "Never invent agents or values not present above."
        )

        try:
            provider = self._get_provider(task)
            response = await provider.complete(
                messages   = [LLMMessage(role="user", content=synthesis_prompt)],
                tools      = [],
                system     = SYSTEM_PROMPT,
                max_tokens = 400,
            )
            return response.content.strip() or (
                "I found some information but couldn't summarise it. Please try again."
            )
        except Exception as e:
            logger.warning(f"Synthesis pass failed: {e}")
            return "I found some information but ran into an issue summarising it. Please try again."
        
    async def run(self, task: str) -> dict:
        """
        Run the agent loop synchronously and return the final result.
        Used for standard POST /agent/task requests.
        """
        self.messages.append(LLMMessage(role="user", content=task))
        selected_tools = _select_tools_for_task(task)

        logger.info(
            f"Starting agent loop | task='{task[:60]}...' | "
            f"tools={[t.name for t in selected_tools]}"
        )

        while self.iterations < self.MAX_ITERATIONS:
            self.iterations += 1
            logger.info(f"Agent iteration {self.iterations}/{self.MAX_ITERATIONS}")

            response = await self._call_llm(task, selected_tools)

            self.messages.append(LLMMessage(
                role       = "assistant",
                content    = response.content,
                tool_calls = response.tool_calls,
            ))

            logger.info(
                f"LLM response | stop_reason={response.stop_reason} | "
                f"tool_calls={len(response.tool_calls)} | "
                f"tokens_in={response.input_tokens} out={response.output_tokens}"
            )

            # No tool calls — LLM is done reasoning
            if not response.tool_calls:
                logger.info("Agent loop complete")
                break

            # Execute each tool call sequentially
            for tool_call in response.tool_calls:
                logger.info(
                    f"Tool call: {tool_call.name} | "
                    f"params={json.dumps(tool_call.parameters)[:100]}"
                )

                result = await self._execute_tool(tool_call)

                self.tool_calls_made.append({
                    "tool":        tool_call.name,
                    "parameters":  tool_call.parameters,
                    "result":      result,
                    "timestamp":   datetime.utcnow().isoformat(),
                })

                # Feed result back — LLM sees this on next iteration
                self.messages.append(LLMMessage(
                    role         = "tool",
                    content      = json.dumps(result)
                                   if isinstance(result, dict) else str(result),
                    tool_call_id = tool_call.id,
                ))

        final_response = ""
        for msg in reversed(self.messages):
            if msg.role == "assistant" and msg.content:
                final_response = msg.content
                break

        if not final_response.strip():
            logger.info("No grounded answer produced — running synthesis pass")
            final_response = await self._force_synthesis(task)

        return {
            "response":        final_response,
            "tool_calls_made": self.tool_calls_made,
            "iterations":      self.iterations,
            "model":           self._resolved_model or settings.default_llm_model,
            "tools_used":      list({tc["tool"] for tc in self.tool_calls_made}),
        }
    
    async def _tool_search_agents(self, params: dict) -> dict:
        from services.discovery import search_agents_by_name

        result = await search_agents_by_name(
            query = params["query"],
            limit = params.get("limit", 5),
            db    = self.db,
        )

        if not result.results:
            return {
                "found":   False,
                "total":   0,
                "message": f"No agents found matching '{params['query']}'. "
                        f"Try discover_agents with a capability filter instead.",
            }

        return {
            "found":   True,
            "total":   result.total,
            "results": [
                {
                    "agent_id":      a.agent_id,
                    "name":          a.name,
                    "description":   a.description,
                    "trust_score":   a.trust_score,
                    "self_verified": a.self_verified,
                    "a2a_endpoint":  a.a2a_endpoint,
                    "supports_x402": a.supports_x402,
                    "owner_address": a.owner_address,
                }
                for a in result.results
            ]
        }

    async def stream(self, task: str) -> AsyncGenerator[dict, None]:
        """
        Run the agent loop and yield server-sent events as they happen.
        Allows callers to watch reasoning steps in real time.
        """
        self.messages.append(LLMMessage(role="user", content=task))
        selected_tools = _select_tools_for_task(task)

        yield {
            "type":         "start",
            "task":         task,
            "tools_loaded": [t.name for t in selected_tools],
        }

        while self.iterations < self.MAX_ITERATIONS:
            self.iterations += 1

            yield {
                "type":      "thinking",
                "iteration": self.iterations,
                "max":       self.MAX_ITERATIONS,
            }

            try:
                response = await self._call_llm(task, selected_tools)
            except Exception as e:
                yield {
                    "type":  "error",
                    "error": str(e),
                }
                return

            self.messages.append(LLMMessage(
                role       = "assistant",
                content    = response.content,
                tool_calls = response.tool_calls,
            ))

            if response.content:
                yield {
                    "type":    "reasoning",
                    "content": response.content,
                    "tokens":  {
                        "input":  response.input_tokens,
                        "output": response.output_tokens,
                    }
                }

            # No tool calls — final answer reached
          
            if not response.tool_calls:
                final = response.content.strip()
                if not final:
                    yield {"type": "reasoning", "content": "Pulling together what I found..."}
                    final = await self._force_synthesis(task)

                yield {
                    "type":            "complete",
                    "response":        final,
                    "tool_calls_made": self.tool_calls_made,
                    "iterations":      self.iterations,
                    "model":           self._resolved_model or settings.default_llm_model,
                    "tools_used":      list({tc["tool"] for tc in self.tool_calls_made}),
                }
                return

            # Execute tool calls and stream each step
            for tool_call in response.tool_calls:
                yield {
                    "type":       "tool_call",
                    "tool":       tool_call.name,
                    "parameters": tool_call.parameters,
                }

                result = await self._execute_tool(tool_call)

                self.tool_calls_made.append({
                    "tool":       tool_call.name,
                    "parameters": tool_call.parameters,
                    "result":     result,
                    "timestamp":  datetime.utcnow().isoformat(),
                })

                yield {
                    "type":   "tool_result",
                    "tool":   tool_call.name,
                    "result": result,
                }

                self.messages.append(LLMMessage(
                    role         = "tool",
                    content      = json.dumps(result)
                                   if isinstance(result, dict) else str(result),
                    tool_call_id = tool_call.id,
                ))

        # Max iterations reached
    
        final_response = ""
        for msg in reversed(self.messages):
            if msg.role == "assistant" and msg.content:
                final_response = msg.content
                break
        
        if not final_response.strip():
            yield {"type": "reasoning", "content": "Pulling together what I found..."}
            final_response = await self._force_synthesis(task)

        yield {
            "type":            "complete",
            "response":        final_response,
            "tool_calls_made": self.tool_calls_made,
            "iterations":      self.iterations,
            "model":           self._resolved_model or settings.default_llm_model,
            "tools_used":      list({tc["tool"] for tc in self.tool_calls_made}),
        }

    # -------------------------------------------------------------------------
    # Tool execution router
    # -------------------------------------------------------------------------

    async def _execute_tool(self, tool_call: ToolCall) -> dict:
        """
        Route a tool call to the correct service function.
        All results returned as dicts for the LLM to reason about.
        Errors are caught and returned as structured error dicts so
        the LLM can reason about failures rather than crashing.
        """
        params = tool_call.parameters

        try:
            if tool_call.name == "verify_agent":
                return await self._tool_verify_agent(params)

            if tool_call.name == "discover_agents":
                return await self._tool_discover_agents(params)

            if tool_call.name == "get_agent_score":
                return await self._tool_get_agent_score(params)

            if tool_call.name == "create_escrow":
                return await self._tool_create_escrow(params)

            if tool_call.name == "check_escrow_status":
                return await self._tool_check_escrow_status(params)

            if tool_call.name == "release_escrow":
                return await self._tool_release_escrow(params)

            if tool_call.name == "execute_x402_payment":
                return await self._tool_execute_x402_payment(params)
            if tool_call.name == "get_agent_profile":
                return await self._tool_get_agent_profile(params)
            if tool_call.name == "search_agents":
                return await self._tool_search_agents(params)

            return {
                "error": f"Unknown tool: {tool_call.name}",
                "available_tools": [t.name for t in TRUSTGUARD_TOOLS]
            }

        except Exception as e:
            logger.error(
                f"Tool {tool_call.name} failed: {e}",
                exc_info=True
            )
            return {
                "error":   str(e),
                "tool":    tool_call.name,
                "status":  "failed",
                "message": "Tool execution failed. Consider trying a different approach."
            }

    # -------------------------------------------------------------------------
    # Individual tool implementations
    # -------------------------------------------------------------------------

    async def _tool_verify_agent(self, params: dict) -> dict:
        from services.verifier import probe_agent
        result = await probe_agent(
            agent_address = params["agent_address"],
            agent_id      = params.get("agent_id", 0),
            db            = self.db,
            post_onchain  = self.allow_onchain,
        )
        return {
            "overall_passed":    result.overall_passed,
            "card_reachable":    result.card_reachable,
            "a2a_passed":        result.a2a_passed,
            "x402_passed":       result.x402_passed,
            "self_verified":     result.self_verified,
            "self_proof_fresh":  result.self_proof_fresh,
            "trust_score":       result.trust_score,
            "evidence":          result.evidence,
            "tx_hash":           result.tx_hash,
        }
    
    async def _tool_get_agent_profile(self, params: dict) -> dict:
        from services.discovery import get_agent_profile

        address  = params.get("address")
        agent_id = params.get("agent_id")

        if not address and agent_id is None:
            return {
                "error": "Provide either address or agent_id",
                "status": "failed"
            }

        profile = await get_agent_profile(
            address  = address,
            agent_id = agent_id,
            db       = self.db,
        )

        if profile is None:
            return {
                "found":   False,
                "message": f"Agent not found for "
                        f"{'address ' + address if address else 'agentId ' + str(agent_id)}",
            }

        # Return a concise summary for the LLM — full profile is too verbose
        # The LLM reasons better with structured summaries than raw dumps
        return {
            "found":          True,
            "agent_id":       profile["agent_id"],
            "owner_address":  profile["owner_address"],
            "name":           profile["name"],
            "description":    profile["description"],
            "trust_score":    profile["trust_score"],
            "risk_level":     profile["risk_level"],
            "is_blacklisted": profile["is_blacklisted"],
            "self_verified":  profile["self_verification"]["verified"],
            "self_proof_fresh": profile["self_verification"]["proof_fresh"],
            "verification_strength": profile["self_verification"]["verification_strength"],
            "a2a_endpoint":   profile["a2a_endpoint"],
            "supports_x402":  profile["supports_x402"],
            "reputation": {
                "total_feedback": profile["reputation"]["total_feedback"],
                "avg_score":      profile["reputation"]["avg_score"],
            },
            "score_breakdown":      profile["score_breakdown"],
            "consecutive_failures": profile["trustguard_metadata"]["consecutive_failures"],
            "last_probed_at":       profile["trustguard_metadata"]["last_probed_at"],
            "recommendation": (
                "SAFE TO USE — high trust, Self verified"
                if profile["trust_score"] >= 70 and profile["self_verification"]["verified"]
                else "PROCEED WITH CAUTION — verify before large payments"
                if profile["trust_score"] >= 40
                else "HIGH RISK — use escrow and verify first"
                if profile["trust_score"] >= 20
                else "UNKNOWN AGENT — no trust signals found"
            )
        }
    
    async def _tool_discover_agents(self, params: dict) -> dict:
        from services.discovery import discover_agents
        result = await discover_agents(
            capability         = params.get("capability"),
            min_score          = params.get("min_score", 0),
            self_verified_only = params.get("self_verified_only", False),
            limit              = params.get("limit", 5),
            db                 = self.db,
        )

        # Return a concise summary to keep tool result tokens low
        return {
            "total":   result.total,
            "results": [
                {
                    "agent_id":      a.agent_id,
                    "name":          a.name or f"Agent #{a.agent_id}",
                    "description":   a.description,
                    "trust_score":   a.trust_score,
                    "self_verified": a.self_verified,
                    "supports_x402": a.supports_x402,
                    "a2a_endpoint":  a.a2a_endpoint,
                    "success_rate":  a.success_rate,
                }
                for a in result.results
            ],
            # Guidance for LLM when no results found
            "note": (
                "No agents found. The subgraph may not be indexed yet "
                "or no agents match this capability filter."
                if result.total == 0 else None
            )
        }

    async def _tool_get_agent_score(self, params: dict) -> dict:
        from services.scorer import get_agent_score
        result = await get_agent_score(
            agent_address = params["agent_address"],
            db            = self.db,
        )
        return {
            "trust_score":            result.trust_score,
            "total_interactions":     result.total_interactions,
            "successful_settlements": result.successful_settlements,
            "failed_verifications":   result.failed_verifications,
            "disputes_raised":        result.disputes_raised,
            "is_blacklisted":         result.is_blacklisted,
            "self_verified":          result.self_verified,
            "self_proof_fresh":       result.self_proof_fresh,
            "risk_level": (
                "HIGH"   if result.is_blacklisted else
                "MEDIUM" if result.trust_score < 50 else
                "LOW"
            )
        }

    async def _tool_create_escrow(self, params: dict) -> dict:
        from services.router import create_escrow
        from schemas.escrow import EscrowCreateRequest
        from onchain.client import web3_client

        request = EscrowCreateRequest(
            payee_agent_id  = params["payee_agent_id"],
            token           = params["token"],
            amount_wei      = params["amount_wei"],
            timeout_seconds = params.get("timeout_seconds", 86400),
            condition       = params["condition"],
        )
        result = await create_escrow(
            request       = request,
            payer_address = web3_client.router_address,
            db            = self.db,
        )
        return {
            "escrow_id":      result.escrow_id,
            "payee":          result.payee,
            "amount_wei":     result.amount_wei,
            "fee_bps":        result.fee_bps,
            "timeout_at":     result.timeout_at.isoformat(),
            "condition_hash": result.condition_hash,
            "tx_hash":        result.tx_hash,
            "state":          result.state,
            "status":         "created"
        }

    async def _tool_check_escrow_status(self, params: dict) -> dict:
        from services.router import get_escrow_status
        result = await get_escrow_status(
            escrow_id = params["escrow_id"],
            db        = self.db,
        )
        return {
            "escrow_id":  result.escrow_id,
            "state":      result.state,
            "payer":      result.payer,
            "payee":      result.payee,
            "amount_wei": result.amount_wei,
            "timeout_at": result.timeout_at.isoformat(),
        }

    async def _tool_release_escrow(self, params: dict) -> dict:
        from services.router import release_escrow
        from schemas.escrow import EscrowReleaseRequest

        request = EscrowReleaseRequest(
            escrow_id        = params["escrow_id"],
            completion_proof = params["completion_proof"],
        )
        result = await release_escrow(request=request, db=self.db)
        return {**result, "status": "released"}

    async def _tool_execute_x402_payment(self, params: dict) -> dict:
        import httpx
        from x402.client import x402_client

        endpoint_url = params["endpoint_url"]
        method       = params.get("method", "POST")
        body         = params.get("body", "{}")

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await x402_client.fetch_with_payment(
                    client  = client,
                    method  = method,
                    url     = endpoint_url,
                    content = body,
                    headers = {"Content-Type": "application/json"},
                )
                return {
                    "status_code": response.status_code,
                    "success":     response.status_code < 400,
                    "response":    response.text[:300],  # truncate for token efficiency
                    "status":      "paid_and_delivered" if response.status_code < 400
                                   else "payment_failed"
                }
            except Exception as e:
                return {
                    "error":   str(e),
                    "success": False,
                    "status":  "failed"
                }