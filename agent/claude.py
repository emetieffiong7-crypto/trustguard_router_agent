from typing import Optional
import anthropic

from agent.base import (
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    ToolDefinition,
    ToolCall
)
from config import settings


class AnthropicProvider(BaseLLMProvider):
    """
    Anthropic Claude provider.
    Converts TrustGuard's generic tool format to Anthropic's tool_use format.
    """

    def __init__(self, model: Optional[str] = None):
        self.model  = model or settings.default_llm_model
        self.client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key
        )

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """Convert generic ToolDefinition list to Anthropic tool format."""
        return [
            {
                "name":         t.name,
                "description":  t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]

    def _convert_messages(self, messages: list[LLMMessage]) -> list[dict]:
        """Convert generic LLMMessage list to Anthropic message format."""
        converted = []
        for msg in messages:
            if msg.role == "tool":
                # Tool result — Anthropic expects this as a user message
                # with tool_result content block
                converted.append({
                    "role": "user",
                    "content": [
                        {
                            "type":        "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content":     msg.content,
                        }
                    ]
                })
            elif msg.tool_calls:
                # Assistant message with tool calls
                content_blocks = []
                if msg.content:
                    content_blocks.append({
                        "type": "text",
                        "text": msg.content
                    })
                for tc in msg.tool_calls:
                    content_blocks.append({
                        "type":  "tool_use",
                        "id":    tc.id,
                        "name":  tc.name,
                        "input": tc.parameters,
                    })
                converted.append({
                    "role":    "assistant",
                    "content": content_blocks
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

        kwargs = {
            "model":      self.model,
            "max_tokens": max_tokens,
            "messages":   self._convert_messages(messages),
        }

        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        if system:
            kwargs["system"] = system

        response = await self.client.messages.create(**kwargs)

        # Extract text content
        text_content = ""
        tool_calls   = []

        for block in response.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id         = block.id,
                    name       = block.name,
                    parameters = block.input,
                ))

        stop_reason_map = {
            "end_turn":    "end_turn",
            "tool_use":    "tool_use",
            "max_tokens":  "max_tokens",
            "stop_sequence": "end_turn",
        }

        return LLMResponse(
            content       = text_content,
            tool_calls    = tool_calls,
            stop_reason   = stop_reason_map.get(response.stop_reason, "end_turn"),
            model         = response.model,
            input_tokens  = response.usage.input_tokens,
            output_tokens = response.usage.output_tokens,
        )

    def supports_streaming(self) -> bool:
        return True