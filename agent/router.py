from typing import Optional
from agent.base import BaseLLMProvider


def get_llm_provider(model: Optional[str] = None) -> BaseLLMProvider:
    """
    Route a model string to the correct provider instance.

    Supported model prefixes:
        claude-*                    → Anthropic
        gpt-*, o1-*, o3-*          → OpenAI
        llama-*, llama3-*, mixtral-*,
        gemma*, whisper-*           → Groq

    Falls back to Groq with the default model if model is None
    or if the prefix is unrecognised.
    """
    from config import settings

    model = model or settings.default_llm_model

    if model.startswith("claude"):
        from agent.claude import AnthropicProvider
        return AnthropicProvider(model=model)

    if model.startswith(("gpt-", "o1-", "o3-")):
        from agent.openai import OpenAIProvider
        return OpenAIProvider(model=model)

    if model.startswith((
        "llama", "mixtral", "gemma", "whisper",
        "deepseek", "qwen"
    )):
        from agent.groq import GroqProvider
        return GroqProvider(model=model)

    # Unknown model — default to Groq
    from agent.groq import GroqProvider
    return GroqProvider(model=settings.default_llm_model)