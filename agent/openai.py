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


class OpenAIProvider(BaseLLMProvider):
    """
    OpenAI provider.
    Converts TrustGuard's generic tool format to OpenAI's function calling format.
    """

    def __init__(self, model: Optional[str] = None):
        self.model = model or "gpt-4o"
        # Import lazily so OpenAI package is optional
        import openai
        self.client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """Convert generic ToolDefinition list to OpenAI function format."""
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
        """Convert generic LLMMessage list to OpenAI message format."""
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

        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        response = await self.client.chat.completions.create(**kwargs)

        choice     = response.choices[0]
        message    = choice.message
        tool_calls = []

        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(ToolCall(
                    id         = tc.id,
                    name       = tc.function.name,
                    parameters = json.loads(tc.function.arguments),
                ))

        stop_reason_map = {
            "stop":         "end_turn",
            "tool_calls":   "tool_use",
            "length":       "max_tokens",
            "content_filter": "end_turn",
        }

        return LLMResponse(
            content       = message.content or "",
            tool_calls    = tool_calls,
            stop_reason   = stop_reason_map.get(choice.finish_reason, "end_turn"),
            model         = response.model,
            input_tokens  = response.usage.prompt_tokens,
            output_tokens = response.usage.completion_tokens,
        )

    def supports_streaming(self) -> bool:
        return True