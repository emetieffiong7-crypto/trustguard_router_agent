from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional
from dataclasses import dataclass, field


@dataclass
class ToolDefinition:
    """
    Provider-agnostic tool definition.
    Each LLM provider converts this to its own format.
    """
    name:        str
    description: str
    parameters:  dict  # JSON Schema object describing the parameters


@dataclass
class ToolCall:
    """A tool call decision returned by the LLM."""
    id:         str
    name:       str
    parameters: dict


@dataclass
class LLMMessage:
    """A single message in the conversation."""
    role:    str   # "user", "assistant", "tool"
    content: str
    tool_calls:   list[ToolCall]   = field(default_factory=list)
    tool_call_id: Optional[str]    = None


@dataclass
class LLMResponse:
    """Response from the LLM after one inference call."""
    content:    str
    tool_calls: list[ToolCall]
    stop_reason: str   # "end_turn", "tool_use", "max_tokens"
    model:      str
    input_tokens:  int
    output_tokens: int


class BaseLLMProvider(ABC):
    """
    Abstract base class for LLM providers.
    All providers implement this interface so the agent loop
    never needs to know which provider it is talking to.
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        tools:    list[ToolDefinition],
        system:   Optional[str] = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        """
        Send messages to the LLM and return its response.
        If the LLM wants to call tools, they are returned in
        LLMResponse.tool_calls for the agent loop to execute.
        """
        pass

    @abstractmethod
    def supports_streaming(self) -> bool:
        pass