"""
LLM client for Layer 6 — provider-agnostic (presentation only).

Defines a small ``LLMClient`` Protocol so the report generator is decoupled from
any provider (and testable without API calls). The configured provider is
**Google Gemini** (``GeminiLLMClient``); ``AnthropicLLMClient`` is kept as a
drop-in alternative — both implement the same ``complete()`` contract.

Provider SDKs + API keys are imported/read lazily, so this module imports without
them and tests can inject a fake.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"   # current stable flash model (verified available)
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
# Low temperature: this is faithful rephrasing of validated findings, not creative writing.
DEFAULT_TEMPERATURE = 0.3


class LLMError(RuntimeError):
    """A Layer-6 LLM call failed."""


class LLMRefusalError(LLMError):
    """The model declined / blocked the request (safety stop)."""


@runtime_checkable
class LLMClient(Protocol):
    """Minimal text-completion contract the report generator depends on."""

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        """Return the model's text response for one (system, user) prompt."""
        ...


def _load_env() -> None:
    """Best-effort: load CBC/.env so the API key env vars are available (no override)."""
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[4] / ".env", override=False)
    except Exception:  # python-dotenv missing / .env absent → rely on process env
        pass


def _enable_truststore() -> None:
    """Best-effort: route TLS through the OS trust store (corporate-proxy SSL)."""
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # truststore missing / already injected — ignore
        pass


# ── Google Gemini (configured provider) ──────────────────────────────────────
class GeminiLLMClient:
    """
    Production ``LLMClient`` backed by the Google Gemini API (``google-genai`` SDK).

    The API key is read from ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``) in the
    environment / ``CBC/.env`` — never passed in code.

    Parameters
    ----------
    model : str
        Gemini model id (default ``gemini-2.5-flash``; e.g. ``gemini-2.5-pro`` is
        also valid).
    temperature : float
        Sampling temperature (default 0.3 — low, for faithful presentation).
    thinking_budget : Optional[int]
        Thinking-token budget. Default ``0`` disables thinking (right for this
        presentation task on Flash — full output, lower cost). Set to ``None`` to
        leave the model default (required if you switch to a model that does not
        support disabling thinking, e.g. ``gemini-2.5-pro``).
    """

    def __init__(
        self,
        model: str = DEFAULT_GEMINI_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        thinking_budget: Optional[int] = 0,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.thinking_budget = thinking_budget
        self.logger = logger or logging.getLogger(__name__)
        self._client: Any = None  # google.genai.Client once created
        self._types: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:  # pragma: no cover - env-specific
                raise LLMError(
                    "The 'google-genai' package is not installed. Run: pip install google-genai"
                ) from exc
            _load_env()
            _enable_truststore()
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise LLMError("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set in the environment/.env.")
            self._client = genai.Client(api_key=api_key)
            self._types = types
        return self._client

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        """Generate one completion (async) and return its text; raise on block/empty."""
        client = self._ensure_client()
        types = self._types
        cfg_kwargs: dict[str, Any] = {
            "system_instruction": system,
            "max_output_tokens": max_tokens,
            "temperature": self.temperature,
        }
        if self.thinking_budget is not None:
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=self.thinking_budget)
        config = types.GenerateContentConfig(**cfg_kwargs)
        try:
            response = await client.aio.models.generate_content(
                model=self.model, contents=user, config=config,
            )
        except Exception as exc:  # network / API errors
            raise LLMError(f"Gemini request failed: {exc}") from exc

        # Prompt-level safety block
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            raise LLMRefusalError(f"Gemini blocked the prompt (block_reason={feedback.block_reason}).")

        try:
            text = (response.text or "").strip()
        except Exception:  # response.text can raise when a candidate has no text (blocked/truncated)
            text = ""
        if not text:
            candidates = getattr(response, "candidates", None) or []
            finish = getattr(candidates[0], "finish_reason", None) if candidates else None
            if str(finish).upper().endswith("SAFETY"):
                raise LLMRefusalError(f"Gemini response blocked (finish_reason={finish}).")
            raise LLMError(f"Gemini returned no text (finish_reason={finish}).")
        return text


# ── Anthropic Claude (alternative provider) ──────────────────────────────────
class AnthropicLLMClient:
    """
    Alternative ``LLMClient`` backed by the Anthropic API (``anthropic`` SDK).

    Kept as a drop-in alternative to ``GeminiLLMClient`` (the Protocol makes the
    provider swappable). Reads ``ANTHROPIC_API_KEY`` from the environment / .env.
    """

    def __init__(
        self,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        adaptive_thinking: bool = True,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.model = model
        self.adaptive_thinking = adaptive_thinking
        self.logger = logger or logging.getLogger(__name__)
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - env-specific
                raise LLMError(
                    "The 'anthropic' package is not installed. Run: pip install anthropic"
                ) from exc
            _load_env()
            self._client = anthropic.AsyncAnthropic()  # reads ANTHROPIC_API_KEY from env
        return self._client

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if self.adaptive_thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        try:
            async with client.messages.stream(**kwargs) as stream:
                message = await stream.get_final_message()
        except Exception as exc:
            raise LLMError(f"Anthropic request failed: {exc}") from exc

        if getattr(message, "stop_reason", None) == "refusal":
            raise LLMRefusalError("Model refused the request (stop_reason=refusal).")
        if getattr(message, "stop_reason", None) == "max_tokens":
            self.logger.warning("Report truncated at max_tokens=%d", max_tokens)

        text = "".join(
            b.text for b in message.content if getattr(b, "type", None) == "text"
        ).strip()
        if not text:
            raise LLMError("Model returned no text content.")
        return text
