from typing import Optional
import json

from agent.base import (
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    ToolDefinition,
    ToolCall
)
from config import settings


class GroqProvider(BaseLLMProvider):
    """
    Groq provider. Groq runs open source models (Llama, Mixtral, Gemma)
    with extremely fast inference — useful for high-frequency agent tasks
    where latency matters more than raw capability.

    Groq's API is OpenAI-compatible so the message and tool format
    is nearly identical to the OpenAI provider.

    Recommended models:
        llama-3.3-70b-versatile     ← best capability, good tool use
        llama-3.1-8b-instant        ← fastest, lower cost
        mixtral-8x7b-32768          ← good for longer context tasks
        gemma2-9b-it                ← lightweight option
    """

    # Groq models that support tool use reliably
    TOOL_CAPABLE_MODELS = {
        "llama-3.3-70b-versatile",
        "llama-3.1-70b-versatile",
        "llama3-groq-70b-8192-tool-use-preview",
        "llama3-groq-8b-8192-tool-use-preview",
        "mixtral-8x7b-32768",
    }

    def __init__(self, model: Optional[str] = None):
        self.model = model or "llama-3.3-70b-versatile"
        from groq import AsyncGroq
        self.client = AsyncGroq(api_key=settings.groq_api_key)

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """Groq uses the same tool format as OpenAI."""
        return [
            {
                "type": "function",
                "function": {
                    "name":        t.name,
                    "description": t.description,
                    "parameters":  t.parameters,
                }
            }
            for t in tools
        ]

    def _convert_messages(self, messages: list[LLMMessage]) -> list[dict]:
        """Groq uses the same message format as OpenAI."""
        converted = []
        for msg in messages:
            if msg.role == "tool":
                converted.append({
                    "role":         "tool",
                    "content":      msg.content,
                    "tool_call_id": msg.tool_call_id,
                })
            elif msg.tool_calls:
                converted.append({
                    "role":    "assistant",
                    "content": msg.content or None,
                    "tool_calls": [
                        {
                            "id":   tc.id,
                            "type": "function",
                            "function": {
                                "name":      tc.name,
                                "arguments": json.dumps(tc.parameters),
                            }
                        }
                        for tc in msg.tool_calls
                    ]
                })
            else:
                converted.append({
                    "role":    msg.role,
                    "content": msg.content
                })
        return converted

    async def complete(
        self,
        messages:   list[LLMMessage],
        tools:      list[ToolDefinition],
        system:     Optional[str] = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:

        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(self._convert_messages(messages))

        kwargs = {
            "model":      self.model,
            "max_tokens": max_tokens,
            "messages":   all_messages,
        }

        # Only pass tools if the model supports them
        if tools and self.model in self.TOOL_CAPABLE_MODELS:
            kwargs["tools"]       = self._convert_tools(tools)
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**kwargs)

        choice     = response.choices[0]
        message    = choice.message
        tool_calls = []

        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                try:
                    parameters = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    parameters = {}

                tool_calls.append(ToolCall(
                    id         = tc.id,
                    name       = tc.function.name,
                    parameters = parameters,
                ))

        stop_reason_map = {
            "stop":       "end_turn",
            "tool_calls": "tool_use",
            "length":     "max_tokens",
        }

        return LLMResponse(
            content       = message.content or "",
            tool_calls    = tool_calls,
            stop_reason   = stop_reason_map.get(
                choice.finish_reason, "end_turn"
            ),
            model         = response.model,
            input_tokens  = response.usage.prompt_tokens,
            output_tokens = response.usage.completion_tokens,
        )

    def supports_streaming(self) -> bool:
        return True