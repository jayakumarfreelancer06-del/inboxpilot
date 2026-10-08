from __future__ import annotations

from functools import lru_cache

from langchain_core.callbacks import BaseCallbackHandler
from langchain_google_genai import ChatGoogleGenerativeAI

from inboxpilot.config import get_settings


@lru_cache
def chat_model() -> ChatGoogleGenerativeAI:
    s = get_settings()
    kwargs = {}
    if s.llm_thinking_budget is not None and s.llm_model.startswith("gemini-2.5"):
        kwargs["thinking_budget"] = s.llm_thinking_budget
    # max_retries=0: retries are owned by the LangGraph node RetryPolicy (exponential backoff + jitter)
    return ChatGoogleGenerativeAI(model=s.llm_model, google_api_key=s.gemini_api_key, temperature=0, max_retries=0, **kwargs)


def tracing_callbacks() -> list[BaseCallbackHandler]:
    """Langfuse tracing when LANGFUSE_PUBLIC_KEY/SECRET_KEY are set; otherwise nothing."""
    if not get_settings().langfuse_public_key:
        return []
    from langfuse.langchain import CallbackHandler

    return [CallbackHandler()]
